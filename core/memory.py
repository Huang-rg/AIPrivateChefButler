"""PostgreSQL 持久化记忆 —— 让管家记住你们聊过什么。

================================================================================
【这个文件干什么】
    给 Agent 装上「长期记忆」：同一个会话（thread_id）里的对话会被存进
    PostgreSQL，关掉浏览器、重启程序后再打开，管家还记得你之前说了什么、
    推荐过哪些菜。

【两种后端，一个开关】
    AI_CHEF_MEMORY_BACKEND 环境变量：
        postgres  （默认）把整段对话存进 PostgreSQL，关掉浏览器再打开还能接着聊
        auto      自动挑：能连上 Postgres 就用 Postgres，否则退回内存
        sqlite    用本地 SQLite 文件（需要额外装 langgraph-checkpoint-sqlite）
        memory    只用进程内内存（重启即失忆，适合临时演示与自测）

    ⚠️ 默认用 postgres：数据库没启动时会**明确报错**并告诉你怎么排查，
    而不是悄悄退回内存让你误以为"记忆生效了"。想临时演示就设 memory。

【为什么需要 setup()】
    PostgresSaver 第一次使用前要建表（checkpoints / checkpoint_writes 等）。
    本模块会自动调用一次 setup()，幂等，重复调用无副作用。
    如果数据库账号没有建表权限，会给出中文提示而不是抛一堆 SQL 报错。

【红线】
    DB_URI 从环境变量读取（由 core/llm_models.py 统一 load_dotenv）。
    本文件**只输出脱敏后的连接摘要**（隐藏账号密码），任何日志、报错都不会
    出现完整连接串。
================================================================================
"""

from __future__ import annotations

import logging
import os
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

logger = logging.getLogger(__name__)

__all__ = [
    "MEMORY_BACKENDS",
    "MemoryConfigError",
    "MemoryHandle",
    "MemoryUnavailable",
    "check_postgres",
    "close_memory",
    "describe_target",
    "diagnose_unavailable",
    "get_checkpointer",
    "get_memory",
    "get_memory_or_fallback",
    "memory_available",
    "redact_uri",
    "reset_memory_singleton",
    "resolve_backend",
]

#: 支持的后端
MEMORY_BACKENDS: Final[tuple[str, ...]] = ("auto", "postgres", "sqlite", "memory")

#: .env 里数据库连接串的变量名（全项目只在这里写一次）
_ENV_DB_URI: Final[str] = "DB_URI"

#: 默认的后端选择。
#: 用户明确选择了「把整段对话存进 PostgreSQL，关掉浏览器再打开还能接着聊」，
#: 所以默认就是 postgres（连不上会明确报错提示启动数据库，而不是悄悄退回内存）。
#: 想临时用内存跑演示：设 AI_CHEF_MEMORY_BACKEND=memory
DEFAULT_BACKEND: Final[str] = "postgres"

#: SQLite 文件默认位置（放在项目内的 .data 目录，方便查看与删除）
DEFAULT_SQLITE_PATH: Final[Path] = Path(__file__).resolve().parents[1] / ".data" / "chef_memory.sqlite"


class MemoryConfigError(RuntimeError):
    """记忆配置有问题（例如 DB_URI 缺失、后端名写错）。"""


class MemoryUnavailable(RuntimeError):
    """记忆后端不可用（例如数据库没启动）。消息为中文，可直接展示。"""


# --------------------------------------------------------------------------- #
# 连接串脱敏
# --------------------------------------------------------------------------- #


def redact_uri(uri: str | None) -> str:
    """把数据库连接串脱敏成「协议://***:***@主机:端口/库名」。

    只保留定位问题所需的信息，账号密码与查询参数一律隐去。
    这是本项目唯一允许查看 DB_URI 的方式——绝不输出原文。

    ⚠️ 安全约定：当连接串格式不合法、无法安全解析时，**宁可什么都不说，
    也绝不回显原文**——否则一次误传就可能把密码写进日志。
    """
    if not uri:
        return "未配置"
    try:
        parsed = urllib.parse.urlparse(uri)
    except ValueError:
        return "已配置（格式无法解析，已隐藏）"

    scheme = parsed.scheme or ""
    host = parsed.hostname or ""
    # 解析不出协议或主机，说明这不是一个正常的连接串，直接隐藏全部内容
    if not scheme or not host:
        return "已配置（格式无法解析，已隐藏）"

    port = f":{parsed.port}" if parsed.port else ""
    database = (parsed.path or "").lstrip("/") or "?"
    user_hint = "***:***@" if parsed.username else ""
    return f"{scheme}://{user_hint}{host}{port}/{database}"


def _env_uri() -> str | None:
    """读取 DB_URI 环境变量。

    ⚠️ 这是个**裸读取**：它不会去加载 .env，只反映"此刻进程环境里有没有"。
    需要保证 .env 已加载的场景（几乎所有场景）请用 :func:`resolve_dsn`。
    踩过的坑：``_build_postgres`` 曾经直接调它，于是"进程刚起来、.env 还没加载"
    的那一瞬间会读到 None，记忆就被误判为不可用并降级成内存后端——
    表现出来就是"历史记录打不开 / 侧边栏空的"。
    """
    value = (os.getenv(_ENV_DB_URI) or "").strip()
    return value or None


def resolve_dsn() -> str | None:
    """★ 全项目**唯一**的数据库连接串解析入口。

    任何需要连数据库的模块（会话历史、健康检查、脚本）都必须走这里，
    不要自己读 ``os.getenv("DB_URI")``——否则配置就有两个来源，
    测试注入不进去，将来也容易出现两边不一致。

    会在读取前确保 .env 已加载（统一走 core.llm_models.reload_env）。
    """
    try:
        from core.llm_models import reload_env

        reload_env()
    except Exception as exc:  # noqa: BLE001 - 加载失败不应阻断调用方
        logger.debug("加载 .env 失败（将尝试直接读环境变量）：%s", exc)
    return _env_uri()


def _backend() -> str:
    raw = (os.getenv("AI_CHEF_MEMORY_BACKEND") or DEFAULT_BACKEND).strip().lower()
    if raw not in MEMORY_BACKENDS:
        logger.warning("AI_CHEF_MEMORY_BACKEND=%r 不是合法值，改用 %s。可选：%s", raw, DEFAULT_BACKEND, MEMORY_BACKENDS)
        return DEFAULT_BACKEND
    return raw


def describe_target() -> str:
    """返回当前记忆目标的脱敏描述，供界面与日志展示。"""
    backend = _backend()
    if backend == "memory":
        return "进程内内存（重启即失忆）"
    if backend == "sqlite":
        return f"SQLite 文件（{DEFAULT_SQLITE_PATH.name}）"
    uri = _env_uri()
    if backend == "postgres":
        return f"PostgreSQL（{redact_uri(uri)}）"
    return f"自动选择：PostgreSQL（{redact_uri(uri)}）优先，失败退回内存"


# --------------------------------------------------------------------------- #
# PostgreSQL 连通性预检
# --------------------------------------------------------------------------- #


def check_postgres(*, timeout: float = 5.0, dsn: str | None = None) -> tuple[bool, str]:
    """检查 PostgreSQL 是否连得上（不抛出异常，只返回结论）。

    Args:
        timeout: 连接超时秒数。
        dsn: 指定连接串；None 表示用 :func:`resolve_dsn`（全项目唯一的解析入口）。

    Returns:
        ``(是否可用, 中文说明)``。说明里只含脱敏后的连接摘要。
    """
    uri = dsn or resolve_dsn()
    if not uri:
        return False, "未配置 DB_URI（无法使用 PostgreSQL 记忆）。"

    try:
        import psycopg
    except ImportError:  # pragma: no cover - 依赖已装，防御性分支
        return False, "未安装 psycopg，无法连接 PostgreSQL。"

    try:
        with psycopg.connect(uri, connect_timeout=int(timeout)) as conn:
            with conn.cursor() as cursor:
                cursor.execute("select version()")
                row = cursor.fetchone()
        version = (row[0] if row else "").split(",")[0]
        return True, f"连接成功：{version}（{redact_uri(uri)}）"
    except Exception as exc:  # noqa: BLE001 - 连不上是预期内的常见情况
        hint = "常见原因：Docker Desktop 未启动、数据库容器未运行、或连接串里的主机端口不对。"
        return False, f"连接失败：{type(exc).__name__}: {str(exc)[:150]}。{hint}"


# --------------------------------------------------------------------------- #
# 记忆句柄
# --------------------------------------------------------------------------- #


@dataclass
class MemoryHandle:
    """一个已就绪的记忆后端。

    Attributes:
        checkpointer: 交给 ``create_agent(checkpointer=...)`` 的对象。
        backend: 实际生效的后端名（postgres / sqlite / memory）。
        target: 脱敏后的目标描述，可直接展示在界面上。
        notes: 初始化过程中的提示（例如「Postgres 不可用，已退回内存」）。
    """

    checkpointer: Any
    backend: str
    target: str
    notes: list[str] = field(default_factory=list)
    _context: Any = field(default=None, repr=False)  # 保持 from_conn_string 的上下文不被回收

    @property
    def persistent(self) -> bool:
        """是否具备跨进程持久化能力（内存后端为 False）。"""
        return self.backend in ("postgres", "sqlite")

    def close(self) -> None:
        """释放资源（关闭数据库连接池）。"""
        if self._context is not None:
            try:
                self._context.__exit__(None, None, None)
            except Exception as exc:  # noqa: BLE001
                logger.debug("关闭记忆后端时出错（可忽略）：%s", exc)
            finally:
                self._context = None


# --------------------------------------------------------------------------- #
# 各后端构造
# --------------------------------------------------------------------------- #


def _build_memory() -> MemoryHandle:
    from langgraph.checkpoint.memory import InMemorySaver

    return MemoryHandle(
        checkpointer=InMemorySaver(),
        backend="memory",
        target="进程内内存（重启即失忆）",
        notes=["当前使用内存记忆：程序重启后对话历史会丢失。"],
    )


def _build_postgres() -> MemoryHandle:
    """构造 PostgreSQL 记忆后端；连不上则抛出 MemoryUnavailable。

    ⚠️ 这里必须用 :func:`resolve_dsn`（它会先确保 .env 已加载），
    不能直接读环境变量——否则进程刚启动、.env 还没加载时会误判为"未配置"，
    把记忆降级成内存后端，用户就会看到"历史记录打不开"。
    """
    uri = resolve_dsn()  # ★ 唯一入口，顺带保证 .env 已加载
    if not uri:
        raise MemoryConfigError(
            "未配置 DB_URI，无法使用 PostgreSQL 记忆。"
            "请检查 .env（本程序不会创建或修改该文件）。"
        )

    ok, detail = check_postgres(dsn=uri)
    if not ok:
        raise MemoryUnavailable(f"PostgreSQL 不可用：{detail}")

    from langgraph.checkpoint.postgres import PostgresSaver

    try:
        context = PostgresSaver.from_conn_string(uri)
        checkpointer = context.__enter__()  # 保持上下文打开，供整个进程使用
    except Exception as exc:  # noqa: BLE001
        raise MemoryUnavailable(
            f"创建 PostgreSQL 记忆失败：{type(exc).__name__}: {str(exc)[:150]}。"
            f"连接摘要：{redact_uri(uri)}"
        ) from exc

    notes: list[str] = []
    try:
        checkpointer.setup()  # 建表，幂等
    except Exception as exc:  # noqa: BLE001
        # 建表失败往往是没有 DDL 权限；给出明确指引而不是让用户看 SQL 报错
        notes.append(
            f"⚠️ 记忆表初始化失败（{type(exc).__name__}: {str(exc)[:120]}）。"
            "如果数据库里还没有 langgraph 的检查点表，请先用有建表权限的账号执行一次 setup()。"
        )
        logger.warning("PostgresSaver.setup() 失败：%s", exc)

    handle = MemoryHandle(
        checkpointer=checkpointer,
        backend="postgres",
        target=f"PostgreSQL（{redact_uri(uri)}）",
        notes=notes,
    )
    handle._context = context
    return handle


def _build_sqlite() -> MemoryHandle:
    """构造 SQLite 记忆后端（需要额外依赖，缺失时给出安装指引）。"""
    try:
        from langgraph.checkpoint.sqlite import SqliteSaver
    except ImportError as exc:
        raise MemoryConfigError(
            "使用 SQLite 记忆需要额外安装：pip install langgraph-checkpoint-sqlite"
        ) from exc

    DEFAULT_SQLITE_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        context = SqliteSaver.from_conn_string(str(DEFAULT_SQLITE_PATH))
        checkpointer = context.__enter__()
    except Exception as exc:  # noqa: BLE001
        raise MemoryUnavailable(f"创建 SQLite 记忆失败：{type(exc).__name__}: {str(exc)[:150]}") from exc

    notes: list[str] = []
    try:
        checkpointer.setup()
    except Exception as exc:  # noqa: BLE001
        logger.debug("SQLite setup() 跳过：%s", exc)

    handle = MemoryHandle(
        checkpointer=checkpointer,
        backend="sqlite",
        target=f"SQLite 文件（{DEFAULT_SQLITE_PATH}）",
        notes=notes,
    )
    handle._context = context
    return handle


# --------------------------------------------------------------------------- #
# 对外入口（单例）
# --------------------------------------------------------------------------- #

_MEMORY: MemoryHandle | None = None


def get_memory(*, force_new: bool = False) -> MemoryHandle:
    """获取记忆后端（单例）。

    按 ``AI_CHEF_MEMORY_BACKEND`` 选择后端：
        auto 模式下，Postgres 连不上会**自动退回内存**并记录提示，绝不因此崩溃。

    Args:
        force_new: True 表示丢弃当前后端重新创建（例如刚启动了数据库）。

    Raises:
        MemoryConfigError: 配置错误（例如 backend=postgres 但没配 DB_URI）。
        MemoryUnavailable: backend=postgres/sqlite 明确指定但后端不可用。
    """
    global _MEMORY
    if _MEMORY is not None and not force_new:
        return _MEMORY

    if force_new and _MEMORY is not None:
        _MEMORY.close()
        _MEMORY = None

    backend = _backend()

    if backend == "memory":
        handle = _build_memory()
    elif backend == "postgres":
        handle = _build_postgres()
    elif backend == "sqlite":
        handle = _build_sqlite()
    else:  # auto
        handle = None
        try:
            handle = _build_postgres()
        except (MemoryUnavailable, MemoryConfigError) as exc:
            logger.info("PostgreSQL 记忆不可用，自动退回内存记忆：%s", exc)
            handle = _build_memory()
            handle.notes.insert(0, f"PostgreSQL 不可用，已退回内存记忆（原因：{exc}）")

    logger.info("记忆后端已就绪：%s", handle.target)
    _MEMORY = handle
    return _MEMORY


def get_checkpointer() -> Any:
    """直接拿 checkpointer，交给 ``ChefAgent(checkpointer=...)`` 使用。"""
    return get_memory().checkpointer


def memory_available() -> bool:
    """记忆功能是否可用（不会抛异常，适合界面初始化时探测）。"""
    try:
        return get_memory().checkpointer is not None
    except Exception as exc:  # noqa: BLE001
        logger.warning("记忆后端不可用：%s", exc)
        return False


def resolve_backend() -> str:
    """返回当前配置的记忆后端名（不建立连接，因而不会失败）。

    界面用它来判断"本来该用数据库但我连不上"，从而给出准确提示，
    而不是误报成「还没有历史对话」。
    """
    return _backend()


def diagnose_unavailable() -> str:
    """给界面用：用大白话说明记忆/历史为什么不可用，以及该怎么办。

    区分两种情况，避免给出误导性结论：
        - 配置的就是内存后端 → 本来就不持久化
        - 配置的是数据库但连不上 → 数据库没启动，需要排查

    注意：连接串一律通过 :func:`resolve_dsn` 获取（唯一入口），
    不要自己读环境变量，否则会与实际连接用的地址不一致、给出错误结论。
    """
    backend = _backend()
    if backend == "memory":
        return "当前设置为内存记忆（AI_CHEF_MEMORY_BACKEND=memory）：关掉页面后历史不会保留。"

    uri = resolve_dsn()
    if not uri:
        return "未配置 DB_URI，无法使用数据库记忆。请检查 .env 是否完整。"

    ok, detail = check_postgres(dsn=uri)
    if ok:
        return f"数据库连接正常（{detail}），但会话表可能尚未建立，请稍后重试。"
    return (
        f"数据库连不上，因此历史记录暂不可用：{detail}"
        "解决办法：启动 Docker Desktop 并确认 PostgreSQL 容器在运行，然后点左侧「重连模型」重试。"
    )


def get_memory_or_fallback(*, force_new: bool = False) -> MemoryHandle:
    """取记忆后端；**连不上数据库时退回内存**并在提示里说明原因。

    与 :func:`get_memory` 的区别：
        - ``get_memory``：默认后端是 postgres，连不上就抛错（适合脚本/测试，
          让你立刻知道数据库没启动）
        - 本函数：给**界面**用——宁可没有长期记忆也要让页面能用，
          同时把「为什么没有记忆」写进 notes 展示给用户

    ⚠️ 关键设计：**降级结果不会被缓存**。
    踩过的坑：曾经把降级句柄也缓存起来，于是"服务启动那一瞬间 .env 还没加载好"
    导致的降级会被永久记住，之后数据库恢复了也一直用内存后端——
    表现出来就是"历史记录打不开"。现在失败不缓存，下次调用会重新尝试连接。
    """
    global _MEMORY
    try:
        return get_memory(force_new=force_new)
    except (MemoryUnavailable, MemoryConfigError) as exc:
        logger.warning("记忆后端不可用，界面退回内存记忆（下次调用会重试）：%s", exc)
        handle = _build_memory()
        handle.notes.insert(
            0,
            f"⚠️ 长期记忆未启用（{exc}）——本轮对话仍可正常进行，"
            "但关掉页面后历史不会保留。数据库恢复后会自动重连。",
        )
        # 故意不写进 _MEMORY：让下一次调用重新尝试连数据库
        _MEMORY = None
        return handle


def close_memory() -> None:
    """关闭记忆后端（应用退出时调用）。"""
    global _MEMORY
    if _MEMORY is not None:
        _MEMORY.close()
        _MEMORY = None


def reset_memory_singleton() -> None:
    """仅清空单例引用，不关闭底层连接（测试用）。"""
    global _MEMORY
    _MEMORY = None

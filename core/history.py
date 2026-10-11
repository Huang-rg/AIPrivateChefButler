"""对话历史与会话管理 —— 用 PostgreSQL 存聊天记录，支持侧边栏的会话列表。

================================================================================
【这个文件干什么】
    给界面提供「聊天记录侧边栏」需要的一切：会话列表、新建、重命名、删除，
    以及点开某个会话时把完整对话恢复出来。

【设计决定：为什么不把聊天内容再存一份】
    对话内容本身已经由 ``langgraph-checkpoint-postgres`` 存进 Postgres 的
    ``checkpoints`` / ``checkpoint_blobs`` 表里了（第五步接入的记忆）。这是框架
    托管的数据，结构复杂且属于框架私有格式。

    所以本模块的做法是：
        - 聊天内容  → 复用框架的 checkpointer（``checkpointer.get_state(...)``）
                      **不另存一份**，避免数据重复、不一致
        - 会话元信息 → 自建一张极小的表 ``chef_sessions``
                      只放「标题、创建时间、最后更新时间、是否置顶、是否删除」
                      （标题是用户可改的，框架表里没有这个概念）

    这样既满足「Postgres 做技术支持」，又不会把框架的私有表结构耦合进业务代码。

【优雅降级】
    Postgres 连不上时，本模块不会抛异常给界面，而是：
        - ``available()`` 返回 False
        - ``list_sessions()`` 返回空列表
    界面据此显示「会话列表暂不可用」，而不是整页崩掉。
================================================================================
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

logger = logging.getLogger(__name__)

__all__ = [
    "SESSION_TABLE",
    "SessionInfo",
    "SessionStore",
    "extract_text_from_message",
    "is_meaningful_title",
    "make_title",
]

#: 会话元信息表名
SESSION_TABLE = "chef_sessions"

#: 标题最大长度
_MAX_TITLE_LEN = 30


@dataclass
class SessionInfo:
    """一个会话（对话线程）的摘要信息。"""

    thread_id: str
    title: str = ""
    created_at: datetime | None = None
    updated_at: datetime | None = None
    message_count: int = 0
    pinned: bool = False

    @property
    def display_title(self) -> str:
        """界面上显示的标题（空标题给个兜底）。"""
        return self.title.strip() or "新对话"

    @property
    def updated_text(self) -> str:
        """「最后更新」的可读文本。"""
        if self.updated_at is None:
            return ""
        delta = datetime.now(timezone.utc) - self.updated_at.astimezone(timezone.utc)
        seconds = int(delta.total_seconds())
        if seconds < 60:
            return "刚刚"
        if seconds < 3600:
            return f"{seconds // 60} 分钟前"
        if seconds < 86400:
            return f"{seconds // 3600} 小时前"
        if seconds < 86400 * 7:
            return f"{seconds // 86400} 天前"
        return self.updated_at.strftime("%m-%d")


def is_meaningful_title(text: str, *, min_length: int = 2) -> bool:
    """判断一段文本够不够格当会话标题。

    踩过的坑：用户测试时随手发了个「1」「2」，会话标题就真的成了「1」「2」，
    侧边栏里完全看不出那是哪次对话，看起来就像"历史记录坏了"。

    拒绝的标准：
        - 太短（少于 min_length 个字符）
        - 纯数字 / 纯标点 / 纯 emoji（没有任何语义）
        - 常见的无意义短词（你好、在吗、测试…）
    """
    cleaned = re.sub(r"\s+", "", text or "")
    if len(cleaned) < min_length:
        return False
    # 纯数字（含全角数字）或纯标点符号
    if re.fullmatch(r"[0-9０-９]+", cleaned):
        return False
    if not re.search(r"[\u4e00-\u9fffA-Za-z]", cleaned):
        return False
    # 无意义的短词
    if cleaned in {"你好", "您好", "在吗", "在么", "测试", "test", "hi", "hello", "喂", "哈喽"}:
        return False
    return True


def make_title(text: str, *, fallback: str = "新对话") -> str:
    """从用户的第一句话生成会话标题。

    Args:
        text: 用户输入。
        fallback: 内容无意义时的兜底标题。

    Returns:
        清理并截断后的标题（最长 30 个字符）；内容无意义时返回 ``fallback``。
    """
    cleaned = re.sub(r"\s+", " ", (text or "").strip())
    cleaned = re.sub(r"^(?:请|帮我|麻烦|你好[，,]?|你好啊)\s*", "", cleaned)
    if not is_meaningful_title(cleaned):
        return fallback
    if len(cleaned) <= _MAX_TITLE_LEN:
        return cleaned
    return cleaned[: _MAX_TITLE_LEN - 1] + "…"


def extract_text_from_message(message: Any) -> str:
    """把一条 LangChain 消息抽成纯文本（跳过图片内容块）。

    图片内容块体积极大（一张图 Base64 上百万字符），绝不能进标题或列表。
    """
    content = getattr(message, "content", message)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
        return "".join(parts)
    return str(content)


class SessionStore:
    """会话元信息的存取（PostgreSQL）。

    用法::

        store = SessionStore()          # 自动从环境变量读 DB_URI
        if store.available():
            for info in store.list_sessions():
                print(info.display_title, info.updated_text)
    """

    def __init__(self, dsn: str | None = None, *, checkpointer: Any | None = None, auto_setup: bool = True) -> None:
        """
        Args:
            dsn: 数据库连接串；None 表示从环境变量 DB_URI 读取。
            checkpointer: 用于恢复会话消息的检查点对象；None 时只做元信息管理。
            auto_setup: 是否自动建表（幂等）。
        """
        self._dsn = dsn
        self._checkpointer = checkpointer
        self._ready = False
        self._error: str | None = None
        if auto_setup:
            self._ensure_ready()

    # ------------------------------ 连接管理 ------------------------------ #

    def _resolve_dsn(self) -> str | None:
        """取数据库连接串。

        统一委托给 :func:`core.memory.resolve_dsn` —— 全项目**只有一个地方**
        解析 DB_URI。之前这里自己读 ``os.getenv("DB_URI")``，导致配置有两个来源：
        测试注入不进去，将来也容易出现两边不一致。
        """
        if self._dsn:
            return self._dsn
        from core.memory import resolve_dsn

        return resolve_dsn()

    def _connect(self):
        """建立一次数据库连接（调用方负责关闭）。"""
        import psycopg

        dsn = self._resolve_dsn()
        if not dsn:
            raise RuntimeError("未配置 DB_URI")
        return psycopg.connect(dsn, connect_timeout=5)

    def _ensure_ready(self) -> None:
        """检查连接并建表（幂等）。失败只记录，不抛给界面。"""
        try:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        f"""
                        CREATE TABLE IF NOT EXISTS {SESSION_TABLE} (
                            thread_id   TEXT PRIMARY KEY,
                            title       TEXT NOT NULL DEFAULT '',
                            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
                            updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
                            message_count INTEGER NOT NULL DEFAULT 0,
                            pinned      BOOLEAN NOT NULL DEFAULT FALSE,
                            deleted     BOOLEAN NOT NULL DEFAULT FALSE
                        )
                        """
                    )
                    cur.execute(
                        f"CREATE INDEX IF NOT EXISTS idx_{SESSION_TABLE}_updated "
                        f"ON {SESSION_TABLE} (updated_at DESC)"
                    )
                conn.commit()
            self._ready = True
            self._error = None
        except Exception as exc:  # noqa: BLE001 - 连不上是预期情况，界面要降级而不是崩
            self._ready = False
            self._error = f"{type(exc).__name__}: {str(exc)[:140]}"
            logger.warning("会话历史不可用（%s）。界面将隐藏会话列表。", self._error)

    def available(self) -> bool:
        """会话历史是否可用。"""
        return self._ready

    @property
    def error(self) -> str | None:
        """不可用时的原因（中文可展示）。"""
        return self._error

    # ------------------------------ 元信息 CRUD ------------------------------ #

    def upsert_session(
        self,
        thread_id: str,
        *,
        title: str | None = None,
        message_count: int | None = None,
    ) -> bool:
        """创建或更新一个会话记录。

        Args:
            thread_id: 会话 ID。
            title: 新标题；None 表示不改动已有标题（新建时用''）。
            message_count: 当前累计消息数；None 表示不改。

        Returns:
            是否写入成功。
        """
        if not self._ready:
            return False
        try:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    # ⚠️ 注意 %s::text 显式转型：不加的话，当 title 为 None 时
                    #    PostgreSQL 无法推断参数类型，会报 IndeterminateDatatype
                    #    （could not determine data type of parameter）。
                    cur.execute(
                        f"""
                        INSERT INTO {SESSION_TABLE} (thread_id, title, message_count, updated_at, deleted)
                        VALUES (%s, COALESCE(%s::text, ''), COALESCE(%s::int, 0), now(), FALSE)
                        ON CONFLICT (thread_id) DO UPDATE SET
                            title = CASE WHEN %s::text IS NULL THEN {SESSION_TABLE}.title ELSE %s::text END,
                            message_count = COALESCE(%s::int, {SESSION_TABLE}.message_count),
                            updated_at = now(),
                            deleted = FALSE
                        """,
                        (thread_id, title, message_count, title, title, message_count),
                    )
                conn.commit()
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("写入会话记录失败：%s", exc)
            return False

    def list_sessions(self, *, limit: int = 50) -> list[SessionInfo]:
        """列出未删除的会话，按最后更新时间倒序。

        Args:
            limit: 最多返回几条。

        Returns:
            会话摘要列表；数据库不可用时返回空列表。
        """
        if not self._ready:
            return []
        try:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        f"""
                        SELECT thread_id, title, created_at, updated_at, message_count, pinned
                        FROM {SESSION_TABLE}
                        WHERE deleted = FALSE
                        ORDER BY pinned DESC, updated_at DESC
                        LIMIT %s
                        """,
                        (max(1, limit),),
                    )
                    rows = cur.fetchall()
            return [
                SessionInfo(
                    thread_id=row[0],
                    title=row[1] or "",
                    created_at=row[2],
                    updated_at=row[3],
                    message_count=row[4] or 0,
                    pinned=bool(row[5]),
                )
                for row in rows
            ]
        except Exception as exc:  # noqa: BLE001
            logger.warning("读取会话列表失败：%s", exc)
            return []

    def rename_session(self, thread_id: str, title: str) -> bool:
        """重命名会话。"""
        if not self._ready:
            return False
        new_title = (title or "").strip()[:80]
        try:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        f"UPDATE {SESSION_TABLE} SET title = %s, updated_at = now() WHERE thread_id = %s",
                        (new_title, thread_id),
                    )
                    changed = cur.rowcount
                conn.commit()
            return changed > 0
        except Exception as exc:  # noqa: BLE001
            logger.warning("重命名会话失败：%s", exc)
            return False

    def delete_session(self, thread_id: str) -> bool:
        """删除会话（软删除：标记 deleted，保留数据以便排查）。

        注意：这里**不删除** langgraph 的检查点数据。删除框架托管的数据风险高
        （表结构属于框架私有），而且用户随时可能想找回。界面上它会从列表消失。
        """
        if not self._ready:
            return False
        try:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        f"UPDATE {SESSION_TABLE} SET deleted = TRUE, updated_at = now() WHERE thread_id = %s",
                        (thread_id,),
                    )
                    changed = cur.rowcount
                conn.commit()
            return changed > 0
        except Exception as exc:  # noqa: BLE001
            logger.warning("删除会话失败：%s", exc)
            return False

    def get_title(self, thread_id: str) -> str | None:
        """读取某个会话的标题（没有记录返回 None）。"""
        if not self._ready:
            return None
        try:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(f"SELECT title FROM {SESSION_TABLE} WHERE thread_id = %s", (thread_id,))
                    row = cur.fetchone()
            return row[0] if row else None
        except Exception as exc:  # noqa: BLE001
            logger.warning("读取会话标题失败：%s", exc)
            return None

    # --------------------------- 从 checkpointer 恢复对话 --------------------------- #

    def load_messages(self, thread_id: str, *, limit: int = 200) -> list[dict[str, Any]]:
        """从检查点恢复某个会话的历史消息，供界面渲染。

        ⚠️ API 说明（踩过坑）：检查点对象**没有** ``get_state()`` 方法
        （那是编译后图的接口，不是 saver 的）。正确读法是
        ``checkpointer.get_tuple(config).checkpoint["channel_values"]["messages"]``，
        它返回**最新**一个检查点的状态，也就是当前完整对话。

        Args:
            thread_id: 会话 ID。
            limit: 最多返回多少条（从最新的往回取）。

        Returns:
            ``[{"role": "user"/"assistant", "content": "..."}]``；
            没有 checkpointer 或读取失败时返回空列表。
        """
        if self._checkpointer is None:
            return []

        try:
            checkpoint_tuple = self._checkpointer.get_tuple({"configurable": {"thread_id": thread_id}})
        except Exception as exc:  # noqa: BLE001
            logger.warning("从检查点读取会话 %s 失败：%s", thread_id, exc)
            return []

        if checkpoint_tuple is None:
            return []

        try:
            checkpoint = getattr(checkpoint_tuple, "checkpoint", None) or {}
            channel_values = checkpoint.get("channel_values", {}) if isinstance(checkpoint, dict) else {}
            messages = channel_values.get("messages") or []
        except Exception as exc:  # noqa: BLE001
            logger.warning("解析检查点内容失败（会话 %s）：%s", thread_id, exc)
            return []

        from langchain_core.messages import AIMessage, HumanMessage

        restored: list[dict[str, Any]] = []
        for message in messages:
            if isinstance(message, HumanMessage):
                text = extract_text_from_message(message)
                # 纯图片消息（文本为空）在界面历史里跳过，避免一排空气泡
                if text.strip():
                    restored.append({"role": "user", "content": text})
            elif isinstance(message, AIMessage):
                text = extract_text_from_message(message)
                if text.strip():
                    restored.append({"role": "assistant", "content": text})

        return restored[-limit:] if limit and len(restored) > limit else restored

    def list_thread_ids_in_checkpoints(self) -> list[str]:
        """列出 checkpointer 里存在的所有 thread_id（用于识别"空壳会话"）。"""
        if not self._ready:
            return []
        try:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT DISTINCT thread_id FROM checkpoints ORDER BY thread_id")
                    return [row[0] for row in cur.fetchall() if row[0]]
        except Exception as exc:  # noqa: BLE001 - 表不存在也算正常（还没产生过对话）
            logger.debug("读取 checkpoints 表的 thread_id 失败：%s", exc)
            return []

    def purge_orphan_sessions(self, *, apply: bool = True) -> list[SessionInfo]:
        """清理「空壳会话」：元信息表里有，但检查点里没有对话内容。

        为什么会存在这种会话（真实踩过的坑）：
            早期版本里，记忆后端可能因为"启动瞬间 .env 还没加载好"而降级成内存，
            但会话元信息仍然写进了 PostgreSQL。结果是——列表里看得到这个会话，
            点进去却一个字都没有，用户会以为"历史记录坏了"。

        这些条目没有任何可恢复的内容（对话本身就在内存里，进程一结束就没了），
        所以直接标记为删除，让列表干净。

        Args:
            apply: True 真正写入数据库；False 只返回将要清理的列表（预览）。

        Returns:
            被清理（或将被清理）的会话列表。
        """
        if not self._ready:
            return []

        live_threads = set(self.list_thread_ids_in_checkpoints())
        orphans: list[SessionInfo] = []
        for info in self.list_sessions(limit=500):
            if info.thread_id in live_threads:
                continue
            # 元信息说有几条消息，但检查点里没有 → 空壳
            if info.message_count > 0:
                orphans.append(info)

        if apply:
            for info in orphans:
                self.delete_session(info.thread_id)
                logger.info("已清理空壳会话：%s（标题 %r）", info.thread_id, info.title)
        return orphans

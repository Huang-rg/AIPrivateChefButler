"""★★★ 大模型初始化中心 ★★★ —— 想换模型，只改这一个文件。

================================================================================
【这个文件为什么存在】
    项目里所有大模型都在这里创建，其它文件一律不许直接 init_chat_model。
    你要换模型时，只改下面「模型清单区」里的常量，或者改环境变量，全项目生效。

【两种换模型的方式】

    方式一（改代码，最直观）：修改下面的 TEXT_MODEL_CANDIDATES / VISION_MODEL_CANDIDATES
        TEXT_MODEL_CANDIDATES   = ("qwen-plus",)                    # 负责文字交流
        VISION_MODEL_CANDIDATES = ("qwen-vl-max", "qwen-vl-plus")    # 负责看图识食材

    方式二（改环境变量，不用动代码）：在系统环境变量里设置
        AI_CHEF_TEXT_MODEL      = qwen-plus          文本模型
        AI_CHEF_VISION_MODEL    = qwen-vl-max        视觉模型
        AI_CHEF_LLM_TEMPERATURE = 0.3                温度
        AI_CHEF_LLM_TIMEOUT     = 90                 单次请求超时（秒）
        AI_CHEF_LLM_MAX_RETRIES = 2                  失败重试次数

    ⚠️ 环境变量优先级高于代码里的常量，方便你不改代码临时切模型。

【为什么要区分「文本模型」和「视觉模型」】
    两者职责不同：
        - 文本模型：聊天、理解用户意图、给食谱排序、写创意建议（量大、要快）
        - 视觉模型：看冰箱照片、识别食材（必须支持多模态）
    分开配置后，你可以用便宜的模型干文本活、用视觉模型只干识图活，省钱又快。

【自动降级（fallback）】
    VISION_MODEL_CANDIDATES 是一个元组，程序会依次尝试：第一个可用就用第一个。
    例如 ("qwen3.7-flash-2026-07-15", "qwen-vl-plus")：如果第一个模型报
    「不支持视觉」之类错误，会自动换第二个，并打印一条提示，界面不会崩。

【红线】
    本文件不写死任何 Key / 连接串。所有密钥都来自**项目根目录的 `.env`**，
    由 python-dotenv 解析后读环境变量。首次使用请参考 `.env.example` 新建 `.env`。
    任何日志、报错、界面展示都不会输出密钥内容（只输出长度或域名）。

================================================================================
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Final

logger = logging.getLogger(__name__)

__all__ = [
    "CHANNEL_LABELS",
    "DEFAULT_TEMPERATURE",
    "DEFAULT_TIMEOUT",
    "ENV_PATH",
    "LLMModels",
    "ModelConfigError",
    "ModelInitError",
    "ModelRole",
    "ModelStructuredOutputError",
    "build_structuring_model",
    "get_llm_models",
    "get_text_model",
    "get_vision_model",
    "list_model_options",
    "mask_secret",
    "reload_env",
    "set_model_override",
]


# ==============================================================================
# 一、环境变量文件位置
# ==============================================================================

#: .env 文件路径：**项目根目录下的 .env**（相对本项目定位，克隆下来即可用）。
#:
#: 为什么用相对路径：别人 clone 这个仓库后，.env 就在项目根目录，
#: 如果写死某个本机绝对路径，别人根本跑不起来。
#: 定位方式：以本文件（core/llm_models.py）为基准，往上找一级就是项目根目录。
PROJECT_ROOT: Final[Path] = Path(__file__).resolve().parents[1]
ENV_PATH: Final[Path] = PROJECT_ROOT / ".env"

#: .env 里本模块需要的变量名
_ENV_BASE_URL: Final[str] = "DASHSCOPE_BASE_URL"
_ENV_API_KEY: Final[str] = "DASHSCOPE_API_KEY"
_ENV_DEEPSEEK_KEY: Final[str] = "DEEPSEEK_API_KEY"


# ==============================================================================
# 二、★★★ 模型清单区：已经搬到 core/model_settings.py ★★★
# ==============================================================================
#
# 为什么搬走：模型名单以前放在这里，界面要用就得自己再抄一份，代码里换了模型
# 界面就不同步。现在名单只有一份（core/model_settings.py 的 MODEL_CATALOG），
# 本文件与界面下拉框都读它，天然同步。
#
#   👉 想换模型：改 core/model_settings.py 的 MODEL_CATALOG
#   👉 想临时换：设环境变量 AI_CHEF_TEXT_MODEL / AI_CHEF_VISION_MODEL
#   👉 想验证模型能不能用：先跑 tests/_probe_models.py
#
# 下面这两个常量保留为「候选列表」的对外接口（按优先级排序），
# 内容由 model_settings 动态生成，因此永远与名单保持一致。

from core import model_settings as _settings  # noqa: E402


def _text_candidates() -> tuple[str, ...]:
    """文本模型候选（按优先级排序）。"""
    default = _settings.default_text_model()
    ordered = [default] + [s.name for s in _settings.list_models(role="text") if s.name != default]
    return tuple(ordered)


def _vision_candidates() -> tuple[str, ...]:
    """视觉模型候选（只含实测能看图的模型，按优先级排序）。"""
    default = _settings.default_vision_model()
    ordered = [default] + [s.name for s in _settings.list_models(role="vision") if s.name != default]
    return tuple(ordered)


#: 默认生成参数
DEFAULT_TEMPERATURE: Final[float] = 0.3
DEFAULT_TIMEOUT: Final[float] = 90.0
DEFAULT_MAX_RETRIES: Final[int] = 2


# ==============================================================================
# 三、异常
# ==============================================================================


class ModelConfigError(RuntimeError):
    """配置有问题（缺 Key、base_url 为空等）。消息是中文，可直接展示给用户。"""


class ModelInitError(RuntimeError):
    """模型创建失败（所有候选模型都不可用）。"""


class ModelStructuredOutputError(RuntimeError):
    """模型不支持结构化输出。消息为中文，可直接展示。"""


class ModelRole(str, Enum):
    """模型角色。"""

    TEXT = "text"
    VISION = "vision"


# ==============================================================================
# 四、工具函数
# ==============================================================================


def mask_secret(secret: str | None, keep: int = 4) -> str:
    """把密钥脱敏成「长度 + 首尾各 n 位」，用于日志与界面展示。

    >>> mask_secret("sk-abcdef123456")
    '已配置（长度 15，sk-a…3456）'
    """
    if not secret:
        return "未配置"
    if len(secret) <= keep * 2:
        return f"已配置（长度 {len(secret)}）"
    return f"已配置（长度 {len(secret)}，{secret[:keep]}…{secret[-keep:]}）"


def _env_first(*names: str) -> str | None:
    """按顺序取第一个非空环境变量。"""
    for name in names:
        value = os.getenv(name)
        if value and value.strip():
            return value.strip()
    return None


def _env_float(name: str, default: float) -> float:
    raw = _env_first(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        logger.warning("环境变量 %s=%r 不是数字，改用默认值 %s。", name, raw, default)
        return default


def _env_int(name: str, default: int) -> int:
    raw = _env_first(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning("环境变量 %s=%r 不是整数，改用默认值 %s。", name, raw, default)
        return default


def _extract_text(response: Any) -> str:
    """把模型回复统一抽成纯文本（兼容 str / list[dict] 等多种返回结构）。"""
    content = getattr(response, "content", response)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                parts.append(str(item.get("text", "")))
            else:
                parts.append(str(item))
        return "".join(parts)
    return str(content)


def _payload_from_bytes(data: bytes):
    """把图片字节转成 ImagePayload（延迟导入，避免核心模块硬依赖 utils）。"""
    from utils.image_handler import prepare_from_bytes

    return prepare_from_bytes(data, origin="<llm-ping>")


def reload_env(*, override: bool = True) -> bool:
    """加载 .env 到进程环境变量。

    其它模块不要自己调用 load_dotenv，统一走这里，保证「只加载一份配置」。

    查找顺序：
        1. 项目根目录的 `.env`（即 clone 下来后你自己新建的那个）
        2. 若上一步找不到，退回 `load_dotenv()` 的默认行为——
           从当前工作目录逐级向上查找 `.env`（方便在子目录里跑脚本）

    Args:
        override: True 表示 .env 的值覆盖已有环境变量（默认，保证配置以 .env 为准）。

    Returns:
        是否成功读到文件（文件不存在返回 False，不抛异常）。
    """
    from dotenv import load_dotenv

    if ENV_PATH.exists():
        load_dotenv(dotenv_path=str(ENV_PATH), override=override)
        logger.debug("已加载 .env：%s", ENV_PATH)
        return True

    # 兜底：让 python-dotenv 自己向上查找（例如从 tests/ 目录里运行时）
    if load_dotenv(override=override):
        logger.debug("已在上级目录找到并加载 .env")
        return True

    logger.warning(
        "找不到 .env 文件（已查找项目根目录 %s 及其上级目录）。"
        "请参考 .env.example 在项目根目录新建一个 .env 并填入 API Key。",
        ENV_PATH,
    )
    return False


# ==============================================================================
# 五、配置对象
# ==============================================================================


@dataclass
class _ConnectionConfig:
    """模型连接配置（两条通道共用一份生成参数）。

    ⚠️ 安全提示：dataclass 的默认 repr 会把 ``api_key`` 明文打印出来，
    而 repr 会出现在异常回溯和日志里。所以：
        - 需要展示配置时，一律用 :meth:`safe_repr`（已脱敏）
        - 常规日志里不要直接 print 这个对象
    """

    base_url: str
    api_key: str
    temperature: float
    timeout: float
    max_retries: int
    deepseek_api_key: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def to_kwargs(self, model_name: str, *, spec: Any | None = None) -> dict[str, Any]:
        """转换成 ``init_chat_model`` 的参数，**按通道自动选择**。

        Args:
            model_name: 模型名。
            spec: :class:`core.model_settings.ModelSpec`；为 None 时按名字推断通道。

        Returns:
            可直接展开传给 ``init_chat_model`` 的参数字典。

        Raises:
            ModelConfigError: 该通道所需的 Key / base_url 缺失。
        """
        channel = self._resolve_channel(model_name, spec)

        # 两条通道共有的生成参数
        common: dict[str, Any] = {
            "model": model_name,
            "temperature": self.temperature,
            "timeout": self.timeout,
            "max_retries": self.max_retries,
        }

        if channel == "deepseek":
            # DeepSeek 官方：LangChain 原生支持，**不能传 base_url**
            if not self.deepseek_api_key:
                raise ModelConfigError(
                    "模型 "
                    f"{model_name} 走的是 DeepSeek 官方通道，需要环境变量 DEEPSEEK_API_KEY，"
                    "但当前没有读到。请检查 .env（本程序不会创建或修改该文件）。"
                )
            return {**common, "model_provider": "deepseek", "api_key": self.deepseek_api_key}

        # 阿里云：用 OpenAI 协议「瞒天过海」
        return {
            **common,
            "model_provider": "openai",
            "base_url": self.base_url,
            "api_key": self.api_key,
        }

    @staticmethod
    def _resolve_channel(model_name: str, spec: Any | None) -> str:
        """判断模型走哪条通道：优先看清单登记，其次按模型名推断。"""
        if spec is not None:
            return str(spec.channel)
        from core import model_settings as settings

        found = settings.spec_for(model_name)
        if found is not None:
            return found.channel
        # 未登记的模型：deepseek-* 归官方通道，其余默认阿里云
        return "deepseek" if model_name.lower().startswith("deepseek-") else "aliyun"

    def safe_repr(self) -> str:
        """脱敏后的配置摘要，可安全写入日志或展示在界面上。"""
        return (
            "_ConnectionConfig("
            f"base_url={self.base_url!r}, "
            f"api_key={mask_secret(self.api_key)}, "
            f"deepseek_api_key={mask_secret(self.deepseek_api_key)}, "
            f"temperature={self.temperature}, "
            f"timeout={self.timeout}, "
            f"max_retries={self.max_retries})"
        )


def _load_connection_config() -> _ConnectionConfig:
    """从环境变量读取连接配置。

    两条通道的必需项不同：
        阿里云通道  ：DASHSCOPE_BASE_URL + DASHSCOPE_API_KEY（缺一不可）
        DeepSeek 官方：DEEPSEEK_API_KEY（**不需要 base_url**）

    所以这里只强制要求阿里云那套；DeepSeek Key 缺失时，只有真正用到
    DeepSeek 官方模型才会报错（见 _ConnectionConfig.to_kwargs）。
    """
    reload_env()
    base_url = _env_first(_ENV_BASE_URL)
    api_key = _env_first(_ENV_API_KEY)

    missing = [name for name, value in ((_ENV_BASE_URL, base_url), (_ENV_API_KEY, api_key)) if not value]
    if missing:
        raise ModelConfigError(
            f"缺少环境变量：{'、'.join(missing)}。请检查 {ENV_PATH} 是否配置完整"
            "（本程序不会创建或修改该文件，也不会读取其中内容的明文）。"
        )

    return _ConnectionConfig(
        base_url=base_url or "",
        api_key=api_key or "",
        temperature=_env_float("AI_CHEF_LLM_TEMPERATURE", DEFAULT_TEMPERATURE),
        timeout=_env_float("AI_CHEF_LLM_TIMEOUT", DEFAULT_TIMEOUT),
        max_retries=_env_int("AI_CHEF_LLM_MAX_RETRIES", DEFAULT_MAX_RETRIES),
        deepseek_api_key=_env_first(_ENV_DEEPSEEK_KEY) or "",
    )


#: 视觉自检用的小图边长（多数视觉模型要求边长 >= 10px，太小会被服务端拒绝，
#: 从而把「模型没问题」误判成「不支持视觉」）
VISION_PING_SIZE: Final[int] = 64
#: 视觉自检图的颜色，以及期望模型答出的关键词
VISION_PING_COLOR: Final[tuple[int, int, int]] = (40, 90, 220)
VISION_PING_EXPECT: Final[tuple[str, ...]] = ("蓝", "blue", "Blue", "BLUE")
#: 视觉自检提示词
VISION_PING_PROMPT: Final[str] = "这张图整体是什么颜色？只回答一个颜色词。"
#: 文本自检提示词
TEXT_PING_PROMPT: Final[str] = "回复两个字：可用"


#: 测试注入点：仅用于自动化测试伪造候选列表（例如塞一个不存在的模型来验证降级）。
#: 生产代码不会写它，界面切换模型走 core.model_settings.set_override。
_CANDIDATE_OVERRIDES: dict[str, tuple[str, ...]] = {}


def _resolve_candidates(role: ModelRole) -> tuple[str, ...]:
    """确定某个角色的候选模型列表。

    候选顺序 = 「当前选中的模型」在最前，其余同角色模型按清单顺序跟在后面，
    于是第一个不可用时能自动降级到下一个。
    """
    if role.value in _CANDIDATE_OVERRIDES:  # 测试注入
        return _CANDIDATE_OVERRIDES[role.value]
    return _text_candidates() if role is ModelRole.TEXT else _vision_candidates()


# ==============================================================================
# 六、模型集合
# ==============================================================================


def _validate_config(config: _ConnectionConfig) -> None:
    """校验阿里云通道的配置完整性（DeepSeek 官方 Key 缺失不在这里报错）。"""
    missing: list[str] = []
    if not config.base_url:
        missing.append(_ENV_BASE_URL)
    if not config.api_key:
        missing.append(_ENV_API_KEY)
    if missing:
        raise ModelConfigError(
            f"缺少环境变量：{'、'.join(missing)}。请检查 {ENV_PATH} 是否配置完整"
            "（本程序不会创建或修改该文件，也不会读取其中内容的明文）。"
        )


class LLMModels:
    """统一管理文本模型与视觉模型（都带自动降级）。

    用法::

        from core.llm_models import get_llm_models

        models = get_llm_models()
        models.vision_model.invoke([message])   # 看图识食材
        models.text_model.invoke("你好")         # 文字交流

    也可以直接用便捷函数::

        from core.llm_models import get_text_model, get_vision_model
    """

    def __init__(self, config: _ConnectionConfig | None = None) -> None:
        # 外部传入的配置也要校验：不能因为是「注入的」就跳过检查，
        # 否则会带着空 Key 一路走到模型创建，报出难以定位的错误。
        if config is not None:
            _validate_config(config)
        self._config = config
        self._text_model: Any | None = None
        self._vision_model: Any | None = None
        self._text_model_name: str | None = None
        self._vision_model_name: str | None = None
        self._init_notes: list[str] = []

    # ------------------------------ 连接配置 ------------------------------ #

    @property
    def config(self) -> _ConnectionConfig:
        if self._config is None:
            self._config = _load_connection_config()
        return self._config

    # ------------------------------ 文本模型 ------------------------------ #

    @property
    def text_model(self) -> Any:
        """负责文字交流的模型（懒加载，第一次用到才创建）。"""
        if self._text_model is None:
            self._text_model, self._text_model_name = self._build_with_fallback(ModelRole.TEXT)
        return self._text_model

    @property
    def text_model_name(self) -> str:
        """实际生效的文本模型名（可能因降级而不同于候选列表第一个）。"""
        _ = self.text_model
        return self._text_model_name or ""

    # ------------------------------ 视觉模型 ------------------------------ #

    @property
    def vision_model(self) -> Any:
        """负责看图识食材的模型（懒加载，第一次用到才创建）。"""
        if self._vision_model is None:
            self._vision_model, self._vision_model_name = self._build_with_fallback(ModelRole.VISION)
        return self._vision_model

    @property
    def vision_model_name(self) -> str:
        """实际生效的视觉模型名。"""
        _ = self.vision_model
        return self._vision_model_name or ""

    # ------------------------------- 构建逻辑 ------------------------------ #

    def _should_ping(self, role: ModelRole) -> bool:
        """是否对该角色做真实探测。

        为什么要探测：``init_chat_model`` **创建时不会校验模型是否存在**，
        连 `不存在的模型名` 也能创建成功。所以「创建没报错」不等于「模型能用」。
        只有真发一次请求才知道。

        默认策略：
            - 视觉模型：**默认探测**（风险最高，模型不支持多模态是最常见的坑）
            - 文本模型：默认不探测（省时间省钱），设 AI_CHEF_MODEL_PING=1 可开启
            - 统一开关：AI_CHEF_MODEL_PING=0 可全部关闭
        """
        switch = _env_first("AI_CHEF_MODEL_PING")
        if switch is not None and switch.lower() in ("0", "false", "no", "off"):
            return False
        if switch is not None and switch.lower() in ("1", "true", "yes", "on"):
            return True
        return role is ModelRole.VISION

    def ping(self, role: ModelRole, model: Any) -> tuple[bool, str]:
        """真实探测一个模型能否干活。

        Args:
            role: 模型角色。
            model: 已创建好的模型实例。

        Returns:
            ``(是否可用, 说明)``。视觉模型必须**真的答对颜色**才算通过，
            仅仅「没报错」不算——否则等于没测出多模态能力。
        """
        from langchain_core.messages import HumanMessage

        if role is ModelRole.TEXT:
            try:
                response = model.invoke(TEXT_PING_PROMPT)
            except Exception as exc:  # noqa: BLE001
                return False, f"{type(exc).__name__}: {str(exc)[:150]}"
            text = _extract_text(response).strip()
            return bool(text), text[:40]

        # 视觉：必须用尺寸合规的图片，否则会被服务端以「尺寸不合规」拒绝
        try:
            from utils.image_handler import build_human_message
            from utils.img_codec import make_solid_png

            png = make_solid_png(VISION_PING_SIZE, VISION_PING_SIZE, VISION_PING_COLOR)
            payload = _payload_from_bytes(png)
            message = build_human_message(VISION_PING_PROMPT, payload)
        except Exception as exc:  # noqa: BLE001
            return False, f"构造视觉自检图片失败：{type(exc).__name__}: {str(exc)[:120]}"

        try:
            response = model.invoke([message])
        except Exception as exc:  # noqa: BLE001
            return False, f"{type(exc).__name__}: {str(exc)[:150]}"

        text = _extract_text(response).strip()
        saw_expected = any(word in text for word in VISION_PING_EXPECT)
        if saw_expected:
            return True, f"认出颜色：{text[:20]}"
        return False, f"未答出预期颜色（回答：{text[:40] or '空'}）"

    def _build_with_fallback(self, role: ModelRole) -> tuple[Any, str]:
        """按候选列表依次尝试，**真实探测可用后**才采用；全部失败抛 ModelInitError。

        流程：创建 → 探测 → 通过就采用；不通过就换下一个候选。
        （候选里可能混着两条通道的模型，通道差异由 build_model 处理。）
        """
        candidates = _resolve_candidates(role)
        do_ping = self._should_ping(role)
        errors: list[tuple[str, str]] = []
        first_created: Any | None = None
        first_name: str | None = None

        role_label = "文本" if role is ModelRole.TEXT else "视觉"

        for index, model_name in enumerate(candidates):
            try:
                model = self.build_model(model_name)
            except Exception as exc:  # noqa: BLE001 - 换下一个候选继续试
                reason = f"创建失败 {type(exc).__name__}: {exc}"
                errors.append((model_name, reason))
                logger.warning("模型 %s（%s）创建失败：%s", model_name, role_label, reason)
                continue

            if first_created is None:
                first_created, first_name = model, model_name

            if not do_ping:
                logger.info("已就绪：%s 模型 = %s（未探测，AI_CHEF_MODEL_PING 已关闭）", role_label, model_name)
                return model, model_name

            ok, note = self.ping(role, model)
            if ok:
                if index > 0:
                    message = (
                        f"{role_label}模型 {candidates[0]} 不可用，已自动降级为 {model_name}。"
                        f"降级原因：{errors[0][1] if errors else '探测未通过'}"
                    )
                    self._init_notes.append(message)
                    logger.warning(message)
                logger.info("已就绪：%s 模型 = %s（探测通过：%s）", role_label, model_name, note)
                return model, model_name

            reason = f"探测未通过：{note}"
            errors.append((model_name, reason))
            logger.warning("模型 %s（%s）%s，尝试下一个候选。", model_name, role_label, reason)

        # 全部候选都没通过：如果探测本身可能是误伤（例如网络抖动），
        # 就把第一个能创建出来的模型交给调用方，并留下醒目提示，而不是直接崩。
        if first_created is not None:
            message = (
                f"⚠️ 所有{role_label}模型自检均未通过，已退回使用 {first_name}，"
                f"请检查网络或模型权限。失败明细：{'；'.join(f'{n} → {r}' for n, r in errors)}"
            )
            self._init_notes.append(message)
            logger.error(message)
            return first_created, first_name or ""

        detail = "；".join(f"{name} → {reason}" for name, reason in errors)
        raise ModelInitError(
            f"所有候选{role_label}模型都创建失败。"
            f"候选列表：{list(candidates)}。逐个报错：{detail}。"
            "提示：视觉模型必须支持多模态，可改用 qwen-vl-plus 或 qwen-vl-max。"
        )

    # ------------------------------- 状态汇报 ------------------------------ #

    @property
    def init_notes(self) -> list[str]:
        """初始化过程中产生的提示（例如发生了降级），可展示在界面上。"""
        return list(self._init_notes)

    def capabilities(self) -> dict[str, Any]:
        """返回「可安全展示」的配置摘要（不含任何密钥明文）。"""
        return {
            "文本模型": self.text_model_name,
            "视觉模型": self.vision_model_name,
            "端点域名": self.config.base_url.split("//")[-1].split("/")[0],
            "API Key": mask_secret(self.config.api_key),
            "DeepSeek Key": mask_secret(os.getenv(_ENV_DEEPSEEK_KEY)),
            "温度": self.config.temperature,
            "超时(秒)": self.config.timeout,
            "重试次数": self.config.max_retries,
            "配置摘要": self.config.safe_repr(),
            "降级提示": self.init_notes,
        }

    # ------------------------------ 自检入口 ------------------------------ #

    def self_check(self) -> dict[str, Any]:
        """对当前生效的文本/视觉模型各做一次真实探测，返回可展示的自检报告。

        Streamlit 侧边栏可以放一个「测试模型连接」按钮调用它。
        """
        report: dict[str, Any] = {}
        for role in (ModelRole.TEXT, ModelRole.VISION):
            label = "文本模型" if role is ModelRole.TEXT else "视觉模型"
            model = self.text_model if role is ModelRole.TEXT else self.vision_model
            name = self.text_model_name if role is ModelRole.TEXT else self.vision_model_name
            ok, note = self.ping(role, model)
            report[label] = {"模型": name, "可用": ok, "说明": note}
        return report

    # -------------------------- 直接指定模型（临时切换） -------------------------- #

    def build_model(self, model_name: str, **overrides: Any) -> Any:
        """按模型名创建实例，**自动判断走哪条通道**。

        这是全项目唯一的模型构造入口，两条通道的差异都被封装在这里：

            阿里云通道：LangChain 不支持阿里云，但阿里云兼容 OpenAI 协议，所以
                        model_provider="openai" + base_url=DASHSCOPE_BASE_URL
            DeepSeek 官方通道：LangChain 原生支持，所以**只给 api_key，不给 base_url**

        Args:
            model_name: 精确模型名（如 ``"qwen3.8-flash"`` / ``"deepseek-chat"``）。
            **overrides: 覆盖生成参数，例如 ``temperature=0.0``。

        Returns:
            创建好的聊天模型实例。

        Raises:
            ModelConfigError: 该通道需要的环境变量没配齐。
        """
        from langchain.chat_models import init_chat_model

        spec = _settings.spec_for(model_name)
        kwargs = self.config.to_kwargs(model_name, spec=spec)
        kwargs.update(overrides)

        logger.info(
            "创建模型：%s（通道=%s，视觉=%s）",
            model_name,
            spec.channel if spec else "未登记",
            "支持" if (spec and spec.vision) else "不支持/未知",
        )
        return init_chat_model(**kwargs)


# ==============================================================================
# 七、模块级单例与便捷函数（其它模块统一用这三个入口）
# ==============================================================================

_LLM_MODELS: LLMModels | None = None


def get_llm_models(*, force_new: bool = False) -> LLMModels:
    """获取全局模型集合（单例，避免重复创建连接）。

    Args:
        force_new: True 表示强制重建（例如用户改了 .env 想立即生效）。
    """
    global _LLM_MODELS
    if _LLM_MODELS is None or force_new:
        _LLM_MODELS = LLMModels()
    return _LLM_MODELS


def get_text_model() -> Any:
    """直接拿文本模型。"""
    return get_llm_models().text_model


def get_vision_model() -> Any:
    """直接拿视觉模型。"""
    return get_llm_models().vision_model


def list_model_options(role: str = "text") -> list[dict[str, Any]]:
    """列出界面上可选的模型（下拉框数据源）。

    Args:
        role: ``"text"`` 列出所有能聊天的模型；``"vision"`` 只列出实测能看图的模型。

    Returns:
        列表，每项含 ``name / label / vision / channel / note``。
        界面下拉框直接把 ``label`` 当选项文本、``name`` 当取值即可。
    """
    from core import model_settings as settings

    return [
        {
            "name": spec.name,
            "label": spec.display,
            "vision": spec.vision,
            "channel": spec.channel,
            "channel_label": spec.channel_label,
            "note": spec.note,
        }
        for spec in settings.list_models(role=role if role in ("text", "vision") else None)
    ]


def set_model_override(role: str, model_name: str, *, rebuild: bool = True) -> None:
    """切换某个角色使用的模型（界面下拉框调用）。

    Args:
        role: ``"text"`` 或 ``"vision"``。
        model_name: 必须是清单里的模型名。
        rebuild: True 表示同时重建全局模型集合，让切换立即生效。

    Raises:
        KeyError: 模型不在清单里。
        ValueError: 给视觉角色选了不支持图像的模型。
    """
    from core import model_settings as settings

    settings.set_override(role, model_name)
    if rebuild:
        get_llm_models(force_new=True)


def build_structuring_model(schema: Any, *, use_vision_model: bool = False, **overrides: Any) -> Any:
    """创建一个「结构化输出模型」：调用它一定返回符合 schema 的对象，而不是一段文字。

    Args:
        schema: Pydantic 模型类，例如 ``schemas.food_schemas.RecipeRecommendation``。
        use_vision_model: True 表示用视觉模型（需要同时看图时用）；默认用文本模型。
        **overrides: 覆盖生成参数，例如 ``temperature=0.0``。

    Returns:
        一个 ``model.with_structured_output(schema)`` 出来的可调用对象。

    Raises:
        ModelStructuredOutputError: 当前模型/接口不支持结构化输出。
    """
    models = get_llm_models()
    model_name = models.vision_model_name if use_vision_model else models.text_model_name

    # 结构化输出要求确定性高、随机性低，默认把温度压到 0
    overrides.setdefault("temperature", 0.0)
    model = models.build_model(model_name, **overrides)

    try:
        return model.with_structured_output(schema)
    except Exception as exc:  # noqa: BLE001
        raise ModelStructuredOutputError(
            f"模型 {model_name} 不支持结构化输出（{type(exc).__name__}: {exc}）。"
            "可以改用普通输出 + schemas.food_schemas.parse_json_object() 兜底解析。"
        ) from exc

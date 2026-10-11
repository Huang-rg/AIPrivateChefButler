"""★ 模型名单与通道配置 —— 全项目唯一的「有哪些模型可用」定义处。

================================================================================
【这个文件是干什么的】
    定义「有哪些模型可以选、每个模型走哪条通道、支不支持视觉、在界面上叫什么名字」。

    ┌─────────────────────────────────────────────────────────────┐
    │  想加/减/换模型？ 改这个文件的 MODEL_CATALOG 就够了。        │
    │  界面上的下拉框会自动同步，不需要改任何界面代码。            │
    └─────────────────────────────────────────────────────────────┘

【为什么要单独抽一个文件】
    之前模型名单散落在 llm_models.py 里，界面想拿到「可选模型」只能自己再抄一份，
    一旦代码里换了模型，界面就不同步了。抽出来之后：
        - core/llm_models.py 用它来创建模型
        - app.py 的侧边栏下拉框用它来生成选项
    两边读同一份数据，天然同步。

【两条通道（关键区别，务必理解）】
    ① ALIYUN（阿里云百炼 / DashScope）
         LangChain 本身**不**支持阿里云，但阿里云兼容 OpenAI 协议，所以要「瞒天过海」：
             init_chat_model(model=..., model_provider="openai",
                             base_url=DASHSCOPE_BASE_URL, api_key=DASHSCOPE_API_KEY)
         需要 DASHSCOPE_BASE_URL + DASHSCOPE_API_KEY 两个环境变量。

    ② DEEPSEEK（DeepSeek 官方）
         LangChain **原生支持** DeepSeek，所以**只要 api_key、不要 base_url**：
             init_chat_model(model="deepseek-chat", api_key=DEEPSEEK_API_KEY)
         只需要 DEEPSEEK_API_KEY。

    ⚠️ 踩过的坑：把 DeepSeek 官方通道的模型名（deepseek-chat）发到阿里云端点上，
    会得到 404「Model not exist」，从而误判成「这个模型不存在」。实际是发错了端点。

【数据来源：全部实测，不是抄文档】
    下面每个模型的 channels 都是本机真实探测出来的结果（每个模型两次极小请求）：
        - 文本探测：「回复两个字：可用」
        - 视觉探测：发一张 64×64 纯红 PNG，要求答出颜色；答不出「红」就不算支持视觉
    探测时间：2026-10-06。重跑命令见 tests/_probe_models.py。

    ⚠️ 免费额度提醒：阿里云这批是免费额度模型，额度用尽会返回
    403 Free quota exhausted。届时候选列表会自动跳到下一个模型，界面也会给出提示。
    真正换模型时，只需要改本文件的 MODEL_CATALOG。
================================================================================
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Final, Literal

logger = logging.getLogger(__name__)

__all__ = [
    "ALIYUN",
    "CHANNELS",
    "DEEPSEEK",
    "MODEL_CATALOG",
    "ModelSpec",
    "REGISTRY",
    "clear_overrides",
    "default_text_model",
    "default_vision_model",
    "get_override",
    "list_models",
    "set_override",
    "spec_for",
]

#: 通道标识
ALIYUN: Final[str] = "aliyun"
DEEPSEEK: Final[str] = "deepseek"
CHANNELS: Final[tuple[str, ...]] = (ALIYUN, DEEPSEEK)

#: 通道的中文名（用于界面显示与报错）
CHANNEL_LABELS: Final[dict[str, str]] = {
    ALIYUN: "阿里云百炼（OpenAI 兼容协议）",
    DEEPSEEK: "DeepSeek 官方（LangChain 原生）",
}


@dataclass(frozen=True)
class ModelSpec:
    """一个可用模型的规格说明。

    Attributes:
        name: 传给 API 的**精确模型名**，一个字都不能改。
        channel: ``aliyun`` 或 ``deepseek``。
        vision: 是否支持图像输入（实测结论，不是文档声称）。
        label: 界面上显示的名字，方便你一眼认出。
        note: 备注（实测结果、额度状态等）。
    """

    name: str
    channel: str
    vision: bool
    label: str = ""
    note: str = ""

    @property
    def display(self) -> str:
        """界面下拉框里显示的文本。"""
        base = self.label or self.name
        tags = []
        tags.append("👁 可看图" if self.vision else "📝 纯文本")
        if self.note:
            tags.append(self.note)
        return f"{base}　（{'｜'.join(tags)}）"

    @property
    def channel_label(self) -> str:
        """通道的中文名。"""
        return CHANNEL_LABELS.get(self.channel, self.channel)


# ==============================================================================
# ★★★ 模型清单区：换模型改这里 ★★★
# ==============================================================================
#
# 排序即优先级：候选列表按这个顺序尝试，第一个能用的自动生效。
# 建议把「又快又稳」的放前面。

MODEL_CATALOG: Final[tuple[ModelSpec, ...]] = (
    # ---------------- 阿里云通道：支持多模态（实测能看图）----------------
    ModelSpec("qwen3.8-flash", ALIYUN, True, "Qwen3.8-Flash", "实测视觉通过·速度快"),
    ModelSpec("qwen3.8-27b", ALIYUN, True, "Qwen3.8-27B", "实测视觉通过"),
    ModelSpec("qwen3.8-max", ALIYUN, True, "Qwen3.8-Max", "实测视觉通过·旗舰"),
    ModelSpec("qwen3.8-max-0902", ALIYUN, True, "Qwen3.8-Max-0902", "实测视觉通过·旗舰快照"),
    ModelSpec("qwen3.7-flash-2026-07-15", ALIYUN, True, "Qwen3.7-Flash-0715", "用户原用模型·实测视觉通过"),
    ModelSpec("qwen3.7-flash", ALIYUN, True, "Qwen3.7-Flash", "实测视觉通过"),
    ModelSpec("kimi-k3", ALIYUN, True, "Kimi-K3", "实测视觉通过"),
    ModelSpec("deepseek-v4.1-flash", ALIYUN, True, "DeepSeek-V4.1-Flash（阿里云）", "实测视觉通过"),
    # ---------------- 阿里云通道：仅文本（实测看不了图）----------------
    ModelSpec("glm-5.3", ALIYUN, False, "GLM-5.3", "实测：回「没看到图片」"),
    ModelSpec("deepseek-v4-pro-0813", ALIYUN, False, "DeepSeek-V4-Pro-0813（阿里云）", "实测：回「无法判断」·较慢"),
    ModelSpec("qwen3.8-2.4t-a95b", ALIYUN, False, "Qwen3.8-2.4T-A95B", "实测：把红图答成灰色"),
    # ⚠️ deepseek-v4-flash-0731 已从清单移除：实测返回
    #    403 Free quota exhausted（免费额度已耗尽），写了也没法用。
    # ---------------- DeepSeek 官方通道（LangChain 原生，无需 base_url）----------------
    ModelSpec("deepseek-flash", DEEPSEEK, False, "DeepSeek-Flash（官方）", "官方通道·仅文本"),
    ModelSpec("deepseek-chat", DEEPSEEK, False, "DeepSeek-Chat（官方）", "官方通道·仅文本"),
    ModelSpec("deepseek-reasoner", DEEPSEEK, False, "DeepSeek-Reasoner（官方）", "官方通道·推理模型·仅文本"),
)

#: 模型名 -> 规格，供快速查找
REGISTRY: Final[dict[str, ModelSpec]] = {spec.name: spec for spec in MODEL_CATALOG}


# ==============================================================================
# 默认值（可被界面/环境变量覆盖；改这里不生效请检查是否设了环境变量）
# ==============================================================================

#: 默认文本模型：挑一个实测又快又稳的
_DEFAULT_TEXT: Final[str] = "qwen3.8-flash"
#: 默认视觉模型：必须实测能看图
_DEFAULT_VISION: Final[str] = "qwen3.8-flash"


# ==============================================================================
# 运行时覆盖（界面下拉框就是通过它切换模型的）
# ==============================================================================

_OVERRIDES: dict[str, str] = {}


def set_override(role: str, model_name: str) -> None:
    """设置某个角色的模型（``role`` 取 ``"text"`` 或 ``"vision"``）。

    界面下拉框切换模型时调用它。会校验模型是否在清单里且支持该角色。
    """
    spec = REGISTRY.get(model_name)
    if spec is None:
        raise KeyError(f"模型 {model_name!r} 不在 MODEL_CATALOG 里，无法使用。请先在 core/model_settings.py 添加。")
    if role == "vision" and not spec.vision:
        raise ValueError(f"模型 {model_name!r} 实测不支持图像输入，不能作为视觉模型。")
    _OVERRIDES[role] = model_name
    logger.info("模型已切换：%s = %s", role, model_name)


def get_override(role: str) -> str | None:
    """读取某个角色的运行时覆盖值。"""
    return _OVERRIDES.get(role)


def clear_overrides() -> None:
    """清空运行时覆盖，回到默认值。"""
    _OVERRIDES.clear()


def spec_for(model_name: str) -> ModelSpec | None:
    """按模型名查规格；不在清单里返回 None。"""
    return REGISTRY.get(model_name)


# ==============================================================================
# 名单查询（界面下拉框的数据源）
# ==============================================================================


def list_models(*, role: Literal["text", "vision"] | None = None, channel: str | None = None) -> list[ModelSpec]:
    """列出可用模型。

    Args:
        role: ``"text"`` 只返回能聊天的；``"vision"`` 只返回实测能看图的；None 返回全部。
        channel: 只返回指定通道的模型（``aliyun`` / ``deepseek``）。

    Returns:
        模型规格列表（保持 MODEL_CATALOG 的顺序，即优先级顺序）。
    """
    specs = list(MODEL_CATALOG)
    if channel:
        specs = [s for s in specs if s.channel == channel]
    if role == "vision":
        specs = [s for s in specs if s.vision]
    # 所有模型都能文本对话（能看图的必然也能聊天）
    return specs


def _env_override(role: str) -> str | None:
    """环境变量指定（优先级最高，方便不改代码临时切模型）。"""
    env_name = "AI_CHEF_TEXT_MODEL" if role == "text" else "AI_CHEF_VISION_MODEL"
    value = (os.getenv(env_name) or "").strip()
    if value and value in REGISTRY:
        return value
    if value:
        logger.warning("环境变量 %s=%r 不在模型清单里，已忽略。", env_name, value)
    return None


def default_text_model() -> str:
    """当前默认文本模型（环境变量 > 界面选择 > 代码默认）。"""
    return _env_override("text") or _OVERRIDES.get("text") or _DEFAULT_TEXT


def default_vision_model() -> str:
    """当前默认视觉模型（环境变量 > 界面选择 > 代码默认）。"""
    return _env_override("vision") or _OVERRIDES.get("vision") or _DEFAULT_VISION

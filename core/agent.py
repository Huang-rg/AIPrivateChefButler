"""★ 主 Agent —— AI私厨管家的「大脑」，把视觉识别、联网搜索、记忆串成一条链。

================================================================================
【这个文件干什么】
    创建并管理主 Agent：绑定大模型（文本 + 视觉）、Tavily 搜索工具、以及（第五步
    接入的）PostgreSQL 记忆，对外只暴露两三个简单函数。

【核心流程（写进了系统提示词，Agent 会照着走）】
    用户拍照/上传食材图
        ➡️ ① 视觉模型看图，列出识别到的食材（识别不到就直说，绝不编造）
        ➡️ ② 调用 search_recipes 工具联网搜真实菜谱
        ➡️ ③ 按「营养价值」和「制作难度」排序
        ➡️ ④ 用统一格式推荐给用户（菜名/营养/难度/耗时/食材/步骤/来源链接）
        ➡️ ⑤ 若搜不到合适菜谱，改调 search_creative_pairings 给创意搭配建议

【为什么用 create_agent 而不是手写流程】
    手写流程是「死」的：用户问一句"那我再加点土豆呢？"就不知道该怎么办了。
    Agent + 工具的方式让大模型自己决定「要不要搜、搜什么、搜几次」，更灵活。
    实测 create_agent 原生支持 checkpointer 参数，所以第五步接 PostgreSQL 记忆时
    只需要给这里传一个 checkpointer，本文件不用改逻辑。

【记忆（第五步预留）】
    传 checkpointer 即开启多轮记忆：
        from core.memory import get_checkpointer
        chef = ChefAgent(checkpointer=get_checkpointer())
    同一 thread_id 的对话会被自动记住；不传 checkpointer 时也能正常用，只是不记忆。

【怎么改它的"性格"和"输出格式"】
    直接改下面的 SYSTEM_PROMPT 即可，改完重启应用就生效。
================================================================================
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Final, Iterable, Sequence

logger = logging.getLogger(__name__)

__all__ = [
    "CHEF_AGENT_NAME",
    "SYSTEM_PROMPT",
    "ChefAgent",
    "ChefAgentError",
    "get_chef_agent",
    "new_thread_id",
]


CHEF_AGENT_NAME: Final[str] = "ai_private_chef"


# ==============================================================================
# ★ 系统提示词：决定 Agent 的行为与输出格式，想改风格就改这里
# ==============================================================================

SYSTEM_PROMPT: Final[str] = """你是一位专业、亲切的「AI私厨管家」，擅长根据用户手头的食材推荐真正能做的菜。

【你的工作流程】
1. 看图识食材：如果用户上传了冰箱或厨房的照片，先仔细看清图中有哪些食材。
   - 只说你**真正看到**的食材，不要凭空猜测或补充图中没有的东西。
   - 如果照片模糊、看不清或根本没有食材，要直接说明，并请用户换一张图或直接报出食材名。
   - 顺带提一句某个食材的状态（如"西红柿看着还挺新鲜""这半颗白菜要尽快吃"）。
2. 搜索真实菜谱：识别出食材后，**必须调用 search_recipes 工具**联网搜索真实菜谱。
   - 不要凭记忆编造菜谱，菜谱必须来自搜索结果的真实来源。
   - 搜索时把主要食材用顿号连接作为输入，例如「西红柿、鸡蛋、小葱」。
   - 如果第一次搜索结果不理想，可以换个说法再搜一次（例如带上"家常""快手"）。
3. 排序推荐：把搜到的菜谱按下面两个维度排序后再呈现。
   - 营养价值：优先推荐蛋白质/膳食纤维丰富、油盐适中的做法。
   - 制作难度：优先推荐用户容易上手的（家常快手优先）。
4. 搜不到怎么办：如果 search_recipes 确实找不到合适菜谱，
   **改用 search_creative_pairings 工具**搜索搭配灵感，然后基于现有食材给出创意搭配建议，
   并明确告诉用户"这是创意建议，不是现成菜谱"。
5. 追问与追问：用户如果追加食材（"我还有点土豆"）、换口味（"想吃辣的"）或追问细节，
   就结合已有食材重新搜一次，不要重复推荐已经推荐过的菜。

【输出格式（每次推荐菜谱都按这个来）】
先一句话总结「用你现有的食材，可以做什么」，然后逐条列出菜谱：

### 1. 菜名
- 🥗 **营养评级**：A / B / C（A=营养均衡，B=中等，C=偏油偏咸），并用一句话说明理由
- 🔥 **制作难度**：⭐ 1~5 星（1=新手零失败，5=需要技巧），标注大致耗时（如 20 分钟）
- 🧺 **需要食材**：列出用到的食材，并标出哪些是你已有的、哪些还需要另买
- 📝 **做法要点**：3~5 步讲清楚关键步骤，不要罗列无关细节
- 🔗 **菜谱来源**：[网站名](链接)  ← 必须给出真实链接，来自搜索结果

【语气与边界】
- 用中文回答，语气像一位耐心的厨师朋友，简洁、不说废话。
- 不要输出与做饭无关的寒暄长文；用户问别的（如营养知识）也要认真回答。
- 如果用户的问题与做饭完全无关，礼貌地把话题带回美食。
- 涉及食品安全（如食材是否变质、能不能生吃）时，给出保守、安全的建议。
"""


class ChefAgentError(RuntimeError):
    """Agent 创建或调用失败。消息为中文，可直接展示在界面上。"""


def new_thread_id(prefix: str = "chef") -> str:
    """生成一个新的会话 ID（用于区分不同对话的记忆）。"""
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


# ==============================================================================
# 输入消息构造
# ==============================================================================


def _coerce_messages(messages: "str | dict[str, Any] | Any | Sequence[Any]") -> list[Any]:
    """把各种入参统一成 LangChain 消息列表。

    支持：
        - 字符串：当成用户说的话（HumanMessage）
        - ImagePayload：当成一张用户上传的图片
        - 字典：``{"role": "user"/"assistant", "content": ...}``（兼容 Streamlit 会话格式）
        - LangChain 消息对象：原样使用
        - 以上任意组合构成的列表
    """
    from langchain_core.messages import AIMessage, HumanMessage
    from utils.image_handler import ImagePayload, build_image_content_block

    if isinstance(messages, (str, ImagePayload, dict)) or hasattr(messages, "content"):
        items: list[Any] = [messages]
    elif isinstance(messages, Iterable):
        items = list(messages)
    else:
        items = [messages]

    converted: list[Any] = []
    for item in items:
        if isinstance(item, str):
            converted.append(HumanMessage(content=item))
        elif isinstance(item, ImagePayload):
            converted.append(HumanMessage(content=[build_image_content_block(item)]))
        elif isinstance(item, dict):
            role = str(item.get("role", "user")).lower()
            content = item.get("content", "")
            if not isinstance(content, (str, list)):
                content = str(content)
            converted.append(AIMessage(content=content) if role == "assistant" else HumanMessage(content=content))
        elif hasattr(item, "content"):
            converted.append(item)
        else:
            raise ChefAgentError(
                f"不支持的消息类型：{type(item).__name__}。"
                "请传字符串、ImagePayload、{'role':..., 'content':...} 字典或 LangChain 消息对象。"
            )
    return converted


def _extract_final_text(state: dict[str, Any]) -> str:
    """从 Agent 返回的状态里取出「最后一条 AI 回复」的纯文本。"""
    from langchain_core.messages import AIMessage

    for message in reversed(state.get("messages") or []):
        if not isinstance(message, AIMessage):
            continue
        content = message.content
        if isinstance(content, str) and content.strip():
            return content.strip()
        if isinstance(content, list):
            parts: list[str] = []
            for block in content:
                if isinstance(block, str):
                    parts.append(block)
                elif isinstance(block, dict) and block.get("type") == "text":
                    parts.append(str(block.get("text", "")))
            text = "".join(parts).strip()
            if text:
                return text
    return ""


def _collect_tool_calls(state: dict[str, Any]) -> list[dict[str, Any]]:
    """把 Agent 这一轮调用过的工具整理出来（用于界面展示"正在联网搜索…"）。"""
    from langchain_core.messages import AIMessage, ToolMessage

    calls: list[dict[str, Any]] = []
    messages = state.get("messages") or []
    # 先收集请求，再按 tool_call_id 补上结果条数
    results_by_id: dict[str, Any] = {}
    for message in messages:
        if isinstance(message, ToolMessage):
            results_by_id[message.tool_call_id] = message.content

    for message in messages:
        if not isinstance(message, AIMessage):
            continue
        for call in getattr(message, "tool_calls", None) or []:
            call_id = call.get("id") or ""
            calls.append(
                {
                    "name": call.get("name") or "",
                    "args": call.get("args") or {},
                    "result_preview": str(results_by_id.get(call_id, ""))[:200],
                }
            )
    return calls


# ==============================================================================
# Agent 封装
# ==============================================================================


@dataclass
class ChefAgent:
    """AI私厨管家主 Agent。

    用法::

        chef = ChefAgent()
        result = chef.chat("我有西红柿和鸡蛋，能做什么？")
        print(result["text"])

        # 带图片：
        from utils.image_handler import prepare_image
        img = prepare_image(r"E:\\冰箱.jpg")
        result = chef.chat([img, "看看我冰箱里有什么，能做什么菜？"])
    """

    #: 记忆检查点（第五步注入 PostgreSQL 用）。None = 不记忆，但功能正常。
    checkpointer: Any | None = None
    #: 是否使用视觉模型作为 Agent 大脑（需要看图时必须为 True）
    use_vision_model: bool = True
    #: 系统提示词，默认用模块级 SYSTEM_PROMPT
    system_prompt: str = SYSTEM_PROMPT
    #: 工具列表；None 表示自动从 tools.search_tool 取
    tools: list[Any] | None = None
    #: 生成参数覆盖
    model_kwargs: dict[str, Any] = field(default_factory=dict)

    _agent: Any | None = field(default=None, init=False, repr=False)
    _load_notes: list[str] = field(default_factory=list, init=False, repr=False)

    # ------------------------------ 构建 ------------------------------ #

    @property
    def agent(self) -> Any:
        """懒加载创建 Agent（第一次用到才初始化模型与工具）。"""
        if self._agent is None:
            self._agent = self._build()
        return self._agent

    def _build(self) -> Any:
        from langchain.agents import create_agent

        from core.llm_models import get_llm_models
        from tools.search_tool import get_search_tools, tavily_available

        models = get_llm_models()
        model = models.vision_model if self.use_vision_model else models.text_model
        model_name = models.vision_model_name if self.use_vision_model else models.text_model_name

        tools = self.tools if self.tools is not None else get_search_tools()
        if not tavily_available():
            self._load_notes.append("未配置 TAVILY_API_KEY：本次不联网搜菜谱，只能靠模型已有知识回答。")

        try:
            agent = create_agent(
                model=model,
                tools=tools,
                system_prompt=self.system_prompt,
                checkpointer=self.checkpointer,
                name=CHEF_AGENT_NAME,
            )
        except Exception as exc:  # noqa: BLE001 - 转化为可展示的中文错误
            raise ChefAgentError(
                f"创建 AI私厨管家 Agent 失败：{type(exc).__name__}: {exc}"
            ) from exc

        logger.info(
            "Agent 已就绪：大脑模型=%s，工具=%s，记忆=%s",
            model_name,
            [getattr(t, "name", type(t).__name__) for t in tools] or "无",
            "已开启" if self.checkpointer is not None else "未开启",
        )
        return agent

    # ------------------------------ 属性 ------------------------------ #

    @property
    def tool_names(self) -> list[str]:
        """已绑定的工具名列表（供界面展示）。"""
        tools = self.tools if self.tools is not None else []
        if self.tools is None:
            from tools.search_tool import get_search_tools

            tools = get_search_tools()
        return [getattr(tool, "name", type(tool).__name__) for tool in tools]

    @property
    def load_notes(self) -> list[str]:
        """初始化过程中的提示（例如没有配置 Tavily）。"""
        return list(self._load_notes)

    @property
    def has_memory(self) -> bool:
        """是否已接入持久化记忆。"""
        return self.checkpointer is not None

    # ------------------------------ 调用 ------------------------------ #

    def chat(
        self,
        messages: "str | dict[str, Any] | Any | Sequence[Any]",
        *,
        thread_id: str | None = None,
        recursion_limit: int = 12,
    ) -> dict[str, Any]:
        """跟管家对话一轮。

        Args:
            messages: 用户输入，可以是字符串、图片、消息字典或它们的列表。
            thread_id: 会话 ID。传了它且开启了记忆时，多轮对话会自动带上历史；
                不传时会自动生成一个（本次调用内部使用）。
            recursion_limit: 工具调用最大轮数，防止模型反复搜索停不下来。

        Returns:
            ``{"text": 最终回复, "tool_calls": [...], "messages": [...], "thread_id": ...}``

        Raises:
            ChefAgentError: 模型调用失败（网络、鉴权、超限等）。
        """
        state_messages = _coerce_messages(messages)
        config: dict[str, Any] = {"recursion_limit": max(4, recursion_limit)}
        used_thread = thread_id or new_thread_id()
        if self.checkpointer is not None:
            config["configurable"] = {"thread_id": used_thread}

        try:
            state = self.agent.invoke({"messages": state_messages}, config=config)
        except Exception as exc:  # noqa: BLE001
            raise ChefAgentError(
                f"AI私厨管家调用失败：{type(exc).__name__}: {exc}"
            ) from exc

        if not isinstance(state, dict):
            raise ChefAgentError(f"Agent 返回了意外的类型：{type(state).__name__}。")

        return {
            "text": _extract_final_text(state),
            "tool_calls": _collect_tool_calls(state),
            "messages": list(state.get("messages") or []),
            "thread_id": used_thread,
        }

    def stream_chat(
        self,
        messages: "str | dict[str, Any] | Any | Sequence[Any]",
        *,
        thread_id: str | None = None,
        recursion_limit: int = 12,
    ):
        """流式对话，逐块产出 ``(类型, 内容)``，供 Streamlit 打字机效果使用。

        产出类型：
            - ``("text", "片段")``：模型正在输出的文字
            - ``("tool", {"name":..., "args":...})``：模型准备调用工具
            - ``("done", {"thread_id":...})``：本轮结束

        注意：需要持久化记忆时优先用 :meth:`chat`（流式下历史由 checkpointer 维护）。
        """
        from langchain_core.messages import AIMessageChunk

        state_messages = _coerce_messages(messages)
        config: dict[str, Any] = {"recursion_limit": max(4, recursion_limit)}
        used_thread = thread_id or new_thread_id()
        if self.checkpointer is not None:
            config["configurable"] = {"thread_id": used_thread}

        try:
            for chunk, _metadata in self.agent.stream(
                {"messages": state_messages}, config=config, stream_mode="messages"
            ):
                if isinstance(chunk, AIMessageChunk):
                    for call in getattr(chunk, "tool_call_chunks", None) or []:
                        if call.get("name"):
                            yield "tool", {"name": call.get("name"), "args": call.get("args")}
                    text = chunk.content
                    if isinstance(text, str) and text:
                        yield "text", text
                    elif isinstance(text, list):
                        for block in text:
                            if isinstance(block, dict) and block.get("type") == "text" and block.get("text"):
                                yield "text", str(block["text"])
        except Exception as exc:  # noqa: BLE001
            raise ChefAgentError(f"AI私厨管家流式调用失败：{type(exc).__name__}: {exc}") from exc

        yield "done", {"thread_id": used_thread}


# ==============================================================================
# 模块级单例
# ==============================================================================

_CHEF: ChefAgent | None = None


def get_chef_agent(*, force_new: bool = False, **kwargs: Any) -> ChefAgent:
    """获取全局管家实例（单例，避免重复初始化模型）。

    Args:
        force_new: True 表示强制重建（例如用户切换了模型或接入了新记忆）。
        **kwargs: 首次创建时透传给 :class:`ChefAgent`。
    """
    global _CHEF
    if _CHEF is None or force_new:
        _CHEF = ChefAgent(**kwargs)
    return _CHEF

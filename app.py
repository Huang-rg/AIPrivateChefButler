"""★ AI私厨管家 · Streamlit Web UI 入口

启动方式：
    1. pip install -r requirements.txt
    2. streamlit run app.py
    （首次运行前请复制 .env.example 为 .env 并填入你自己的 API Key）

【界面设计原则（按用户反馈重做）】
    1. **白底黑字**：整页纯白 #ffffff，正文 #1f2328。之前在浅色背景上没显式设置
       文字颜色，一旦主题是暗色就变成「白底白字、看不见内容」。现在颜色全部写死，
       并且颜色定义集中在下面的 THEME 字典里，改一处即可换肤。
    2. 参考 DeepSeek 网页端的克制风格：细边框、中等圆角、几乎不用阴影、大量留白。
    3. 侧边栏承担导航：新建对话、会话列表（可重命名/删除）、模型切换、更多输入方式。
    4. 所有异常都翻译成中文提示，不把英文堆栈糊到页面上。

【图片进来的四条路】
    ① 上传文件  ② 摄像头（折叠收起）  ③ 剪贴板一键读图  ④ 粘贴 http(s) 图片地址
================================================================================
"""

from __future__ import annotations

import base64
import sys
import time
from pathlib import Path
from typing import Any

import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from utils.recipe_parser import RecipeCard, parse_recipes_from_text, render_recipe_card_html  # noqa: E402

# --------------------------------------------------------------------------- #
# 一、页面配置（必须是第一个 Streamlit 调用）
# --------------------------------------------------------------------------- #

st.set_page_config(
    page_title="AI私厨管家",
    page_icon="🍳",
    layout="wide",
    initial_sidebar_state="expanded",
)


# --------------------------------------------------------------------------- #
# 二、配色与样式
# --------------------------------------------------------------------------- #

THEME: dict[str, str] = {
    "bg": "#ffffff",          # 主背景：纯白
    "text": "#1f2328",        # 正文：深灰黑（必须与 bg 有足够对比）
    "text_muted": "#6b7280",  # 次要文字
    "text_faint": "#9aa0a6",  # 更弱的提示文字
    "sidebar": "#f8f9fa",     # 侧边栏底
    "border": "#e5e7eb",      # 细边框
    "border_soft": "#f0f1f3", # 更浅的分隔
    "accent": "#4d6bfe",      # 主色（克制使用）
    "hover": "#f1f3f5",       # 悬停底
    "grade_a_bg": "#e8f5ec",
    "grade_a_fg": "#136c34",
    "grade_b_bg": "#fff6e0",
    "grade_b_fg": "#8a5a00",
    "grade_c_bg": "#fdecea",
    "grade_c_fg": "#a4231a",
}

_CSS = f"""
<style>
    /* ---------- 基础：白底 + 明确文字色（关键修复） ---------- */
    .stApp, [data-testid="stAppViewContainer"], [data-testid="stMain"] {{
        background: {THEME['bg']} !important;
        color: {THEME['text']} !important;
    }}
    html, body, .stApp, .stMarkdown, .stMarkdown p, .stMarkdown li,
    [data-testid="stMarkdownContainer"], [data-testid="stMarkdownContainer"] p,
    [data-testid="stChatMessage"] p, [data-testid="stChatMessage"] li,
    label, span, div {{
        color: {THEME['text']};
    }}
    h1, h2, h3, h4, h5, h6 {{ color: {THEME['text']} !important; }}
    a {{ color: {THEME['accent']}; }}
    small, .stCaption, [data-testid="stCaptionContainer"] {{ color: {THEME['text_muted']} !important; }}
    hr {{ border-color: {THEME['border_soft']}; }}
    header[data-testid="stHeader"] {{ background: transparent; }}

    /* ---------- 侧边栏 ---------- */
    [data-testid="stSidebar"] {{
        background: {THEME['sidebar']} !important;
        border-right: 1px solid {THEME['border']};
    }}
    [data-testid="stSidebar"] * {{ color: {THEME['text']}; }}

    /* ---------- 品牌区 ---------- */
    .brand {{ display: flex; align-items: center; gap: 10px; padding: 2px 2px 10px 2px; }}
    .brand-icon {{
        width: 34px; height: 34px; border-radius: 9px; flex: 0 0 34px;
        background: linear-gradient(135deg, #ffb26b 0%, #ff7a45 100%);
        display: flex; align-items: center; justify-content: center; font-size: 18px;
    }}
    .brand-name {{ font-size: 15.5px; font-weight: 700; line-height: 1.2; }}
    .brand-sub {{ font-size: 11.5px; color: {THEME['text_faint']}; }}

    /* ---------- 欢迎页卡片 ---------- */
    .hero-title {{ font-size: 26px; font-weight: 700; margin: 6px 0 4px 0; }}
    .hero-sub {{ color: {THEME['text_muted']}; font-size: 14px; margin-bottom: 18px; }}
    .step-card {{
        border: 1px solid {THEME['border']}; border-radius: 12px;
        padding: 14px 16px; background: {THEME['bg']}; height: 100%;
    }}
    .step-card .n {{
        display: inline-block; width: 22px; height: 22px; border-radius: 50%;
        background: {THEME['hover']}; color: {THEME['text_muted']};
        font-size: 12px; font-weight: 700; text-align: center; line-height: 22px; margin-right: 6px;
    }}
    .step-card h5 {{ margin: 0 0 6px 0; font-size: 14px; }}
    .step-card p {{ margin: 0; font-size: 12.5px; color: {THEME['text_muted']}; line-height: 1.6; }}

    /* ---------- 食谱卡片 ---------- */
    .recipe-card {{
        border: 1px solid {THEME['border']}; border-radius: 12px;
        padding: 14px 16px; background: {THEME['bg']}; margin-bottom: 12px;
    }}
    .recipe-card h4 {{ margin: 0 0 9px 0; font-size: 16px; color: {THEME['text']}; }}
    .recipe-meta {{ display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 9px; }}
    .chip {{
        display: inline-block; padding: 2.5px 9px; border-radius: 999px;
        font-size: 12px; font-weight: 600; white-space: nowrap;
        border: 1px solid {THEME['border']}; background: {THEME['hover']}; color: {THEME['text_muted']};
    }}
    .chip-grade-A {{ background: {THEME['grade_a_bg']}; color: {THEME['grade_a_fg']}; border-color: {THEME['grade_a_bg']}; }}
    .chip-grade-B {{ background: {THEME['grade_b_bg']}; color: {THEME['grade_b_fg']}; border-color: {THEME['grade_b_bg']}; }}
    .chip-grade-C {{ background: {THEME['grade_c_bg']}; color: {THEME['grade_c_fg']}; border-color: {THEME['grade_c_bg']}; }}
    .chip-ready {{ background: #e8f1ff; color: #1d4ed8; border-color: #e8f1ff; }}
    /* ---------- 食材标签区 ----------
       布局规范（解决"脏标签挤在一起 / 过滤后留空档"两个问题）：

       1. 容器用 flex + gap，而不是给每个标签加 margin：
          换行时左右间距均匀，不会出现"行尾多出一截空隙"的错位感。
       2. 标签一律 nowrap（单个标签内部不折行），过长时靠 max-width + 省略号收敛，
          绝不让一个标签被拆成两行、把整块布局挤歪。
       3. 分组标题（已有 / 需另买）单独一列，与标签区对齐；空分组整块不渲染，
          所以过滤掉脏标签后**不会留下空行或占位灰条**。
       4. 过滤规则见 utils/recipe_parser.py 的 _is_renderable_ingredient：
          空标签、纯符号、残缺括号一律丢弃，不占位。 */
    .ing-row {{
        display: flex; align-items: flex-start; gap: 8px; margin-top: 8px;
    }}
    .ing-row-label {{
        flex: 0 0 auto; font-size: 12px; color: {THEME['text_faint']};
        line-height: 24px; padding-top: 1px; white-space: nowrap;
    }}
    .ing-list {{
        display: flex; flex-wrap: wrap; gap: 6px; flex: 1 1 auto; min-width: 0;
    }}
    .ing-chip {{
        display: inline-block; max-width: 100%;
        padding: 3px 9px; border-radius: 999px; font-size: 12.5px;
        line-height: 18px; white-space: nowrap;
        overflow: hidden; text-overflow: ellipsis;
        background: {THEME['hover']}; color: {THEME['text']}; border: 1px solid {THEME['border']};
    }}
    .ing-chip.missing {{ color: {THEME['text_faint']}; background: {THEME['bg']}; }}
    .recipe-source {{ margin-top: 10px; }}
    .recipe-source a {{ color: #e8590c; font-weight: 600; text-decoration: none; }}
    .recipe-source a:hover {{ text-decoration: underline; }}

    /* ---------- 会话列表 ---------- */
    .hist-title {{ font-size: 12px; color: {THEME['text_faint']}; font-weight: 700;
        letter-spacing: .5px; margin: 4px 0 6px 2px; }}
    .hist-item {{ font-size: 13px; color: {THEME['text']}; }}

    /* ---------- 输入区提示 ---------- */
    .hint-bar {{
        border: 1px solid {THEME['border']}; border-radius: 10px;
        padding: 8px 12px; background: {THEME['sidebar']};
        font-size: 13px; color: {THEME['text_muted']}; margin-bottom: 8px;
    }}

    /* ---------- 聊天消息：去掉默认气泡底色，保持白底黑字 ---------- */
    [data-testid="stChatMessage"] {{
        background: {THEME['bg']} !important;
        border: 1px solid {THEME['border_soft']};
        border-radius: 12px;
    }}

    /* ---------- 按钮：朴素一点，别花 ---------- */
    .stButton > button {{
        border-radius: 9px; border: 1px solid {THEME['border']};
        background: {THEME['bg']}; color: {THEME['text']};
    }}
    .stButton > button:hover {{ background: {THEME['hover']}; border-color: {THEME['border']}; }}

    .foot-note {{ text-align: center; color: {THEME['text_faint']}; font-size: 11.5px; margin-top: 20px; }}
</style>
"""
st.markdown(_CSS, unsafe_allow_html=True)


# --------------------------------------------------------------------------- #
# 三、会话状态
# --------------------------------------------------------------------------- #

#: 待发送图片存放在 st.session_state["staged_images"]，是一个**列表**，
#: 每项 ``{"id": int, "payload": ImagePayload, "source": str}``。
#: 用列表而不是"按来源分键"，是为了支持**逐张删除**（每张图有独立 id 和删除按钮）。
STAGED_IMAGES_KEY: Final[str] = "staged_images"


def _init_state() -> None:
    defaults: dict[str, Any] = {
        "messages": [],          # 界面展示的消息流
        "thread_id": None,       # 当前会话 ID
        "staged_images": [],     # 待发送图片列表（每项带 id，可逐张删除）
        "image_seq": 0,          # 图片 id 自增序号（保证删除按钮的 key 稳定唯一）
        "last_image": None,      # 最近一次图片（供后续追问继续引用）
        "chef": None,            # ChefAgent
        "chef_signature": None,  # 建 Agent 时的配置指纹（模型变了要重建）
        "init_error": None,
        "memory_info": None,
        "clip_fp_seen": None,    # 已提示过的剪贴板指纹（避免反复提示）
        "clip_pending": False,   # 是否有待确认的剪贴板新图
        "uploader_key": 0,
        "store": None,           # SessionStore
        "rename_target": None,
        "delete_target": None,
    }
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)


_init_state()


# --------------------------------------------------------------------------- #
# 四、资源加载（缓存，避免每次交互都重建）
# --------------------------------------------------------------------------- #


#: 记忆连接失败后，隔多久才允许再试一次（秒）。避免数据库没启动时每次交互都卡 5 秒。
_MEMORY_RETRY_INTERVAL = 30.0


def _load_memory():
    """取记忆后端，**只缓存成功的结果**。

    ⚠️ 踩过的坑：记忆句柄曾被 ``@st.cache_resource`` 无条件缓存，于是"服务启动
    那一瞬间 .env 还没加载好"造成的降级被永久记住——数据库明明是好的，界面却
    一直显示"历史记录不可用"。现在改为：
        - 成功 → 缓存在会话里，后续复用
        - 失败 → 不缓存，但 30 秒内不重复尝试（避免每次都卡住等超时）
    """
    import time

    from core.memory import get_memory_or_fallback

    cached = st.session_state.get("memory_handle")
    if cached is not None:
        return cached

    last_fail = st.session_state.get("memory_fail_at") or 0.0
    if time.time() - last_fail < _MEMORY_RETRY_INTERVAL:
        return get_memory_or_fallback(force_new=True)

    handle = get_memory_or_fallback(force_new=True)
    if handle.persistent:
        st.session_state.memory_handle = handle
        st.session_state.memory_fail_at = None
    else:
        st.session_state.memory_fail_at = time.time()
    return handle


def _load_store():
    """加载会话存储（复用记忆后端的 checkpointer 来恢复对话内容）。"""
    from core.history import SessionStore

    return SessionStore(checkpointer=_load_memory().checkpointer)


@st.cache_resource(show_spinner=False)
def _load_capabilities() -> dict[str, Any]:
    from core.llm_models import get_llm_models

    return get_llm_models().capabilities()


def _model_signature() -> str:
    """当前模型配置的指纹；变了就说明要重建 Agent。"""
    from core import model_settings as settings

    return f"{settings.default_text_model()}|{settings.default_vision_model()}"


def _ensure_chef() -> tuple[Any, str | None]:
    """确保 Agent 可用，返回 (chef, 错误信息)。"""
    signature = _model_signature()
    if st.session_state.chef is not None and st.session_state.chef_signature == signature:
        return st.session_state.chef, None

    try:
        from core.agent import ChefAgent, new_thread_id

        with st.spinner("正在唤醒 AI 私厨管家…"):
            memory = _load_memory()
            chef = ChefAgent(checkpointer=memory.checkpointer)
        st.session_state.chef = chef
        st.session_state.chef_signature = signature
        st.session_state.memory_info = (memory.backend, memory.target, list(memory.notes))
        if st.session_state.thread_id is None:
            st.session_state.thread_id = new_thread_id()
        return chef, None
    except Exception as exc:  # noqa: BLE001 - 任何失败都变中文提示
        message = f"{type(exc).__name__}: {exc}"
        st.session_state.init_error = message
        return None, message


# --------------------------------------------------------------------------- #
# 五、会话历史
# --------------------------------------------------------------------------- #


def _store() -> Any:
    if st.session_state.store is None:
        st.session_state.store = _load_store()
    return st.session_state.store


def _register_session(title_source: str, message_count: int) -> None:
    """把当前会话登记进历史列表（标题取自首条**有意义**的用户消息）。

    为什么要判断"有意义"：用户第一句常常是「1」「2」这种测试内容。
    如果直接拿来当标题，侧边栏就会出现一堆叫「1」「2」的会话，
    看起来像历史记录丢了。所以：
        - 只有当前标题还是占位符（空/新对话）时，才用新消息替换
        - 已经在用的好标题不会被后续消息覆盖
    """
    store = _store()
    if not store.available() or not st.session_state.thread_id:
        return

    from core.history import is_meaningful_title, make_title

    existing = (store.get_title(st.session_state.thread_id) or "").strip()
    title: str | None = None

    # 两种情况下才更新标题：还没有标题，或者标题还是占位符
    if not existing or not is_meaningful_title(existing):
        candidate = make_title(title_source, fallback="")
        if candidate:
            title = candidate

    store.upsert_session(st.session_state.thread_id, title=title, message_count=message_count)


def _restore_message(message: dict[str, Any]) -> dict[str, Any]:
    """把从数据库恢复的一条消息，还原成界面可以完整渲染的结构。

    ⚠️ 真实事故（用户报的最严重问题）：
        早期版本恢复历史时把 ``parsed`` 一律设成 None，于是助手消息
        **只渲染原始 Markdown**，菜谱卡片和「查看完整内容」入口统统消失——
        用户重进会话后只能看到完整版，再也切不回带关键词标签的卡片视图。

    这里对助手消息**重新解析一次**：
        数据库里存的是完整回复文本（含食材、步骤、来源链接），
        解析器对同一段文本是**稳定可重现**的，所以能完整还原卡片与切换入口。
    """
    role = message.get("role", "user")
    content = message.get("content", "")
    restored: dict[str, Any] = {"role": role, "content": content, "parsed": None}

    if role == "assistant" and content:
        try:
            parsed = parse_recipes_from_text(content)
            restored["parsed"] = parsed or None
        except Exception as exc:  # noqa: BLE001 - 解析失败就退回纯文本，不影响阅读
            logger.debug("恢复历史时解析菜谱卡片失败（退回纯文本）：%s", exc)

    return restored


def _switch_session(thread_id: str) -> None:
    """切换到另一个会话：从检查点恢复完整对话。

    恢复后必须与「首次对话」表现完全一致——
    包括菜谱卡片、食材标签，以及「查看完整内容」的切换入口。
    """
    store = _store()
    restored = store.load_messages(thread_id) if store.available() else []
    st.session_state.thread_id = thread_id
    st.session_state.messages = [_restore_message(m) for m in restored]
    _clear_staged_images()
    st.rerun()


def _new_session() -> None:
    from core.agent import new_thread_id

    st.session_state.thread_id = new_thread_id()
    st.session_state.messages = []
    _clear_staged_images()
    st.session_state.uploader_key += 1
    st.rerun()


@st.dialog("重命名对话")
def _rename_dialog() -> None:
    target = st.session_state.rename_target
    if not target:
        st.rerun()
    store = _store()
    current = store.get_title(target) or ""
    new_title = st.text_input("新名称", value=current, max_chars=60)
    col1, col2 = st.columns(2)
    if col1.button("保存", use_container_width=True, type="primary"):
        if store.rename_session(target, new_title):
            st.session_state.rename_target = None
            st.rerun()
        else:
            st.error("重命名失败，请稍后重试。")
    if col2.button("取消", use_container_width=True):
        st.session_state.rename_target = None
        st.rerun()


@st.dialog("删除对话")
def _delete_dialog() -> None:
    target = st.session_state.delete_target
    if not target:
        st.rerun()
    st.warning("删除后该对话会从列表中隐藏（数据仍保留在数据库里，可联系管理员恢复）。")
    col1, col2 = st.columns(2)
    if col1.button("确认删除", use_container_width=True, type="primary"):
        store = _store()
        store.delete_session(target)
        st.session_state.delete_target = None
        if st.session_state.thread_id == target:
            _new_session()
        st.rerun()
    if col2.button("取消", use_container_width=True):
        st.session_state.delete_target = None
        st.rerun()


# --------------------------------------------------------------------------- #
# 六、模型切换
# --------------------------------------------------------------------------- #


def _render_model_switcher() -> None:
    """侧边栏的模型切换区。

    下拉框的选项**直接来自 core/model_settings.py 的模型清单**，
    所以在代码里改了清单，界面会自动跟着变，不需要改界面代码。
    """
    from core.llm_models import list_model_options, set_model_override

    with st.expander("🤖 模型设置", expanded=False):
        from core import model_settings as settings

        text_options = list_model_options("text")
        vision_options = list_model_options("vision")

        # 选项用「显示文本」，取值时再查回模型名。
        # 为什么不直接把模型名当选项？因为那样下拉框里只能看到 qwen3.8-flash
        # 这种机器名；用显示文本更友好（还能标出「可看图」与实测备注）。
        text_labels = [item["label"] for item in text_options]
        text_by_label = {item["label"]: item["name"] for item in text_options}
        vision_labels = [item["label"] for item in vision_options]
        vision_by_label = {item["label"]: item["name"] for item in vision_options}

        # 回填当前选中的项（按当前生效的模型名找出对应显示文本）
        current_text = settings.default_text_model()
        text_index = next(
            (i for i, item in enumerate(text_options) if item["name"] == current_text), 0
        )
        picked_text_label = st.selectbox(
            "💬 文字对话模型",
            options=text_labels,
            index=text_index,
            help="负责聊天、理解意图、整理推荐。选项来自 core/model_settings.py 的模型清单。",
        )

        current_vision = settings.default_vision_model()
        vision_index = next(
            (i for i, item in enumerate(vision_options) if item["name"] == current_vision), 0
        )
        picked_vision_label = st.selectbox(
            "👁 看图识食材模型",
            options=vision_labels,
            index=vision_index,
            help="必须实测支持图像输入。实测看不了图的模型不会出现在这里。",
        )

        picked_text = text_by_label.get(picked_text_label, text_options[0]["name"])
        picked_vision = vision_by_label.get(picked_vision_label, vision_options[0]["name"])

        if picked_text != current_text or picked_vision != current_vision:
            try:
                set_model_override("text", picked_text, rebuild=False)
                set_model_override("vision", picked_vision, rebuild=False)
                st.session_state.chef = None  # 强制按新模型重建 Agent
                st.success(f"已切换：文字 {picked_text}｜看图 {picked_vision}")
                st.rerun()
            except Exception as exc:  # noqa: BLE001
                st.error(f"切换失败：{exc}")

        capabilities = _load_capabilities()
        st.caption(
            f"当前生效：文字 `{settings.default_text_model()}`｜看图 `{settings.default_vision_model()}`"
        )
        st.caption("提示：这两个下拉框的选项自动同步自 core/model_settings.py，改代码后界面会跟着变。")

        if st.button("🔍 测试模型连接", use_container_width=True):
            try:
                from core.llm_models import get_llm_models

                with st.spinner("正在探测…"):
                    report = get_llm_models(force_new=True).self_check()
                for label, info in report.items():
                    mark = "✅" if info.get("可用") else "❌"
                    st.write(f"{mark} {label}：`{info.get('模型')}` — {info.get('说明')}")
            except Exception as exc:  # noqa: BLE001
                st.error(f"自检失败：{exc}")


# --------------------------------------------------------------------------- #
# 七、图片输入（四条路）
# --------------------------------------------------------------------------- #
#
# 【数据结构】待发送的图片是一个**列表**，每张带自己的 id，支持逐张删除。
#   真实需求（用户反馈）：原来只有一个「一键清空」，把剪贴板图和上传图一起删掉，
#   太粗暴。现在每张图右上角有独立删除按钮，想留哪张留哪张。
#
#   每一项形如：{"id": 自增序号, "payload": ImagePayload, "source": "upload"/"clipboard"/…}

#: 图片来源的中文名（用于界面标注）
SOURCE_LABELS: dict[str, str] = {
    "upload": "上传",
    "camera": "拍照",
    "clipboard": "剪贴板",
    "url": "图片网址",
    "cache": "缓存",
}


def _staged_images() -> list[dict[str, Any]]:
    """取出当前所有待发送图片（不清除）。"""
    staged = st.session_state.get("staged_images")
    if not isinstance(staged, list):
        staged = []
        st.session_state.staged_images = staged
    return staged


def _stage_image(payload: Any, source: str) -> None:
    """把一张图片加入待发送区（**追加**，不覆盖已有的）。"""
    staged = _staged_images()
    st.session_state.image_seq = int(st.session_state.get("image_seq", 0)) + 1
    staged.append({"id": st.session_state.image_seq, "payload": payload, "source": source})
    st.session_state.last_image = payload


def _remove_staged_image(image_id: int) -> None:
    """删除指定的待发送图片（只删这一张，其余保留）。"""
    staged = _staged_images()
    remaining = [item for item in staged if item.get("id") != image_id]
    st.session_state.staged_images = remaining
    # 同步更新"最近一张图片"（供后续追问继续引用）
    st.session_state.last_image = remaining[-1]["payload"] if remaining else None


def _clear_staged_images() -> None:
    """清空所有待发送图片。"""
    st.session_state.staged_images = []
    st.session_state.last_image = None


def _render_staged_images(*, with_delete: bool = True) -> None:
    """把待发送图片逐张渲染出来，每张右上角带独立删除按钮。

    Args:
        with_delete: 是否显示删除按钮（主区只做预览时可关掉，避免重复 key）。
    """
    from utils.image_handler import describe_image

    staged = _staged_images()
    if not staged:
        return

    st.markdown(f"**待发送图片（{len(staged)} 张）**")
    for item in list(staged):
        image_id = item.get("id")
        payload = item.get("payload")
        label = SOURCE_LABELS.get(item.get("source", ""), "图片")
        try:
            caption = f"{label}｜{describe_image(payload)}"
        except Exception:  # noqa: BLE001
            caption = label

        if with_delete:
            col_img, col_del = st.columns([8, 1])
            with col_img:
                st.image(payload.raw_bytes, caption=caption, use_container_width=True)
            with col_del:
                # 独立删除按钮：只删这一张
                if st.button("✕", key=f"del_img_{image_id}", help="删除这张图片"):
                    _remove_staged_image(image_id)
                    st.rerun()
        else:
            st.image(payload.raw_bytes, caption=caption, use_container_width=True)


def _render_image_inputs() -> None:
    """侧边栏的图片输入区：上传 / 摄像头（折叠）/ 剪贴板 / URL。"""
    st.markdown("**📷 食材图片**")

    uploaded = st.file_uploader(
        "上传图片",
        type=["jpg", "jpeg", "png", "webp", "bmp", "gif"],
        key=f"uploader_{st.session_state.uploader_key}",
        label_visibility="collapsed",
        accept_multiple_files=True,
    )
    if uploaded:
        files = uploaded if isinstance(uploaded, list) else [uploaded]
        for handle in files:
            try:
                from utils.image_handler import prepare_image_from_uploaded_file

                payload = prepare_image_from_uploaded_file(handle)
                # 同一张图重复上传时不要重复入列（按内容指纹去重）
                already = any(
                    item["payload"].raw_bytes == payload.raw_bytes
                    for item in _staged_images()
                )
                if already:
                    continue
                _stage_image(payload, "upload")
            except Exception as exc:  # noqa: BLE001
                st.error(f"图片读取失败：{exc}")

    with st.expander("📸 摄像头拍照", expanded=False):
        camera = st.camera_input("用摄像头拍一张", key=f"camera_{st.session_state.uploader_key}")
        if camera is not None:
            try:
                from utils.image_handler import prepare_image_from_uploaded_file

                payload = prepare_image_from_uploaded_file(camera)
                already = any(item["payload"].raw_bytes == payload.raw_bytes for item in _staged_images())
                if not already:
                    _stage_image(payload, "camera")
            except Exception as exc:  # noqa: BLE001
                st.error(f"拍照读取失败：{exc}")

    col_a, col_b = st.columns(2)
    if col_a.button("📋 剪贴板", use_container_width=True, help="读取你复制/截图的图片（Win+Shift+S 后直接点这里）"):
        try:
            from utils.image_input import probe_clipboard_image

            probe = probe_clipboard_image()
            if probe.has_image:
                _stage_image(probe.payload, "clipboard")
                st.session_state.clip_fp_seen = probe.fingerprint
                st.rerun()
            else:
                st.warning(probe.error or "剪贴板里没有图片。")
        except Exception as exc:  # noqa: BLE001
            st.error(f"读取剪贴板失败：{exc}")

    # 这个按钮保留为"全部清空"的快捷方式，但逐张删除请用每张图上的 ✕
    if col_b.button("🧹 全部清空", use_container_width=True, help="清空所有待发送图片（想单张删除请点图片右上角的 ✕）"):
        _clear_staged_images()
        st.session_state.uploader_key += 1
        st.rerun()

    with st.expander("🔗 用图片网址", expanded=False):
        url = st.text_input("图片地址", placeholder="https://example.com/food.jpg", key="url_input")
        if st.button("下载这张图", use_container_width=True):
            try:
                from utils.image_input import download_image_cached

                payload = download_image_cached(url.strip())
                _stage_image(payload, "url")
                st.rerun()
            except Exception as exc:  # noqa: BLE001
                st.error(f"下载失败：{exc}")

    _render_staged_images(with_delete=True)


def _render_clipboard_hint() -> None:
    """剪贴板新图自动检测提示条。

    只读**图片**：不会读取剪贴板里的文字或密码。指纹没变就不打扰你。
    """
    try:
        from utils.image_input import probe_clipboard_image

        probe = probe_clipboard_image()
    except Exception:  # noqa: BLE001 - 检测失败静默跳过，不影响主流程
        return

    if not probe.has_image or probe.fingerprint == st.session_state.clip_fp_seen:
        return

    st.markdown(
        '<div class="hint-bar">📋 检测到你刚复制了一张图片，'
        "可以直接把它加进这次提问。</div>",
        unsafe_allow_html=True,
    )
    col1, col2, _ = st.columns([1, 1, 4])
    if col1.button("用它", key="use_clip", type="primary"):
        _stage_image(probe.payload, "clipboard")
        st.session_state.clip_fp_seen = probe.fingerprint
        st.rerun()
    if col2.button("忽略", key="ignore_clip"):
        st.session_state.clip_fp_seen = probe.fingerprint
        st.rerun()


# --------------------------------------------------------------------------- #
# 八、侧边栏
# --------------------------------------------------------------------------- #


def _render_sidebar() -> dict[str, Any]:
    with st.sidebar:
        st.markdown(
            '<div class="brand"><div class="brand-icon">🍳</div>'
            '<div><div class="brand-name">AI私厨管家</div>'
            '<div class="brand-sub">拍张照，就知道做什么菜</div></div></div>',
            unsafe_allow_html=True,
        )

        if st.button("＋ 新建对话", use_container_width=True, type="primary"):
            _new_session()

        # ---------------- 会话列表 ---------------- #
        store = _store()
        st.markdown('<div class="hist-title">最近对话</div>', unsafe_allow_html=True)
        if store.available():
            sessions = store.list_sessions(limit=30)
            if not sessions:
                st.caption("还没有历史对话。")
            for info in sessions:
                active = info.thread_id == st.session_state.thread_id
                row = st.columns([6, 1, 1])
                label = f"{'● ' if active else ''}{info.display_title}"
                if row[0].button(label, key=f"pick_{info.thread_id}", use_container_width=True,
                                 help=f"{info.updated_text} · {info.message_count} 条消息"):
                    _switch_session(info.thread_id)
                if row[1].button("✏️", key=f"ren_{info.thread_id}", help="重命名"):
                    st.session_state.rename_target = info.thread_id
                    _rename_dialog()
                if row[2].button("🗑", key=f"del_{info.thread_id}", help="删除"):
                    st.session_state.delete_target = info.thread_id
                    _delete_dialog()
        else:
            # ⚠️ 这里必须说清「为什么没有」，不能笼统显示「还没有历史对话」——
            #    那会让用户以为记录丢了，实际上是数据库没连上。
            from core.memory import diagnose_unavailable

            st.warning("历史记录暂不可用", icon="⚠️")
            st.caption(diagnose_unavailable())

        st.divider()
        _render_image_inputs()

        st.divider()
        _render_model_switcher()

        st.divider()
        # 注意：expander 里的控件在 Streamlit 中**总会执行**，但为了代码健壮性，
        # 这里仍先给默认值，避免将来重构时变量未定义。
        max_results = 5
        sort_pref = "综合"
        with st.expander("⚙️ 生成设置", expanded=False):
            max_results = st.slider("菜谱条数", min_value=3, max_value=10, value=max_results)
            sort_pref = st.radio("排序偏好", ["综合", "营养优先", "简单优先"], index=0, horizontal=True)

        memory_info = st.session_state.memory_info
        if memory_info:
            backend, target, notes = memory_info
            icon = "🗄️" if backend == "postgres" else "🧠"
            st.caption(f"{icon} 记忆：{target}")
            for note in notes:
                st.caption(f"⚠️ {note}")

        if st.button("🔄 重连模型", use_container_width=True):
            st.session_state.chef = None
            st.session_state.init_error = None
            st.cache_resource.clear()
            st.rerun()

    return {"max_results": max_results, "sort_pref": sort_pref}


# --------------------------------------------------------------------------- #
# 九、主区渲染
# --------------------------------------------------------------------------- #


def _render_welcome() -> None:
    st.markdown('<div class="hero-title">今天想做点什么？</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="hero-sub">上传一张冰箱或厨房的照片，我来识别食材、联网搜真实菜谱，'
        "再按营养和难度帮你排好序。</div>",
        unsafe_allow_html=True,
    )
    col1, col2, col3 = st.columns(3)
    cards = [
        ("1", "拍张照", "左侧上传食材照片，或用剪贴板一键读入截图。"),
        ("2", "自动识别", "视觉模型看清有哪些食材、大概多少量，看不清就直说，绝不瞎猜。"),
        ("3", "联网找菜谱", "搜索下厨房等站点的真实做法，附上来源链接。"),
    ]
    for column, (number, title, desc) in zip((col1, col2, col3), cards):
        column.markdown(
            f'<div class="step-card"><h5><span class="n">{number}</span>{title}</h5><p>{desc}</p></div>',
            unsafe_allow_html=True,
        )
    st.write("")
    with st.expander("没有照片？试试这样问"):
        st.markdown(
            "- 我冰箱里有西红柿、鸡蛋、小葱，能做点什么？\n"
            "- 只有半颗白菜和一把粉丝，想吃清淡点的\n"
            "- 我女儿吃不了辣，帮我推荐几个不辣的菜\n"
            "- 上面那几个菜里，哪个最适合减脂期吃？"
        )


def _render_answer(content: str, parsed: list[RecipeCard] | None) -> None:
    """渲染助手回答：能解析出菜谱就出卡片，否则原样 Markdown（绝不白屏）。"""
    if parsed:
        for card in parsed:
            st.markdown(render_recipe_card_html(card), unsafe_allow_html=True)
        with st.expander("📄 查看完整回答"):
            st.markdown(content)
    else:
        st.markdown(content)


def _handle_input(user_text: str, settings: dict[str, Any]) -> None:
    """处理一次输入：可能是文字、可能是图片地址、也可能带若干张待发送图片。"""
    from utils.image_input import ImageInputError, detect_image_url, download_image_cached

    # 取出所有待发送图片（多图），并清空暂存区
    staged_items = _staged_images()
    staged_payloads = [item["payload"] for item in staged_items]
    st.session_state.staged_images = []

    image_url = detect_image_url(user_text)

    # 情况一：用户粘贴了图片地址 → 服务端下载后当成图片处理
    if not staged_payloads and image_url:
        try:
            with st.spinner("正在下载图片…"):
                downloaded = download_image_cached(image_url)
            staged_payloads = [downloaded]
            st.session_state.last_image = downloaded
        except ImageInputError as exc:
            st.session_state.messages.append({"role": "user", "content": user_text})
            st.session_state.messages.append(
                {"role": "assistant", "content": f"❌ 这张图片没能下载下来：{exc}", "parsed": None}
            )
            return

    # 本轮要送给模型的图片：优先用刚暂存的；没有则沿用上一轮那张（方便追问）
    images_for_turn = staged_payloads or ([st.session_state.last_image] if st.session_state.last_image else [])
    if staged_payloads:
        st.session_state.last_image = staged_payloads[-1]

    message: dict[str, Any] = {
        "role": "user",
        "content": user_text,
        "has_image": bool(images_for_turn),
    }
    # 只把**本轮新上传**的图存进消息用于回显（避免每轮重复展示旧图）
    if staged_payloads:
        message["images_b64"] = [p.base64_str for p in staged_payloads]
    st.session_state.messages.append(message)

    chef, error = _ensure_chef()
    if chef is None:
        st.session_state.messages.append(
            {"role": "assistant", "content": f"❌ 管家启动失败：{error}", "parsed": None}
        )
        return

    prompt = (
        f"{user_text}\n\n（请最多推荐 {settings['max_results']} 个菜谱；"
        f"排序偏好：{settings['sort_pref']}。）"
    )
    # 多图 + 文本一起送给模型
    payload: list[Any] = [*images_for_turn, prompt]

    with st.chat_message("assistant", avatar="🍳"):
        placeholder = st.empty()
        placeholder.markdown("_正在看图、搜菜谱…_")
        started = time.perf_counter()
        try:
            result = chef.chat(payload, thread_id=st.session_state.thread_id)
        except Exception as exc:  # noqa: BLE001
            placeholder.empty()
            st.error(f"管家这次没答上来：{exc}")
            st.session_state.messages.append(
                {"role": "assistant", "content": f"❌ 调用失败：{exc}", "parsed": None}
            )
            return
        elapsed = time.perf_counter() - started
        placeholder.empty()

        text = result.get("text") or "（模型没有返回内容）"
        tool_names = [call.get("name") for call in result.get("tool_calls") or []]
        if tool_names:
            st.caption(f"🔍 联网搜索 {len(tool_names)} 次（{'、'.join(tool_names)}）· {elapsed:.1f}s")
        else:
            st.caption(f"💬 未联网搜索 · {elapsed:.1f}s")

        parsed = parse_recipes_from_text(text)
        _render_answer(text, parsed)

    st.session_state.messages.append(
        {
            "role": "assistant",
            "content": text,
            "parsed": parsed,
            "tool_names": tool_names,
            "elapsed": round(elapsed, 1),
        }
    )
    _register_session(user_text, len(st.session_state.messages))


def main() -> None:
    settings = _render_sidebar()

    if st.session_state.init_error:
        st.error(f"⚠️ 初始化失败：{st.session_state.init_error}")
        st.caption("常见原因：网络不通、Key 无效、模型名不可用。可在左侧点「重连模型」重试。")

    if not st.session_state.messages:
        _render_welcome()
    else:
        for message in st.session_state.messages:
            role = message.get("role", "user")
            with st.chat_message(role, avatar="🧑‍🍳" if role == "user" else "🍳"):
                # 用户消息里附带的图片（支持多张）
                for encoded in message.get("images_b64") or []:
                    try:
                        st.image(base64.b64decode(encoded), width=240)
                    except Exception:  # noqa: BLE001 - 图片只是装饰，坏了不影响文字
                        pass
                if message.get("image_b64"):  # 兼容旧结构
                    try:
                        st.image(base64.b64decode(message["image_b64"]), width=240)
                    except Exception:  # noqa: BLE001
                        pass
                if role == "assistant":
                    if message.get("tool_names"):
                        st.caption(
                            f"🔍 联网搜索 {len(message['tool_names'])} 次 · {message.get('elapsed', '?')}s"
                        )
                    _render_answer(message.get("content", ""), message.get("parsed"))
                else:
                    st.markdown(message.get("content", ""))

    _render_clipboard_hint()

    user_text = st.chat_input("说说你的食材、口味或忌口…（也可以直接粘贴图片网址）")
    if user_text:
        with st.chat_message("user", avatar="🧑‍🍳"):
            for item in _staged_images():
                st.image(item["payload"].raw_bytes, width=240)
            st.markdown(user_text)
        _handle_input(user_text, settings)
        st.rerun()

    st.markdown(
        '<div class="foot-note">AI私厨管家 · 多模态大模型 + Tavily 联网搜索 ｜ '
        "菜谱来源为第三方网站，请以实际为准</div>",
        unsafe_allow_html=True,
    )

    # 「↓ 滚动到最新消息」悬浮按钮：
    # 只在用户向上翻看历史、视图不在底部时浮现；点击平滑滚到底部后自动隐藏。
    # 有新消息而用户不在底部时，按钮上会显示未读条数。
    from utils.scroll_button import render_scroll_to_latest

    render_scroll_to_latest()


if __name__ == "__main__":
    main()

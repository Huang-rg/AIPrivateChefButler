"""菜谱解析器 —— 把管家的 Markdown 回答解析成结构化的菜谱卡片数据。

================================================================================
【为什么需要它】
    管家（Agent）返回的是一段漂亮的 Markdown 文本，人看着舒服，但界面想把它
    渲染成「营养/难度/耗时/来源」的卡片，就需要把文本里的字段抠出来。

    另一种做法是让模型直接输出结构化 JSON（项目里已经支持，见
    schemas/food_schemas.py）。但结构化输出会让回答变得生硬、失去对话感。
    所以这里的选择是：**回答用 Markdown（好看、自然），界面再尽力解析成卡片**。

【设计原则：宁可退回纯文本，也绝不白屏】
    模型输出格式随时可能变（换个模型、换个提示词就变了）。所以本解析器的
    约定是——解析不出来就返回空列表，由界面决定退回 Markdown 原样展示。
    绝不允许因为格式变化就让用户看不到任何内容。

【从源码里独立出来的原因】
    这些函数是纯文本处理逻辑，放在 app.py 里会被 Streamlit 的页面代码缠住，
    既不好复用（第四步的结构化模型、未来的接口都能用）也不方便单独测试。
================================================================================
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["RecipeCard", "clean_md", "parse_recipes_from_text", "render_recipe_card_html"]


# --------------------------------------------------------------------------- #
# 正则
# --------------------------------------------------------------------------- #

#: 菜谱小标题：`### 1. 菜名` / `**1. 菜名**` / `1. 菜名`
_HEADING_RE = re.compile(r"^(\s{0,3})(?:(#{2,4})\s*)?(?:\*\*)?\s*(\d+)[.、)]\s*(.+?)\s*(?:\*\*)?\s*$")

#: 营养评级：兼容「营养评级：B」「**营养评级**：A（...）」「营养 B级」。
#: 关键点：冒号是可选的、`**` 与空格可以夹在中间——模型输出里加粗写法非常常见。
_GRADE_RE = re.compile(r"(?:营养评级|营养)\s*\**\s*[：:]?\s*\**\s*([ABC])\b", re.IGNORECASE)

#: 难度星级：兼容「制作难度：⭐⭐⭐」「**制作难度**：⭐ 1星」「难度 ⭐⭐」
_STARS_RE = re.compile(r"(?:制作难度|难度)\s*\**\s*[：:]?\s*\**\s*((?:⭐|★|\*){1,5})")

#: 纯数字星级（模型有时写「1星」而不是画星号）
_STARS_NUM_RE = re.compile(r"(?:制作难度|难度)\s*\**\s*[：:]?\s*\**\s*([1-5])\s*星")

#: 耗时
_TIME_RE = re.compile(r"(\d+)\s*(?:分钟|min)")

#: 来源链接
_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)]+)\)")

#: 食材行：兼容「- 🧺 **需要食材**：xxx」
_ING_RE = re.compile(r"(?:需要食材|食材|用料)\s*\**\s*[：:]\s*(.+)")

#: 食材条目里的状态前缀，解析前先剥掉
_ING_PREFIX_RE = re.compile(r"^(?:已有|家里有|现有|需另买|需要买|还缺|缺少|没有)\s*[：:]?\s*")

#: 判断某段食材是否属于「需另买」（含 ❓/🛒/❌ 这类标记）
_MISSING_RE = re.compile(r"另买|需买|还缺|缺少|没有|❓|🛒|❌")

#: 判断某段食材是否明确属于「已有」——用于切换回"已有"区段
_HAVE_MARK_RE = re.compile(r"已有|家里有|现有|不需要买|不用买|✅|✔️")

#: 列表符号与 emoji 前缀
_ING_BULLET_RE = re.compile(r"^[✅❓🛒✔️❌\-\s•·]+")

#: 状态词扫描：只负责**找出位置**，冒号与空白由调用方一并吃掉。
#:
#: ⚠️ 踩过的坑：早期用「前缀逐个试探」的写法（先试 emoji 再试词），
#:    对「❓ 需另买：姜」只会匹配到 `❓ `，把「需另买：」留在后面的片段里，
#:    导致状态归属错乱（"冰糖"被算成了"已有"）。
#:    改成"先全量扫描位置、再统一切分"就没有这个问题。
#:    注意也要覆盖「不需要买」这种反向表达（它属于"已有"）。
_STATUS_MARK_RE = re.compile(
    r"(?:不需要买|已有|家里有|现有|需另买|需要买|还缺|缺少|没有|✅|❓|🛒|✔️|❌|⚠️)"
)

#: 小标题最大长度（超过这个长度的「编号行」视为普通正文，防止误判）
_MAX_TITLE_LEN = 40

#: 做法步骤的特征词。菜名里几乎不会出现这些动词，出现就说明这行是步骤而不是菜名。
_STEP_VERBS = (
    "打散", "备用", "倒入", "加入", "放入", "下锅", "翻炒", "炒出", "盛出", "捞出",
    "切块", "切丝", "切片", "洗净", "沥干", "腌制", "调成", "淋入", "撒上", "出锅",
    "大火", "中小火", "小火", "热锅", "冷油", "油热", "煮", "蒸", "焖", "炖",
)

#: 句末标点。菜名基本不会以句号/感叹号结尾，而步骤经常是完整句子。
_SENTENCE_END = "。．.！!；;，,"


# --------------------------------------------------------------------------- #
# 数据结构
# --------------------------------------------------------------------------- #


@dataclass
class RecipeCard:
    """一张菜谱卡片。

    Attributes:
        name: 菜名。
        grade: 营养评级 A/B/C；解析不到为 None。
        stars: 难度星级 1~5；解析不到为 None。
        minutes: 耗时（分钟）；解析不到为 None。
        ingredients: 已有食材。
        missing: 还需另买的食材。
        link: 来源链接。
        link_text: 链接显示文字。
    """

    name: str
    grade: str | None = None
    stars: int | None = None
    minutes: int | None = None
    ingredients: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    link: str | None = None
    link_text: str | None = None

    @property
    def is_ready(self) -> bool:
        """是否食材齐全、马上能做。"""
        return not self.missing

    @property
    def stars_text(self) -> str:
        """星级的可视化写法。"""
        return "⭐" * self.stars if self.stars else "—"

    @property
    def grade_label(self) -> str:
        """营养评级的中文说明。"""
        return {"A": "营养 A · 均衡", "B": "营养 B · 中等", "C": "营养 C · 偏重口"}.get(
            self.grade or "", f"营养 {self.grade}" if self.grade else "营养未知"
        )


# --------------------------------------------------------------------------- #
# 工具
# --------------------------------------------------------------------------- #


def clean_md(text: str) -> str:
    """去掉 Markdown 强调符号，便于在卡片里展示。"""
    return re.sub(r"\*\*|__|`", "", text).strip()


# --------------------------------------------------------------------------- #
# 解析
# --------------------------------------------------------------------------- #


def _looks_like_step(title: str) -> bool:
    """判断一个「编号行」到底是做法步骤，还是菜谱小标题。

    这是解析器最容易出错的地方：模型经常这样写——

        ### 1. 经典浓郁勾芡版
        - 📝 **做法要点**：
            1. 鸡蛋打散炒至凝固后盛出备用。
            2. 锅中留底油，倒入切块的西红柿。

    如果只看「有没有编号」，上面两个步骤会被误判成两道新菜谱，
    一道菜就被切成了三份。所以要靠几个特征把它们区分开：

        1. 有没有 Markdown 标题标记（``###``）或加粗包裹 —— 有就是菜谱标题
        2. 有没有行首缩进 —— 步骤通常缩进在「做法要点」下面
        3. 标题里出现「打散/倒入/翻炒」这类动词 —— 那多半是步骤
        4. 以句号逗号结尾的完整句子 —— 菜名不会这么写

    Args:
        title: 已去掉编号前缀的候选标题文本。

    Returns:
        True 表示「这更像一条步骤」，应把它并入上一道菜谱的正文。
    """
    text = title.strip()
    if not text:
        return True
    if any(verb in text for verb in _STEP_VERBS):
        return True
    if text[-1] in _SENTENCE_END:
        return True
    return False


def _is_recipe_heading(line: str) -> str | None:
    """如果这行是菜谱小标题，返回清洗后的菜名；否则返回 None。"""
    matched = _HEADING_RE.match(line)
    if not matched:
        return None

    indent, hashes, _number, raw_title = matched.groups()
    title = clean_md(raw_title)
    if not title:
        return None

    # 条件一：有 Markdown 标题标记或加粗包裹，基本可以确定是菜谱标题
    explicit = bool(hashes) or "**" in line[: matched.start(4)]
    # 条件二：顶格写（没有缩进）
    top_level = not indent

    if len(title) > _MAX_TITLE_LEN:
        return None
    if _looks_like_step(title) and not explicit:
        return None
    if not explicit and not top_level:
        return None
    return title


def _split_blocks(text: str) -> list[tuple[str, str]]:
    """按「编号小标题」把回答切成 (菜名, 正文) 若干块。"""
    blocks: list[tuple[str, list[str]]] = []
    current: tuple[str, list[str]] | None = None

    for line in text.splitlines():
        title = _is_recipe_heading(line)
        if title is not None:
            if current is not None:
                blocks.append(current)
            current = (title, [])
            continue
        if current is not None:
            current[1].append(line)
    if current is not None:
        blocks.append(current)

    return [(name, "\n".join(body)) for name, body in blocks]


def _find_status_marks(text: str) -> list[re.Match[str]]:
    """扫描全文，找出所有「状态词」出现的位置。

    状态词有两种写法，且**常常叠在一起**：``❓ 需另买：``、``✅ 已有：``。
    本函数只负责找出位置，具体怎么切分交给 :func:`_split_outside_brackets`。

    ⚠️ 踩过的坑：早期用「前缀逐个试探」的写法，`❓ 需另买：姜` 只会匹配到 `❓ `，
    把「需另买：」留在后面的片段里，导致状态归属错乱（冰糖被算成"已有"）。
    改成"先全量扫描位置、再统一切分"就没这个问题了。
    """
    return list(_STATUS_MARK_RE.finditer(text or ""))


def _split_with_status(text: str) -> list[tuple[str, str]]:
    """按分隔符切分，并记录每一段属于哪个状态区段。

    为什么要返回状态（真实事故）：
        切分器会**把状态词从数据里剥掉**（这是必要的，否则「需另买」会被当成食材）。
        但剥离之后，如果还想去片段里搜索状态词来判断归属，就永远搜不到——
        结果所有条目都被算成"已有"（"冰糖"被归错的根因）。
        所以状态必须在切分时就记下来，随片段一起返回。

    Returns:
        ``[(片段内容, 状态)]``，状态取值：
            ``"missing"`` 需另买 / ``"have"`` 已有 / ``""`` 未标注（沿用上一段）
    """
    delimiters = "、,，;；"
    bracket_pairs = {"（": "）", "(": ")", "【": "】", "[": "]"}
    closers = set(bracket_pairs.values())

    # 预先算出所有状态词的位置，并让它后面的冒号一并被吃掉
    status_spans: dict[int, tuple[int, str]] = {}
    for mark in _find_status_marks(text):
        end = mark.end()
        while end < len(text) and text[end] in "：: \t":
            end += 1
        token = mark.group(0)
        if _MISSING_RE.search(token):
            state = "missing"
        elif _HAVE_MARK_RE.search(token):
            state = "have"
        else:
            state = ""
        status_spans[mark.start()] = (end, state)

    segments: list[tuple[str, str]] = []
    current: list[str] = []
    stack: list[str] = []
    index = 0

    while index < len(text):
        char = text[index]

        # 括号外遇到状态词 → 在此切分，并把连续出现的状态词全部吃掉。
        # ⚠️ 必须 while 而不是 if：模型常写「❓ 需另买：」这种叠加形态，
        #    if 只跳过一个标记会落在下一个状态词中间。
        if not stack and index in status_spans:
            if current:
                segments.append(("".join(current), ""))
                current = []
            state = ""
            while index in status_spans:
                index, state = status_spans[index]
            # 用新的状态标记"接下来这一段"的归属
            if state:
                segments.append(("", state))  # 占位段：仅用于传递状态
            continue

        if char in bracket_pairs:
            stack.append(bracket_pairs[char])
        elif char in closers and stack and stack[-1] == char:
            stack.pop()

        if char in delimiters and not stack:
            if current:
                segments.append(("".join(current), ""))
                current = []
            index += 1
            continue

        current.append(char)
        index += 1

    if current:
        segments.append(("".join(current), ""))

    return segments


def _split_outside_brackets(text: str) -> list[str]:
    """按分隔符切分文本（只返回内容，忽略状态区段）。

    供需要"纯内容列表"的场景使用；需要归属信息请用 :func:`_split_with_status`。
    """
    return [content for content, _state in _split_with_status(text) if content]


#: 括号内的「纯数量」写法（这种才值得留在标签里；空括号或无意义内容要剥掉）
_BRACKET_CONTENT_RE = re.compile(r"^[（(\[【]\s*(.*?)\s*[）)\]}】]$")

#: 判定「括号内容有没有实际意义」：含中文/字母/数字才算
_MEANINGFUL_RE = re.compile(r"[\u4e00-\u9fffA-Za-z0-9]")

#: 空括号（只有空白/标点）——要连括号一起剥掉
_EMPTY_BRACKET_RE = re.compile(r"[（(\[【]\s*[）)\]}】]")

#: 一个合法的括号组
_BRACKET_GROUP_RE = re.compile(r"[（(\[【][^（()）\[\]【】]*[）)\]}】]")


def _tidy_bracket(item: str) -> str:
    """清理标签里的括号。

    处理三类脏数据：
        1. 空括号「番茄（ ）」→ 连括号剥掉，得到「番茄」
        2. 重复/畸形括号「番茄（1个）（瓶装」→ 只保留第一个合法括号组
        3. 括号内容有意义「排骨（2-3个）」→ 原样保留（数量信息对用户有用）
    """
    text = _EMPTY_BRACKET_RE.sub("", item).strip()

    groups = _BRACKET_GROUP_RE.findall(text)
    if not groups:
        # 没有任何合法括号组：把残缺的括号字符清掉（例如结尾孤零零的「（」）
        return text.strip("（）()[]【】 ").strip()

    first = groups[0]
    name = text[: text.index(first)].strip()
    tail = text[text.index(first) + len(first) :].strip()
    # 括号组后面还有残留内容（基本是脏数据，如「（瓶装」），丢弃
    if tail and not _MEANINGFUL_RE.sub("", tail).strip():
        tail = ""
    return f"{name}{first}{tail}".strip() if not tail else f"{name}{first}".strip()


def _is_renderable_ingredient(item: str) -> bool:
    """判断一个食材标签值不值得渲染。

    ⚠️ 这是给「脏数据」兜的第二道防线（前端渲染规则见 README 的视觉规范）：
        空字符串、纯符号、残缺括号（如 `（`、`（瓶装`、`）`）一律丢弃。

    返回 False 的条目会被**直接过滤，界面完全不渲染**——不留空占位、不留灰条。
    这样即使模型输出格式再离谱，用户也不会看到「（ ）」这种空壳标签。

    允许的形态：
        - `排骨`                     纯名字
        - `排骨（2-3个）`            名字 + 一个合法括号组
        - `生抽/少许老抽`            名字里带斜杠
    拒绝的形态：
        - 空串、纯标点、纯 emoji
        - 括号不成对（`（`、`（瓶装`、`）`）
        - 出现两组及以上括号（畸形）
        - 剥掉括号后没有实体名（`（2-3个）`）
    """
    text = item.strip()
    if not text or not _MEANINGFUL_RE.search(text):
        return False

    open_count = sum(text.count(ch) for ch in "（(【[")
    close_count = sum(text.count(ch) for ch in "）)】]")
    if open_count != close_count:
        return False

    groups = _BRACKET_GROUP_RE.findall(text)
    if len(groups) > 1:
        return False
    if any(not _MEANINGFUL_RE.search(g[1:-1]) for g in groups):
        return False

    # 剥掉合法括号组后必须还有实体名
    remainder = text
    for group in groups:
        remainder = remainder.replace(group, "", 1)
    if not _MEANINGFUL_RE.search(remainder):
        return False

    # 括号只能出现在末尾（「番茄（1个）」合法；「（1个）番茄」不合法）
    if groups and not text.rstrip().endswith(tuple("）)】]")):
        return False
    return True


def _parse_ingredients(body: str) -> tuple[list[str], list[str]]:
    """从正文里切出「已有食材」与「需另买食材」。

    模型常见的两种写法：

        ``- 🧺 **需要食材**：✅ 已有：西红柿、鸡蛋　❓ 需另买：玉米淀粉``
        ``- 🧺 **需要食材**：排骨（2-3个）、番茄（1个）、姜``

    注意全角空格（``　``）会被模型当作分隔符使用，所以先统一成半角空格再切。
    括号内的分隔符不会切断（见 _split_outside_brackets）。
    """
    matched = _ING_RE.search(body)
    if not matched:
        return [], []

    raw = matched.group(1).replace("　", " ")
    have: list[str] = []
    missing: list[str] = []

    # 第二道兜底：模型偶尔会把「`排骨（2-3个）`」拆成两条（`排骨` + `（2-3个）`）。
    # 遇到以括号开头的片段，**合并回上一条**，恢复成完整标签，
    # 而不是丢掉（丢了会少信息）或原样渲染（就是用户截图里的脏标签）。
    # 注意：合并时要**保留该片段的状态**，否则归属会丢。
    merged: list[tuple[str, str]] = []
    for piece, section in _split_with_status(raw):
        cleaned_piece = clean_md(piece).strip()
        if cleaned_piece.startswith(("（", "(", "【", "[")) and merged:
            prev_text, prev_section = merged[-1]
            combined = f"{prev_text}{cleaned_piece}"
            # 只有当合并结果**本身是干净的标签**时才合并；
            # 否则（例如原始文本里括号没闭合，导致残渣被一起带进来）
            # 只保留前一个完整标签，丢弃残渣——宁可少一个脏标签，也不要渲染出
            # 「番茄（ ）（瓶装」这种畸形内容。
            if prev_text and _is_renderable_ingredient(combined):
                merged[-1] = (combined, prev_section or section)
            elif not prev_text:
                merged[-1] = (combined, prev_section or section)
            continue
        merged.append((piece, section))

    # 归属判定：状态区段由切分器随片段一起返回（见 _split_with_status）。
    # ⚠️ 踩过的坑：早期想看"片段自己有没有带状态词"来判断，
    #    但状态词在切分时已经被剥掉（否则会被当成食材），于是永远搜不到，
    #    结果「❓ 需另买：姜、冰糖」里的姜和冰糖都被算成了"已有"。
    in_missing_section = False

    for piece, section in merged:
        if section == "missing":
            in_missing_section = True
        elif section == "have":
            in_missing_section = False
        if not piece:
            continue  # 占位段，只用来传递状态
        item = clean_md(piece)
        item = _ING_BULLET_RE.sub("", item).strip()
        item = _ING_PREFIX_RE.sub("", item).strip()
        item = item.strip("：: ").strip()
        item = _tidy_bracket(item).strip()
        if not item:
            continue
        if not _is_renderable_ingredient(item):
            # 脏数据（空标签/纯符号/残缺括号）直接丢弃，界面不渲染
            logger.debug("丢弃无法渲染的食材标签：%r", item)
            continue

        (missing if in_missing_section else have).append(item)

    return have, missing


def _parse_stars(body: str) -> int | None:
    """解析难度星级，兼容星号写法与「3星」写法。"""
    matched = _STARS_RE.search(body)
    if matched:
        return max(1, min(len(matched.group(1)), 5))
    matched = _STARS_NUM_RE.search(body)
    if matched:
        return int(matched.group(1))
    return None


def parse_recipes_from_text(text: str) -> list[RecipeCard]:
    """从管家的 Markdown 回答里解析出菜谱卡片。

    Args:
        text: 模型回复原文。

    Returns:
        菜谱卡片列表。没有任何可用菜谱时返回**空列表**（界面需自行退回纯文本展示）。

    Examples:
        >>> parse_recipes_from_text("### 1. 西红柿炒鸡蛋\\n- 🥗 **营养评级**：A")[0].name
        '西红柿炒鸡蛋'
    """
    if not text or not text.strip():
        return []

    cards: list[RecipeCard] = []
    for name, body in _split_blocks(text):
        grade_match = _GRADE_RE.search(body)
        link_match = _LINK_RE.search(body)
        time_match = _TIME_RE.search(body)
        have, missing = _parse_ingredients(body)

        card = RecipeCard(
            name=name,
            grade=grade_match.group(1).upper() if grade_match else None,
            stars=_parse_stars(body),
            minutes=int(time_match.group(1)) if time_match else None,
            ingredients=have,
            missing=missing,
            link=link_match.group(2) if link_match else None,
            link_text=clean_md(link_match.group(1)) if link_match else None,
        )

        # 至少要有一项「卡片特征」才算菜谱，否则可能只是普通编号列表
        if card.grade or card.stars or card.link:
            cards.append(card)

    return cards


# --------------------------------------------------------------------------- #
# 渲染（HTML 片段，供 Streamlit st.markdown(unsafe_allow_html=True) 使用）
# --------------------------------------------------------------------------- #

_GRADE_CLASS = {"A": "chip-grade-A", "B": "chip-grade-B", "C": "chip-grade-C"}


def render_recipe_card_html(card: RecipeCard) -> str:
    """把菜谱卡片渲染成 HTML 片段。

    样式类名与 app.py 里的 CSS 对应（chip / chip-grade-* / ing-chip 等）。
    """
    chips: list[str] = []

    grade_class = _GRADE_CLASS.get(card.grade or "", "chip-plain")
    if card.grade:
        chips.append(f'<span class="chip {grade_class}">🥗 {card.grade_label}</span>')
    if card.stars:
        chips.append(f'<span class="chip chip-plain">🔥 难度 {card.stars_text}</span>')
    if card.minutes:
        chips.append(f'<span class="chip chip-plain">⏱ 约 {card.minutes} 分钟</span>')
    if card.missing:
        chips.append('<span class="chip chip-plain">🛒 还需另买</span>')
    else:
        chips.append('<span class="chip chip-ready">✅ 食材齐全，马上能做</span>')

    html = ['<div class="recipe-card">', f"<h4>{card.name}</h4>", '<div class="recipe-meta">']
    html.extend(chips)
    html.append("</div>")

    # ---------- 食材标签区 ----------
    # 布局规范（与 app.py 的 .ing-list / .ing-chip 对应）：
    #   • 用 flex + gap 而不是 margin —— 换行后左右间距均匀，不会出现"行尾多余空隙"
    #   • 空分组**整块不渲染** —— 过滤掉脏标签后不会留下空行或占位灰条
    #   • 「已有」与「需另买」分成两行，各自带小标题，避免两种语义混在一个流里
    if card.ingredients:
        items = "".join(f'<span class="ing-chip">{item}</span>' for item in card.ingredients[:14])
        html.append(f'<div class="ing-row"><span class="ing-row-label">已有</span><div class="ing-list">{items}</div></div>')
    if card.missing:
        items = "".join(f'<span class="ing-chip missing">{item}</span>' for item in card.missing[:10])
        html.append(
            f'<div class="ing-row"><span class="ing-row-label">需另买</span>'
            f'<div class="ing-list">{items}</div></div>'
        )
    if card.link:
        text = card.link_text or "查看原菜谱"
        html.append(
            f'<div class="recipe-source"><a href="{card.link}" target="_blank">🔗 {text} ↗</a></div>'
        )

    html.append("</div>")
    return "".join(html)

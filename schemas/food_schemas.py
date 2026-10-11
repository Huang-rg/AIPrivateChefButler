"""Pydantic 数据模型 —— 让大模型「吐出规整数据」而不是一段散文。

================================================================================
【这个文件干什么】
    定义食材、食谱、推荐清单三类结构，并负责：
        1. 校验：模型输出必须在合法范围内（星级 1~5、营养评级 A/B/C、链接必须是 http）
        2. 打分排序：按「营养价值 + 制作难度」算出综合分并排序
        3. 降级容错：模型偶尔会吐出 ``4星`` / ``A级`` / 缺字段，自动洗干净

【为什么非要结构化输出】
    如果让模型自由发挥，得到的是「这道菜挺有营养的，难度中等偏上」这种人话——
    界面没法把它渲染成星级条，也没法排序。结构化之后，每个字段都能直接画控件。

【三个模型】
    Ingredient   单个食材：名称、数量估计、新鲜度、是否要另买
    Recipe       单个食谱：菜名、营养评级、难度星级、耗时、食材、步骤、来源链接
    RecipeList   一次推荐的清单（含排序结果与创意建议兜底）

【排序规则（可改）】
    综合分 = 营养价值分 + 难度易做分，各占 50 分：
        营养价值：A=50、B=35、C=20
        制作难度：1星=50、2星=40、3星=30、4星=15、5星=5
    另外：食材已齐的菜额外 +5 分（更容易立刻做），总分最高 105。
    想改口味偏好，直接改下面的 NUTRITION_SCORES / DIFFICULTY_SCORES。
================================================================================
"""

from __future__ import annotations

import logging
import re
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

logger = logging.getLogger(__name__)

__all__ = [
    "DIFFICULTY_SCORES",
    "NUTRITION_SCORES",
    "Freshness",
    "Ingredient",
    "NutritionGrade",
    "Recipe",
    "RecipeList",
    "RecipeRecommendation",
    "SortPreference",
    "clean_difficulty",
    "clean_nutrition_grade",
    "parse_json_object",
    "recommend_recipes",
    "sort_recipes",
]


# ==============================================================================
# 枚举
# ==============================================================================

NutritionGrade = Literal["A", "B", "C"]
"""营养评级：A=营养均衡、B=中等、C=偏油偏咸。"""

Freshness = Literal["新鲜", "一般", "需尽快食用", "未知"]
"""食材新鲜度。"""

SortPreference = Literal["综合", "营养优先", "简单优先"]
"""排序偏好，界面上的一个下拉框。"""


#: 营养评级 -> 得分（满分 50）
NUTRITION_SCORES: Final[dict[str, int]] = {"A": 50, "B": 35, "C": 20}

#: 难度星级 -> 得分（满分 50，越容易越高）
DIFFICULTY_SCORES: Final[dict[int, int]] = {1: 50, 2: 40, 3: 30, 4: 15, 5: 5}


# ==============================================================================
# 清洗函数：模型输出经常「不太规范」，这里统一擦干净
# ==============================================================================

#: 中文数字 -> 阿拉伯数字，用于解析「三星」「一星半」这类写法
_CN_NUMBERS: Final[dict[str, int]] = {
    "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
    "1": 1, "2": 2, "3": 3, "4": 4, "5": 5,
}


def clean_nutrition_grade(value: Any) -> str:
    """把各种写法的营养评级统一成 A / B / C。

    能处理：``"A"`` / ``"a级"`` / ``"营养评级：B"`` / ``"B（蛋白质丰富）"`` / 无法识别 → ``"B"``。

    为什么默认给 B：评级拿不准时给中间值，比硬说"A 很健康"更负责。
    """
    if value is None:
        return "B"
    text = str(value).strip().upper()
    match = re.search(r"\b([ABC])\b", text)
    if match:
        return match.group(1)
    match = re.search(r"([ABC])", text)
    if match:
        return match.group(1)
    logger.debug("无法解析营养评级 %r，按 B 处理。", value)
    return "B"


def clean_difficulty(value: Any) -> int:
    """把各种写法统一成 1~5 的整数星级。

    能处理：``3`` / ``"3"`` / ``"⭐⭐⭐"`` / ``"三星"`` / ``"3星"`` / ``"简单"`` / 无法识别 → ``3``。

    为什么默认给 3：拿不准时按「中等难度」展示，不会给用户错误的"很容易"预期。
    """
    if isinstance(value, bool):
        return 3
    if isinstance(value, (int, float)):
        return max(1, min(int(round(value)), 5))
    if value is None:
        return 3

    text = str(value).strip()
    if not text:
        return 3

    # 星号数量
    stars = text.count("⭐") + text.count("★") + text.count("*")
    if stars:
        return max(1, min(stars, 5))

    # 阿拉伯数字
    match = re.search(r"([1-5])", text)
    if match:
        return int(match.group(1))

    # 中文数字
    for word, number in _CN_NUMBERS.items():
        if word in text and word not in ("1", "2", "3", "4", "5"):
            return number

    # 文字描述
    if any(word in text for word in ("零失败", "新手", "非常简单", "很简单", "简单", "容易", "快手")):
        return 1
    if any(word in text for word in ("中等", "一般", "普通")):
        return 3
    if any(word in text for word in ("复杂", "困难", "较难", "很难", "有技巧")):
        return 4

    logger.debug("无法解析难度 %r，按 3 星处理。", value)
    return 3


def parse_json_object(text: str) -> dict[str, Any]:
    """从模型输出里抠出一个 JSON 对象（兼容 ```json 代码块与前后废话）。

    Args:
        text: 模型原始输出。

    Returns:
        解析出的字典；失败时返回空字典（不抛异常，交由调用方决定怎么办）。
    """
    import json

    if not text or not text.strip():
        return {}

    raw = text.strip()

    # 去掉 ```json ... ``` 包裹
    fence = re.search(r"```(?:json)?\s*(.*?)```", raw, re.DOTALL)
    if fence:
        raw = fence.group(1).strip()

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        # 退一步：截取第一个 { 到最后一个 } 之间的内容再试
        start, end = raw.find("{"), raw.rfind("}")
        if start == -1 or end <= start:
            logger.warning("模型输出里找不到 JSON 对象：%s", raw[:120])
            return {}
        try:
            data = json.loads(raw[start : end + 1])
        except json.JSONDecodeError as exc:
            logger.warning("解析模型 JSON 失败：%s（原文前 120 字：%s）", exc, raw[:120])
            return {}

    return data if isinstance(data, dict) else {}


# ==============================================================================
# 数据模型
# ==============================================================================


class Ingredient(BaseModel):
    """一种食材。"""

    model_config = ConfigDict(extra="ignore")

    name: str = Field(description="食材名称，例如「西红柿」。")
    quantity: str = Field(default="适量", description="大致份量，例如「2 个」「半颗」「一把」。")
    freshness: Freshness = Field(default="未知", description="新鲜度判断。")
    needed: bool = Field(default=True, description="是否还需要另买：True=家里没有/不够，False=已有。")
    note: str = Field(default="", description="备注，例如「看着有点蔫，建议先吃」。")

    @field_validator("name", mode="before")
    @classmethod
    def _clean_name(cls, value: Any) -> str:
        return str(value or "").strip()

    @field_validator("quantity", mode="before")
    @classmethod
    def _clean_quantity(cls, value: Any) -> str:
        text = str(value or "").strip()
        return text or "适量"

    @field_validator("freshness", mode="before")
    @classmethod
    def _clean_freshness(cls, value: Any) -> str:
        text = str(value or "").strip()
        for option in ("新鲜", "一般", "需尽快食用"):
            if option in text:
                return option
        return "未知"

    @field_validator("needed", mode="before")
    @classmethod
    def _clean_needed(cls, value: Any) -> bool:
        """模型可能给「已有 / 需另买 / 是 / 否」等写法。"""
        if isinstance(value, bool):
            return value
        text = str(value or "").strip()
        if any(word in text for word in ("已有", "家里有", "现有", "不需要", "不用买", "否", "false", "no")):
            return False
        if any(word in text for word in ("需另买", "要买", "需要买", "缺少", "是", "true", "yes")):
            return True
        return True

    @property
    def label(self) -> str:
        """界面上显示用的一行文本，例如「西红柿（2 个）」。"""
        return f"{self.name}（{self.quantity}）" if self.quantity else self.name


class Recipe(BaseModel):
    """一个食谱。"""

    model_config = ConfigDict(extra="ignore")

    name: str = Field(description="菜名，例如「西红柿炒鸡蛋」。")
    nutrition_grade: NutritionGrade = Field(description="营养评级 A/B/C。")
    nutrition_note: str = Field(default="", description="营养评级的一句话理由。")
    difficulty: int = Field(description="制作难度星级 1~5，1 最容易。")
    difficulty_note: str = Field(default="", description="难度的一句话说明。")
    time_minutes: int | None = Field(default=None, description="大致耗时（分钟）。")
    ingredients: list[str] = Field(default_factory=list, description="需要的食材清单。")
    missing_ingredients: list[str] = Field(default_factory=list, description="你还缺、需要另买的食材。")
    steps: list[str] = Field(default_factory=list, description="关键步骤，3~6 步。")
    source_title: str = Field(default="", description="来源网站名，例如「下厨房」。")
    source_url: str = Field(default="", description="来源链接（必须是真实链接，不能编造）。")
    summary: str = Field(default="", description="一句话推荐理由。")

    # ------------------------------ 字段清洗 ------------------------------ #

    @field_validator("nutrition_grade", mode="before")
    @classmethod
    def _clean_grade(cls, value: Any) -> str:
        return clean_nutrition_grade(value)

    @field_validator("difficulty", mode="before")
    @classmethod
    def _clean_difficulty(cls, value: Any) -> int:
        return clean_difficulty(value)

    @field_validator("time_minutes", mode="before")
    @classmethod
    def _clean_time(cls, value: Any) -> int | None:
        if value is None or isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            return int(value) if value > 0 else None
        match = re.search(r"(\d+)", str(value))
        return int(match.group(1)) if match else None

    @field_validator("ingredients", "missing_ingredients", "steps", mode="before")
    @classmethod
    def _clean_str_list(cls, value: Any) -> list[str]:
        """模型的列表字段可能是字符串、字典列表或带序号的文本。"""
        if value is None:
            return []
        if isinstance(value, str):
            parts = re.split(r"[\n；;]+", value)
        elif isinstance(value, (list, tuple, set)):
            parts = list(value)
        else:
            parts = [value]

        cleaned: list[str] = []
        for part in parts:
            if isinstance(part, dict):
                text = str(part.get("text") or part.get("step") or part.get("name") or "").strip()
            else:
                text = str(part).strip()
            # 依次剥掉列表符号与序号前缀，顺序很重要：
            # "- 3. 下西红柿" 要先去掉 "-"，再去掉 "3."，否则会漏掉序号。
            text = re.sub(r"^\s*[-*•·]\s*", "", text)
            text = re.sub(r"^\s*\d+\s*[.、)）:：]\s*", "", text)
            text = re.sub(r"^\s*第?\s*\d+\s*步\s*[:：]?\s*", "", text)
            if text:
                cleaned.append(text)
        return cleaned

    @field_validator("name", "source_title", "source_url", "summary", "nutrition_note", "difficulty_note", mode="before")
    @classmethod
    def _clean_text(cls, value: Any) -> str:
        return str(value or "").strip()

    @model_validator(mode="after")
    def _drop_fake_url(self) -> "Recipe":
        """拦掉模型编造的假链接（不是 http 开头就清空，避免误导用户点击）。"""
        if self.source_url and not self.source_url.lower().startswith(("http://", "https://")):
            logger.warning("菜谱 %s 的来源链接不像真实链接，已清空：%r", self.name, self.source_url)
            object.__setattr__(self, "source_url", "")
        return self

    # ------------------------------ 派生属性 ------------------------------ #

    @property
    def stars(self) -> str:
        """星级的可视化写法，例如 ``⭐⭐⭐``。"""
        return "⭐" * self.difficulty

    @property
    def nutrition_score(self) -> int:
        """营养价值得分（满分 50）。"""
        return NUTRITION_SCORES.get(self.nutrition_grade, 35)

    @property
    def difficulty_score(self) -> int:
        """难度易做分（满分 50）。"""
        return DIFFICULTY_SCORES.get(self.difficulty, 30)

    @property
    def easy_bonus(self) -> int:
        """食材齐全的加分（5 分）。"""
        return 5 if not self.missing_ingredients else 0

    @property
    def score(self) -> int:
        """综合分（满分 105）。"""
        return self.nutrition_score + self.difficulty_score + self.easy_bonus

    @property
    def time_text(self) -> str:
        """耗时的可读写法。"""
        return f"约 {self.time_minutes} 分钟" if self.time_minutes else "耗时不明"

    @property
    def is_ready(self) -> bool:
        """是否「立刻就能做」（不缺任何食材）。"""
        return not self.missing_ingredients


class RecipeRecommendation(BaseModel):
    """一次推荐的结果清单。"""

    model_config = ConfigDict(extra="ignore")

    identified_ingredients: list[Ingredient] = Field(
        default_factory=list, description="从图片中识别到的食材清单。"
    )
    recipes: list[Recipe] = Field(default_factory=list, description="推荐的菜谱列表。")
    creative_suggestions: list[str] = Field(
        default_factory=list, description="找不到合适菜谱时的创意搭配建议。"
    )
    summary: str = Field(default="", description="对这一餐的总体建议，一句话。")
    sort_preference: SortPreference = Field(default="综合", description="本次排序依据。")

    @field_validator("identified_ingredients", "recipes", mode="before")
    @classmethod
    def _coerce_models(cls, value: Any) -> list[Any]:
        if value is None:
            return []
        if isinstance(value, dict):
            return [value]
        return list(value) if isinstance(value, (list, tuple)) else [value]

    @field_validator("creative_suggestions", mode="before")
    @classmethod
    def _coerce_suggestions(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [line.strip() for line in re.split(r"[\n；;]+", value) if line.strip()]
        if isinstance(value, (list, tuple)):
            return [str(item).strip() for item in value if str(item).strip()]
        return [str(value)]

    @property
    def ingredient_names(self) -> list[str]:
        """食材名列表。"""
        return [item.name for item in self.identified_ingredients if item.name]

    @property
    def has_recipes(self) -> bool:
        """是否搜到了菜谱。"""
        return bool(self.recipes)

    @property
    def top_recipe(self) -> Recipe | None:
        """综合分最高的菜谱。"""
        return self.recipes[0] if self.recipes else None


# 兼容别名：早期草稿里叫 RecipeList，保留避免调用方踩空
RecipeList = RecipeRecommendation


# ==============================================================================
# 排序
# ==============================================================================


def sort_recipes(
    recipes: list[Recipe],
    *,
    preference: SortPreference = "综合",
) -> list[Recipe]:
    """按偏好给菜谱排序（返回新列表，不改原列表）。

    Args:
        recipes: 待排序的菜谱。
        preference:
            - ``"综合"``：营养价值 + 难度易做 + 食材齐全加分
            - ``"营养优先"``：先比营养分，再比综合分
            - ``"简单优先"``：先比易做分，再比综合分

    Returns:
        排序后的新列表。同分时保持原有顺序（稳定排序）。
    """
    if preference == "营养优先":
        key = lambda r: (r.nutrition_score, r.score, r.difficulty_score)  # noqa: E731
    elif preference == "简单优先":
        key = lambda r: (r.difficulty_score, r.score, r.nutrition_score)  # noqa: E731
    else:
        key = lambda r: (r.score, r.nutrition_score)  # noqa: E731
    return sorted(recipes, key=key, reverse=True)


def recommend_recipes(
    data: "dict[str, Any] | RecipeRecommendation | str",
    *,
    preference: SortPreference = "综合",
    limit: int | None = None,
) -> RecipeRecommendation:
    """把「模型输出」变成排好序的推荐清单。

    这是第四步对外的核心入口，也是界面最该调用的函数。

    Args:
        data: 模型输出，可以是 JSON 字符串、字典或已经构造好的模型。
        preference: 排序偏好。
        limit: 最多保留几条菜谱（None 表示全部）。

    Returns:
        :class:`RecipeRecommendation` 实例（已排序、已裁剪）。

    Raises:
        ValueError: 传入的内容完全无法解析成推荐清单。
    """
    if isinstance(data, RecipeRecommendation):
        result = data
    else:
        payload = parse_json_object(data) if isinstance(data, str) else dict(data or {})
        if not payload:
            raise ValueError("无法从模型输出中解析出推荐清单（不是合法 JSON 对象）。")
        # 只保留模型认识的字段，避免模型多塞字段导致校验失败
        allowed = set(RecipeRecommendation.model_fields)
        result = RecipeRecommendation(**{key: value for key, value in payload.items() if key in allowed})

    ordered = sort_recipes(result.recipes, preference=preference)
    if limit is not None and limit > 0:
        ordered = ordered[:limit]
    return result.model_copy(update={"recipes": ordered, "sort_preference": preference})


# ==============================================================================
# 打包成「给模型看的」JSON Schema（配合 with_structured_output 使用）
# ==============================================================================


def recommendation_json_schema() -> dict[str, Any]:
    """返回推荐清单的 JSON Schema，供需要手写提示词时嵌进指令里。"""
    return RecipeRecommendation.model_json_schema()

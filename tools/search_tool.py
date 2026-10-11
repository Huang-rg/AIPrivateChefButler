"""Tavily 搜索工具封装 —— AI私厨管家的「联网查菜谱」能力。

================================================================================
【这个文件干什么】
    把 Tavily 联网搜索能力包装成大模型可以直接调用的工具（Tool），共三个：

    1. search_recipes      ← 主工具：按食材搜真实中文菜谱
    2. search_creative_pairings ← 兜底：搜不到菜谱时，搜「食材搭配创意」
    3. get_search_tools    ← 供 core/agent.py 一次性拿到工具列表绑定给 Agent

【为什么要限定站点】
    实测（不限定站点搜「西红柿炒鸡蛋 做法 食谱」）前几条是百度百科和 YouTube 视频，
    这些对"怎么做菜"没什么用。加上 include_domains 限定为下厨房 / 美食杰 / 豆果美食
    等菜谱站后，返回的全是真实菜谱页，质量提升明显。

    ⚠️ include_domains 是**硬过滤**：万一日后这些站点在 Tavily 索引里消失，
    可能搜出 0 条。所以 search_recipes 内置了「限定站点搜不到 → 自动放宽为全网搜」
    的兜底逻辑，不会让用户看到空白。

【怎么自定义】
    环境变量（不用改代码）：
        AI_CHEF_RECIPE_SITES   英文逗号分隔的站点白名单，设为空字符串 = 不限站点
                               例：AI_CHEF_RECIPE_SITES=xiachufang.com,meishij.net
        AI_CHEF_TAVILY_MAX_RESULTS  每次搜几条（默认 5）
        AI_CHEF_TAVILY_DEPTH        basic / advanced / fast（默认 basic，省钱快）
        AI_CHEF_TAVILY_SITE_FALLBACK 限定站点搜不到时是否放宽全网（默认 1=放宽）

【红线】
    TAVILY_API_KEY 只从环境变量读取（由 core/llm_models.py 统一 load_dotenv），
    本文件不出现任何密钥明文，也不打印密钥。
================================================================================
"""

from __future__ import annotations

import logging
import os
from typing import Any, Final

logger = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_MAX_RESULTS",
    "DEFAULT_RECIPE_SITES",
    "TavilyConfigError",
    "create_creative_search_tool",
    "create_recipe_search_tool",
    "get_search_tools",
    "search_recipes",
    "search_creative_pairings",
    "tavily_available",
]


# --------------------------------------------------------------------------- #
# 默认配置
# --------------------------------------------------------------------------- #

#: 默认每次搜索返回的条数
DEFAULT_MAX_RESULTS: Final[int] = 5

#: 默认的中文菜谱站点白名单（按实测可用性排序）
DEFAULT_RECIPE_SITES: Final[tuple[str, ...]] = (
    "xiachufang.com",
    "meishij.net",
    "douguo.com",
    "xiangha.com",
    "msdsp.com",
)

#: 搜索深度
DEFAULT_SEARCH_DEPTH: Final[str] = "basic"

#: Tavily 官方搜索接口（降级通道直接打这个地址）
_TAVILY_ENDPOINT: Final[str] = "https://api.tavily.com/search"

#: 请求用的 User-Agent
_DEFAULT_USER_AGENT: Final[str] = "AIPrivateChefButler/1.0"


class TavilyConfigError(RuntimeError):
    """Tavily 配置缺失（没有 API Key）。消息为中文，可直接展示。"""


class SearchTransportError(RuntimeError):
    """搜索通道故障（网络、TLS、HTTP 状态异常）。

    与"正常但没搜到结果"是两件事，必须区分开——否则界面会把
    "搜索服务连不上"误报成"没搜到菜谱"。"""


# --------------------------------------------------------------------------- #
# 配置读取
# --------------------------------------------------------------------------- #


def _max_results() -> int:
    """每次搜索条数，默认 5，可用 AI_CHEF_TAVILY_MAX_RESULTS 覆盖。"""
    raw = (os.getenv("AI_CHEF_TAVILY_MAX_RESULTS") or "").strip()
    if not raw:
        return DEFAULT_MAX_RESULTS
    try:
        value = int(raw)
    except ValueError:
        logger.warning("AI_CHEF_TAVILY_MAX_RESULTS=%r 不是整数，改用默认 %d。", raw, DEFAULT_MAX_RESULTS)
        return DEFAULT_MAX_RESULTS
    return max(1, min(value, 10))  # Tavily 单次上限 20，这里限制在 10 以内控成本


def _search_depth() -> str:
    raw = (os.getenv("AI_CHEF_TAVILY_DEPTH") or "").strip().lower()
    if raw in ("basic", "advanced", "fast", "ultra-fast"):
        return raw
    return DEFAULT_SEARCH_DEPTH


def _recipe_sites() -> list[str]:
    """站点白名单；返回空列表表示不限站点。"""
    raw = os.getenv("AI_CHEF_RECIPE_SITES")
    if raw is None:
        return list(DEFAULT_RECIPE_SITES)
    raw = raw.strip()
    if not raw:  # 显式设为空字符串 = 关闭站点限制
        return []
    if raw.lower() in ("none", "all", "*"):
        return []
    return [item.strip().lower() for item in raw.split(",") if item.strip()]


def _site_fallback_enabled() -> bool:
    raw = (os.getenv("AI_CHEF_TAVILY_SITE_FALLBACK") or "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def tavily_available() -> bool:
    """Tavily 是否已配置（只看有没有 Key，不打印内容）。"""
    return bool((os.getenv("TAVILY_API_KEY") or "").strip())


def _require_api_key() -> str:
    key = (os.getenv("TAVILY_API_KEY") or "").strip()
    if not key:
        raise TavilyConfigError(
            "未配置 TAVILY_API_KEY（联网搜菜谱需要它）。请检查环境变量；"
            "若 .env 里已有该变量，请确认项目启动时已执行 load_dotenv。"
        )
    return key


# --------------------------------------------------------------------------- #
# 搜索实现（供工具与直接调用共用）
# --------------------------------------------------------------------------- #


def _search_via_langchain(query: str, *, include_domains: list[str] | None, max_results: int) -> dict[str, Any]:
    """首选路径：用 langchain_tavily 的 TavilySearch（依赖 httpx）。"""
    from langchain_tavily import TavilySearch

    kwargs: dict[str, Any] = {
        "max_results": max_results,
        "topic": "general",
        "search_depth": _search_depth(),
        "include_answer": False,
    }
    if include_domains:
        kwargs["include_domains"] = include_domains

    tool = TavilySearch(**kwargs)
    result = tool.invoke({"query": query})
    if isinstance(result, dict):
        return result
    return {"results": [], "raw": result}


def _http_post_json(url: str, payload: dict[str, Any], *, timeout: float = 25.0) -> dict[str, Any]:
    """用**标准库**发一个 HTTPS POST（不依赖 httpx）。

    为什么需要它（真实排查出来的问题）：
        在部分 Windows 环境下（例如开着 Clash 的 TUN 模式），``httpx`` 连接
        ``api.tavily.com`` 会在 TLS 阶段直接断开，稳定复现
        ``SSLError(SSLEOFError(8, '[SSL: UNEXPECTED_EOF_WHILE_READING]'))``；
        而**同一台机器、同一个请求体**改用标准库 ``ssl`` + ``socket`` 却稳定返回 200。
        这是客户端 TLS 行为差异（或中间盒干扰），不是 Tavily 服务的问题。
        所以这里用标准库作为降级路径，保证联网搜菜谱不因环境问题整体失效。

    Returns:
        解析后的 JSON 字典。

    Raises:
        SearchTransportError: 网络、TLS 或解析失败。
    """
    import http.client
    import json as _json
    import ssl
    import urllib.parse

    parsed = urllib.parse.urlparse(url)
    host = parsed.hostname or ""
    if not host:
        raise SearchTransportError(f"搜索地址不合法：{url}")

    body = _json.dumps(payload, ensure_ascii=False).encode("utf-8")
    context = ssl.create_default_context()
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"

    connection = None
    try:
        connection = http.client.HTTPSConnection(host, parsed.port or 443, timeout=timeout, context=context) if parsed.scheme == "https" else http.client.HTTPConnection(host, parsed.port or 80, timeout=timeout)
        connection.request(
            "POST",
            path,
            body=body,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Content-Length": str(len(body)),
                "User-Agent": _DEFAULT_USER_AGENT,
            },
        )
        response = connection.getresponse()
        raw = response.read()
    except Exception as exc:  # noqa: BLE001 - 统一转成可展示错误
        raise SearchTransportError(f"{type(exc).__name__}: {str(exc)[:200]}") from exc
    finally:
        if connection is not None:
            try:
                connection.close()
            except Exception:  # noqa: BLE001
                pass

    if response.status != 200:
        snippet = raw[:200].decode("utf-8", "replace")
        raise SearchTransportError(f"HTTP {response.status}：{snippet}")

    try:
        data = _json.loads(raw.decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise SearchTransportError(f"返回内容不是合法 JSON：{raw[:120]!r}") from exc
    return data if isinstance(data, dict) else {"results": []}


def _search_via_stdlib(query: str, *, include_domains: list[str] | None, max_results: int) -> dict[str, Any]:
    """降级路径：直接用标准库 POST Tavily 官方接口。"""
    api_key = _require_api_key()
    payload: dict[str, Any] = {
        "api_key": api_key,
        "query": query,
        "max_results": max_results,
        "search_depth": _search_depth(),
        "topic": "general",
        "include_answer": False,
    }
    if include_domains:
        payload["include_domains"] = include_domains
    return _http_post_json(_TAVILY_ENDPOINT, payload)


def _run_tavily(query: str, *, include_domains: list[str] | None, max_results: int) -> dict[str, Any]:
    """执行一次搜索：**优先用 langchain_tavily，失败自动降级到标准库**。

    ⚠️ 降级不是"静默兜底"：会在返回结构里写入 ``transport`` 与 ``transport_note``，
    并在日志里记一条 warning，这样"为什么走了降级"是可追溯的——
    等环境修好（例如关掉 Clash 的 TUN 模式）首选路径会自动恢复。
    """
    try:
        result = _search_via_langchain(query, include_domains=include_domains, max_results=max_results)
        if _looks_like_transport_failure(result):
            raise SearchTransportError(_extract_error_text(result) or "langchain_tavily 返回了空结果并附带错误")
        result.setdefault("transport", "langchain_tavily")
        return result
    except Exception as exc:  # noqa: BLE001 - 任何失败都尝试降级
        logger.warning(
            "首选搜索通道（langchain_tavily / httpx）失败，改用标准库通道：%s: %s",
            type(exc).__name__,
            str(exc)[:150],
        )
        fallback = _search_via_stdlib(query, include_domains=include_domains, max_results=max_results)
        fallback["transport"] = "stdlib"
        fallback["transport_note"] = (
            f"首选通道（langchain_tavily/httpx）不可用，已自动改用标准库通道。原因：{type(exc).__name__}: {str(exc)[:120]}"
        )
        return fallback


def _extract_error_text(result: dict[str, Any]) -> str:
    """从 Tavily 返回里取出错误描述（可能是字符串，也可能是异常对象）。"""
    raw = result.get("error")
    if raw is None:
        return ""
    return str(raw)


def _looks_like_transport_failure(result: dict[str, Any]) -> bool:
    """判断这次返回是不是"通道故障"（而不是"正常但没搜到"）。

    这个判断很关键——踩过的坑：
        TavilySearch 出错时返回的是 ``{"error": ...}``（没有 results）。
        旧代码只取 results，于是**错误被完全吞掉**，表现成"搜到 0 条结果"。
        后果是：① 触发无意义的"白名单放宽"二次搜索；
        ② 模型看不到任何错误，反复重试（实测搜了 6 次、耗时 68 秒）；
        ③ 最后告诉用户"没搜到菜谱"，而真相是"搜索服务连不上"。
    """
    if result.get("results"):
        return False
    return bool(result.get("error"))


def _normalize(result: dict[str, Any]) -> dict[str, Any]:
    """把 Tavily 原始返回裁剪成「模型好读、界面好展示」的精简结构。

    ⚠️ 这里**必须保留 error 与 transport 信息**，否则上层无法区分
    "搜到 0 条" 和 "通道故障"，会给出误导性的结论。
    """
    items: list[dict[str, Any]] = []
    for raw in result.get("results") or []:
        if not isinstance(raw, dict):
            continue
        items.append(
            {
                "title": (raw.get("title") or "").strip(),
                "url": (raw.get("url") or "").strip(),
                "content": (raw.get("content") or "").strip(),
                "score": raw.get("score"),
            }
        )

    normalized: dict[str, Any] = {
        "query": result.get("query", ""),
        "count": len(items),
        "results": items,
    }
    if result.get("error"):
        normalized["error"] = _extract_error_text(result)[:300]
    if result.get("transport"):
        normalized["transport"] = result["transport"]
    if result.get("transport_note"):
        normalized["transport_note"] = result["transport_note"]
    return normalized


def search_recipes(
    ingredients: str,
    *,
    max_results: int | None = None,
    include_domains: list[str] | None = None,
) -> dict[str, Any]:
    """按食材搜索真实中文菜谱（限定菜谱站点，搜不到自动放宽）。

    Args:
        ingredients: 食材描述，例如 ``"西红柿、鸡蛋、葱"``。
        max_results: 返回条数，默认取配置（5）。
        include_domains: 站点白名单；None 表示用配置里的默认白名单。

    Returns:
        ``{"query":..., "count":..., "results":[{title, url, content, score}], "site_filter":...}``

    Raises:
        TavilyConfigError: 没有配置 TAVILY_API_KEY。
    """
    _require_api_key()
    text = (ingredients or "").strip()
    if not text:
        return {"query": "", "count": 0, "results": [], "error": "食材为空，无法搜索。"}

    limit = max_results or _max_results()
    domains = _recipe_sites() if include_domains is None else include_domains
    query = f"{text} 做法 食谱 步骤"

    result = _normalize(_run_tavily(query, include_domains=domains or None, max_results=limit))
    result["site_filter"] = domains or "不限站点"

    # ⚠️ 通道故障时**不要**再放宽站点重搜：
    #    那次请求同样会失败，只是白白多花一次调用和时间（实测会拖到几十秒）。
    if result.get("error"):
        result["note"] = (
            "联网搜索通道本次不可用，因此没有菜谱结果。"
            "请检查网络或代理设置（例如代理软件的 TUN 模式）后重试。"
        )
        logger.warning("搜索通道故障，跳过站点放宽重试：%s", str(result.get("error"))[:150])
        return result

    # 硬过滤兜底：万一日后菜谱站不在索引里，自动放宽为全网搜，避免用户看到空白
    if result["count"] == 0 and domains and _site_fallback_enabled():
        logger.warning("限定站点 %s 未搜到结果，自动放宽为全网搜索。", domains)
        fallback = _normalize(_run_tavily(query, include_domains=None, max_results=limit))
        fallback["site_filter"] = "不限站点（原站点白名单未命中，已自动放宽）"
        if fallback.get("error"):
            fallback["note"] = "联网搜索通道本次不可用，因此没有菜谱结果。"
        else:
            fallback["note"] = "菜谱站白名单未搜到结果，已自动放宽为全网搜索。"
        return fallback

    return result


def search_creative_pairings(ingredients: str, *, max_results: int = 5) -> dict[str, Any]:
    """搜不到合适菜谱时的兜底：搜「食材搭配 / 创意做法」。

    Args:
        ingredients: 手头现有食材。
        max_results: 返回条数。

    Returns:
        与 :func:`search_recipes` 同结构的结果字典。
    """
    _require_api_key()
    text = (ingredients or "").strip()
    if not text:
        return {"query": "", "count": 0, "results": [], "error": "食材为空，无法搜索。"}

    query = f"{text} 搭配 创意做法 家常菜 灵感"
    result = _normalize(_run_tavily(query, include_domains=None, max_results=max_results))
    result["site_filter"] = "不限站点（创意建议模式）"
    return result


# --------------------------------------------------------------------------- #
# 包装成 Agent 可调用的工具
# --------------------------------------------------------------------------- #


def create_recipe_search_tool() -> Any:
    """创建「按食材搜菜谱」工具（供 Agent 绑定）。

    工具名固定为 ``search_recipes``；大模型会依据描述决定何时调用它。
    """
    from langchain_core.tools import StructuredTool
    from pydantic import BaseModel, Field

    class RecipeSearchInput(BaseModel):
        """search_recipes 的入参。"""

        ingredients: str = Field(
            description="要搜索的食材，用中文顿号或逗号分隔，例如「西红柿、鸡蛋、小葱」。"
            "尽量包含主要食材与数量线索（如「半颗白菜」）。"
        )
        max_results: int = Field(
            default=DEFAULT_MAX_RESULTS,
            ge=1,
            le=10,
            description="希望返回的菜谱条数，默认 5 条。",
        )

    def _search(ingredients: str, max_results: int = DEFAULT_MAX_RESULTS) -> dict[str, Any]:
        return search_recipes(ingredients, max_results=max_results)

    return StructuredTool.from_function(
        func=_search,
        name="search_recipes",
        description=(
            "根据识别出的食材，联网搜索真实的中文菜谱（含做法步骤）。"
            "当用户提供了食材、需要具体菜谱时，优先调用这个工具。"
            "输入应该是食材清单，不要输入完整的句子。"
        ),
        args_schema=RecipeSearchInput,
    )


def create_creative_search_tool() -> Any:
    """创建「创意搭配」工具（搜不到菜谱时使用）。"""
    from langchain_core.tools import StructuredTool
    from pydantic import BaseModel, Field

    class CreativeSearchInput(BaseModel):
        """search_creative_pairings 的入参。"""

        ingredients: str = Field(description="手头现有的食材，用中文顿号或逗号分隔。")
        max_results: int = Field(default=5, ge=1, le=10, description="返回条数，默认 5。")

    def _search(ingredients: str, max_results: int = 5) -> dict[str, Any]:
        return search_creative_pairings(ingredients, max_results=max_results)

    return StructuredTool.from_function(
        func=_search,
        name="search_creative_pairings",
        description=(
            "当 search_recipes 找不到合适菜谱时，用这个工具搜索食材搭配灵感与创意做法，"
            "以便基于现有食材给出创意建议。输入是食材清单。"
        ),
        args_schema=CreativeSearchInput,
    )


def get_search_tools() -> list[Any]:
    """返回绑给 Agent 的工具列表。

    目前包含：搜菜谱 + 创意搭配。
    Tavily 未配置时不抛异常，而是返回空列表并记一条警告，
    这样界面还能做「不联网」的纯模型对话。
    """
    if not tavily_available():
        logger.warning("未配置 TAVILY_API_KEY，本次不加载搜索工具（联网搜菜谱功能不可用）。")
        return []
    return [create_recipe_search_tool(), create_creative_search_tool()]

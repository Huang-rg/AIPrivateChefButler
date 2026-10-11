"""界面图片输入工具 —— 剪贴板读图、网络图片下载与缓存、URL 识别。

================================================================================
【这个文件解决什么问题】
    用户有三条把图片送进对话的路，之前只做了「上传」一条：

        ① 上传文件 / 摄像头拍照   —— 已有
        ② 从系统剪贴板读图        —— 本文件提供（截图后一键进来，不用先存盘）
        ③ 粘贴一个 http(s) 图片地址 —— 本文件提供（服务端下载，不依赖浏览器直连）

【为什么剪贴板要单独处理】
    图片在 **Windows 系统剪贴板**里，而页面跑在**浏览器**里，两者平时不通。
    所以用 Windows 原生 API 把图读出来（不需要装 Pillow），再交给界面预览。
    实现细节在 utils/image_handler.prepare_image_from_clipboard()。

【为什么要做「指纹」（哈希）】
    想实现「你刚复制了新图，页面自动发现」这种体验，就得知道剪贴板里的图
    变没变。做法是算图片内容的指纹（长度 + md5），两次指纹相同就不打扰你。
    指纹只对**图片**计算，不会读取你剪贴板里的文字/密码。

【为什么要做本地缓存】
    网络抖动时图片站可能连不上（你反馈过 bilibili 能看、食物图却显示不出来）。
    所以下载下来的图**存到本地缓存目录**，同一个 URL 第二次直接读本地，
    既快又不怕图站抽风。加载失败时返回明确的失败原因，界面显示占位块而不是空白。
================================================================================
"""

from __future__ import annotations

import hashlib
import logging
import re
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "CACHE_DIR",
    "ClipboardProbe",
    "ImageInputError",
    "cache_size_bytes",
    "cached_image_path",
    "clear_cache",
    "detect_image_url",
    "download_image_cached",
    "load_cached_image",
    "looks_like_image_url",
    "probe_clipboard_image",
    "read_clipboard_payload",
]

#: 图片缓存目录（放在项目内，方便查看与清理）
CACHE_DIR = Path(__file__).resolve().parents[1] / ".data" / "image_cache"

#: 从文本里识别图片 URL 的正则
_URL_RE = re.compile(r"https?://[^\s<>\"'）)】\]]+", re.IGNORECASE)

#: 看起来像图片的扩展名
_IMAGE_EXT_RE = re.compile(r"\.(?:jpe?g|png|webp|gif|bmp)(?:\?|#|$)", re.IGNORECASE)

#: 像图床/对象存储的主机名关键词（这些站的图片链接常常没有扩展名）
_IMAGE_HOST_RE = re.compile(
    r"(?:^|\.)(?:img|image|images|photo|photos|pic|pics|cdn|oss|cos|s3|qiniu|static)\.",
    re.IGNORECASE,
)


class ImageInputError(RuntimeError):
    """图片输入失败。消息为中文，可直接展示在界面上。"""


# --------------------------------------------------------------------------- #
# 剪贴板
# --------------------------------------------------------------------------- #


@dataclass
class ClipboardProbe:
    """剪贴板探测结果。

    Attributes:
        has_image: 剪贴板里当前是否有图片。
        fingerprint: 图片指纹（没有图片时为 None）。用于「是否是新图」的判断。
        description: 人类可读描述（例如 ``image/png｜24×18｜1.3 KB``）。
        error: 没有图片时的原因说明。
        payload: 成功读到的图片（ImagePayload）。
    """

    has_image: bool
    fingerprint: str | None = None
    description: str = ""
    error: str = ""
    payload: Any = None


def read_clipboard_payload() -> Any:
    """读取剪贴板图片，返回 ImagePayload。

    Raises:
        ImageInputError: 剪贴板里没有图片、被占用或格式不支持。
    """
    from utils.image_handler import ImageHandlerError, prepare_image_from_clipboard

    try:
        return prepare_image_from_clipboard()
    except ImageHandlerError as exc:
        raise ImageInputError(str(exc)) from exc


def probe_clipboard_image() -> ClipboardProbe:
    """探测剪贴板里有没有图片，并给出指纹（用于自动检测新图）。

    本函数**不抛异常**：没有图片是正常情况（用户可能复制的是文字）。
    """
    try:
        payload = read_clipboard_payload()
    except ImageInputError as exc:
        return ClipboardProbe(has_image=False, error=str(exc))
    except Exception as exc:  # noqa: BLE001 - 剪贴板被占用等意外情况
        return ClipboardProbe(has_image=False, error=f"{type(exc).__name__}: {str(exc)[:100]}")

    from utils.image_handler import describe_image

    raw = payload.raw_bytes or b""
    fingerprint = f"{len(raw)}:{hashlib.md5(raw).hexdigest()[:16]}"
    return ClipboardProbe(
        has_image=True,
        fingerprint=fingerprint,
        description=describe_image(payload),
        payload=payload,
    )


# --------------------------------------------------------------------------- #
# 网络图片：下载 + 本地缓存
# --------------------------------------------------------------------------- #


def detect_image_url(text: str) -> str | None:
    """从用户输入里识别出图片地址。

    判断规则（按顺序）：
        1. 整行就是一个 http(s) 链接：
           - 路径带图片扩展名（.jpg/.png/.webp/.gif/.bmp）→ 是图片
           - 主机名像图床/对象存储（img./cdn./oss./…）→ 是图片
           - 否则 → 交给 :func:`looks_like_image_url` 用服务端探测一次 Content-Type
             （这样短链图床也能认出来，而 bilibili 这类网页链接不会被误判）
        2. 文字里夹着**带图片扩展名**的链接 → 是图片
        3. 其余情况 → 返回 None，当普通文本处理

    Args:
        text: 用户输入。

    Returns:
        识别到的图片 URL；识别不到返回 None。
    """
    raw = (text or "").strip()
    if not raw:
        return None

    # 情况一：整行就是一个链接
    if raw.lower().startswith(("http://", "https://")) and " " not in raw:
        if _IMAGE_EXT_RE.search(raw):
            return raw
        host = urllib.parse.urlparse(raw).hostname or ""
        if _IMAGE_HOST_RE.search(host):
            return raw
        # 没有明显线索：用服务端探测一次（HEAD 很轻），确认真是图片才收下
        if looks_like_image_url(raw):
            return raw
        logger.info("链接不像图片，按普通文本处理：%s", raw[:60])
        return None

    # 情况二：文字里夹着明显的图片链接
    for candidate in _URL_RE.findall(raw):
        if _IMAGE_EXT_RE.search(candidate):
            return candidate
    return None


def looks_like_image_url(url: str, *, timeout: float = 6.0) -> bool:
    """用一次轻量请求判断这个地址是否真的是图片（看 Content-Type 或文件头）。

    用于「没有扩展名、主机名也不像图床」的短链，避免把普通网页当图片下载。
    判断顺序：
        1. Content-Type 是 ``image/*`` → 是图片
        2. Content-Type 明确是别的类型（如 text/html）→ 不是图片
        3. Content-Type 缺失或只是 ``application/octet-stream``（服务器没给线索）
           → 读前几个字节看**文件头魔数**，这才是最可靠的判断依据

    探测失败（网络不通等）时返回 False，宁可按文本处理。
    """
    import urllib.error
    import urllib.request

    def _magic_says_image() -> bool:
        from utils.image_handler import sniff_mime_type

        get_request = urllib.request.Request(url, headers={"User-Agent": _UA})
        with urllib.request.urlopen(get_request, timeout=timeout) as get_response:
            head = get_response.read(64)
        return sniff_mime_type(head) is not None

    _UA = "AIPrivateChefButler/1.0"

    try:
        request = urllib.request.Request(url, method="HEAD", headers={"User-Agent": _UA})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            content_type = (response.headers.get("Content-Type") or "").split(";")[0].strip().lower()

        if content_type.startswith("image/"):
            return True
        if content_type and content_type != "application/octet-stream":
            return False
        # 服务器没给明确线索 → 用文件头魔数定论
        return _magic_says_image()
    except urllib.error.HTTPError as exc:
        # 有些服务器不支持 HEAD（405），退回 GET 看魔数
        if exc.code in (403, 405, 501):
            try:
                return _magic_says_image()
            except Exception:  # noqa: BLE001
                return False
        return False
    except Exception as exc:  # noqa: BLE001 - 探测失败就按文本处理
        logger.info("图片地址探测失败（按文本处理）：%s（%s）", url[:60], type(exc).__name__)
        return False


def cached_image_path(url: str) -> Path:
    """按 URL 算出缓存文件路径。"""
    digest = hashlib.md5(url.encode("utf-8")).hexdigest()
    return CACHE_DIR / f"{digest}.img"


def load_cached_image(url: str) -> Any | None:
    """尝试从缓存读取图片。

    ⚠️ 严格校验：缓存文件的**内容**必须真的是图片（按文件头魔数判断）。
    踩过的坑：URL 里带 `.png` 就以为内容是 PNG，结果一个 19 字节的垃圾文件被
    当成 PNG 收下，还解析出宽 6384748 像素的荒唐尺寸。

    Returns:
        ImagePayload；没有缓存或缓存损坏时返回 None（损坏的缓存文件会被删掉）。
    """
    path = cached_image_path(url)
    if not path.exists():
        return None

    try:
        data = path.read_bytes()
        from utils.image_handler import sniff_mime_type

        if sniff_mime_type(data) is None:
            raise ValueError("文件内容不是已知的图片格式（魔数不匹配）")

        from utils.image_handler import prepare_from_bytes

        return prepare_from_bytes(data, origin=url, source_kind="cache")
    except Exception as exc:  # noqa: BLE001 - 缓存坏了就当没有，重新下载
        logger.info("缓存文件不可用，将重新下载：%s（%s）", path.name, exc)
        try:
            path.unlink()
        except OSError:
            pass
        return None


def download_image_cached(url: str, *, use_cache: bool = True, allow_private_hosts: bool = False) -> Any:
    """下载网络图片并写入本地缓存。

    Args:
        url: 图片地址。
        use_cache: 命中缓存时是否直接返回缓存内容。
        allow_private_hosts: 仅本机自测用；线上保持 False 以防 SSRF。

    Returns:
        ImagePayload。

    Raises:
        ImageInputError: 下载失败、不是图片、或文件过大。
    """
    from utils.image_handler import ImageHandlerError, describe_image, prepare_image_from_url

    if use_cache:
        cached = load_cached_image(url)
        if cached is not None:
            logger.info("命中图片缓存：%s（%s）", url[:60], describe_image(cached))
            return cached

    try:
        payload = prepare_image_from_url(url, allow_private_hosts=allow_private_hosts)
    except ImageHandlerError as exc:
        raise ImageInputError(str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise ImageInputError(f"下载图片失败：{type(exc).__name__}: {str(exc)[:150]}") from exc

    # 写入缓存（失败不影响本次使用）
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cached_image_path(url).write_bytes(payload.raw_bytes or b"")
    except OSError as exc:
        logger.info("写入图片缓存失败（不影响使用）：%s", exc)

    return payload


def cache_size_bytes() -> int:
    """图片缓存占用的总字节数。"""
    if not CACHE_DIR.exists():
        return 0
    total = 0
    for item in CACHE_DIR.glob("*.img"):
        try:
            total += item.stat().st_size
        except OSError:
            continue
    return total


def clear_cache() -> int:
    """清空图片缓存，返回删除的文件数。"""
    if not CACHE_DIR.exists():
        return 0
    count = 0
    for item in CACHE_DIR.glob("*.img"):
        try:
            item.unlink()
            count += 1
        except OSError:
            continue
    return count

"""图片处理模块：本地图片 / 网络 URL 图片 / 剪贴板图片 -> Base64 / DataURL。

【本文件职责】
    把「任何来源的一张图片」统一转换成多模态大模型能吃的格式（Base64 DataURL
    或公网 URL），是整个 AI 私厨管家「看图识食材」链路的第一环。

【三种来源】
    1. 本地文件：prepare_image_from_local(r"E:\\图片\\冰箱.jpg")
    2. 网络 URL：prepare_image_from_url("https://example.com/food.jpg")
    3. 剪贴板  ：prepare_image_from_clipboard()   ← 直接粘贴截图，无需存盘
    另有：Streamlit 上传对象 prepare_image_from_uploaded_file(uploaded_file)
         原始字节        prepare_from_bytes(data)
         内存对象        prepare_image_from_memory(任意带 read()/getvalue() 的对象)
    以及统一入口   prepare_image(source)  ← 自动判断来源，最省事

【设计要点（为什么这样写）】
    - **零第三方依赖**：剪贴板读取用 Windows 原生 API（ctypes），Pillow 只作为
      可选的兜底方案。这样在本项目「不装 Pillow 也能先跑通识别链路」。
    - **不猜后缀**：MIME 类型按「文件头魔数」判断，而不是靠后缀名。用户把
      .png 改名成 .jpg 也不会传错类型给模型。
    - **不硬编码路径**：.env 的路径由调用方（core/llm_models.py 等）通过
      python-dotenv 传入，本文件不出现任何 Key / DSN。
    - **错误可描述**：所有失败都抛 ImageHandlerError 并带上中文原因，
      Streamlit 界面可以直接把这个提示展示给用户。
"""

from __future__ import annotations

import base64
import binascii
import ctypes
import ipaddress
import logging
import mimetypes
import os
import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

logger = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_MAX_IMAGE_BYTES",
    "SUPPORTED_MIME_TYPES",
    "ImageHandlerError",
    "ImagePayload",
    "build_image_content_block",
    "build_human_message",
    "data_url_to_image_payload",
    "describe_image",
    "prepare_from_bytes",
    "prepare_image",
    "prepare_image_from_clipboard",
    "prepare_image_from_local",
    "prepare_image_from_memory",
    "prepare_image_from_uploaded_file",
    "prepare_image_from_url",
    "sniff_mime_type",
]


# --------------------------------------------------------------------------- #
# 常量
# --------------------------------------------------------------------------- #

#: 单张图片的最大字节数（默认 15 MB）。超限的多模态请求既慢又贵，而且多数
#: 模型对图片边长也有上限。可用环境变量 AI_CHEF_MAX_IMAGE_MB 覆盖。
DEFAULT_MAX_IMAGE_BYTES: Final[int] = 15 * 1024 * 1024

#: 兼容的图片格式。Qwen-VL / OpenAI 兼容接口稳定支持这几种。
SUPPORTED_MIME_TYPES: Final[frozenset[str]] = frozenset(
    {"image/jpeg", "image/png", "image/webp", "image/gif", "image/bmp"}
)

#: 扩展名 -> MIME 的兜底猜测（仅在文件头魔数无法判定时使用）
_EXTENSION_FALLBACK: Final[dict[str, str]] = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".jpe": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
}

#: 网络下载超时（秒）
_HTTP_TIMEOUT: Final[float] = 20.0

_DEFAULT_USER_AGENT: Final[str] = "AIPrivateChefButler/1.0 (+image_handler)"


class ImageHandlerError(RuntimeError):
    """图片读取/编码失败。消息为中文，可直接展示在 Web UI 上。"""


# --------------------------------------------------------------------------- #
# 数据结构
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ImagePayload:
    """一张已经处理好、可以直接喂给多模态模型的图片。

    Attributes:
        raw_bytes: 图片原始字节。URL 直传（prefer_base64=False）时为 None。
        mime_type: 标准 MIME 类型，例如 ``image/jpeg``。
        source_kind: 来源类别：``local`` / ``url`` / ``clipboard`` / ``bytes`` / ``upload``。
        origin: 人类可读的来源描述（文件绝对路径或 URL），用于界面显示与排错。
        width / height: 图片像素尺寸（能解析出来时才有值，否则为 None）。
        extra: 附加信息（例如剪贴板来源、下载 Content-Type 等）。
    """

    mime_type: str
    source_kind: str
    origin: str = ""
    raw_bytes: bytes | None = None
    width: int | None = None
    height: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    # ---------------------------- 便捷属性 ---------------------------- #

    @property
    def byte_size(self) -> int:
        """图片字节数（URL 直传时为 0）。"""
        return len(self.raw_bytes) if self.raw_bytes else 0

    @property
    def size_kb(self) -> float:
        """图片大小，单位 KB，保留 1 位小数。"""
        return round(self.byte_size / 1024, 1)

    @property
    def base64_str(self) -> str:
        """图片的 Base64 字符串（不含 DataURL 前缀）。"""
        self._require_bytes()
        return base64.b64encode(self.raw_bytes).decode("ascii")

    @property
    def data_url(self) -> str:
        """形如 ``data:image/jpeg;base64,xxxx`` 的 DataURL，可直接放进消息体。"""
        self._require_bytes()
        return f"data:{self.mime_type};base64,{self.base64_str}"

    def _require_bytes(self) -> None:
        if self.raw_bytes is None:
            raise ImageHandlerError(
                f"该图片是 URL 直传模式（origin={self.origin}），没有本地字节，"
                "无法生成 Base64。请用 prepare_image_from_url(url, prefer_base64=True) 重新获取。"
            )

    # ------------------------- 模型消息适配 ------------------------- #

    def to_content_block(self) -> dict[str, Any]:
        """转成 OpenAI 兼容的多模态 content block。

        返回形如::

            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,..."}}
        """
        if self.raw_bytes is None:
            url = self.origin
            if not url:
                raise ImageHandlerError("URL 直传模式缺少 origin，无法构建图片块。")
            return {"type": "image_url", "image_url": {"url": url}}
        return {"type": "image_url", "image_url": {"url": self.data_url}}


# --------------------------------------------------------------------------- #
# 格式探测
# --------------------------------------------------------------------------- #


def sniff_mime_type(data: bytes) -> str | None:
    """按文件头魔数识别图片 MIME 类型。

    Args:
        data: 图片字节内容（至少前 32 字节）。

    Returns:
        识别到的 MIME 类型；无法识别时返回 ``None``。
    """
    if len(data) < 12:
        return None
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"GIF87a") or data.startswith(b"GIF89a"):
        return "image/gif"
    if data.startswith(b"BM"):
        return "image/bmp"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _validate_mime(mime_type: str, origin: str) -> str:
    """确认 MIME 类型在支持列表内，否则抛出中文错误。"""
    normalized = (mime_type or "").split(";")[0].strip().lower()
    if normalized in SUPPORTED_MIME_TYPES:
        return normalized
    raise ImageHandlerError(
        f"不支持的图片格式：{normalized or '未知'}（来源：{origin}）。"
        f"请改用：{'、'.join(sorted(SUPPORTED_MIME_TYPES))}。"
        "提示：HEIC（iPhone 实拍）请先在手机或看图软件里导出为 JPG/PNG。"
    )


def _resolve_mime(data: bytes, origin: str, hint: str | None = None) -> str:
    """确定 MIME：魔数优先，其次调用方给的 hint，最后按扩展名猜。"""
    by_magic = sniff_mime_type(data)
    if by_magic:
        return by_magic
    if hint:
        guessed = (hint or "").split(";")[0].strip().lower()
        if guessed in SUPPORTED_MIME_TYPES:
            logger.debug("魔数无法识别，采用调用方 hint：%s", guessed)
            return guessed
    guessed_ext = _EXTENSION_FALLBACK.get(Path(origin).suffix.lower())
    if guessed_ext:
        logger.debug("魔数无法识别，按扩展名推断：%s", guessed_ext)
        return guessed_ext
    suffix = Path(origin).suffix.lower() or "无后缀"
    raise ImageHandlerError(
        f"无法识别图片格式（来源：{origin}）。该文件的内容不是已知的图片格式"
        f"（后缀 {suffix} 也不在支持范围内），文件可能已损坏，或它根本不是图片。"
    )


def _max_image_bytes() -> int:
    """读取允许的最大图片大小，可被环境变量 AI_CHEF_MAX_IMAGE_MB 覆盖。"""
    raw = os.getenv("AI_CHEF_MAX_IMAGE_MB", "").strip()
    if not raw:
        return DEFAULT_MAX_IMAGE_BYTES
    try:
        mb = float(raw)
    except ValueError:
        logger.warning("AI_CHEF_MAX_IMAGE_MB=%r 不是数字，改用默认 15MB。", raw)
        return DEFAULT_MAX_IMAGE_BYTES
    if mb <= 0:
        return DEFAULT_MAX_IMAGE_BYTES
    return int(mb * 1024 * 1024)


def _check_size(size: int, origin: str) -> None:
    limit = _max_image_bytes()
    if size <= 0:
        raise ImageHandlerError(f"图片内容为空（来源：{origin}），请换一张图片试试。")
    if size > limit:
        raise ImageHandlerError(
            f"图片过大：{size / 1024 / 1024:.1f} MB，超过上限 {limit / 1024 / 1024:.1f} MB"
            f"（来源：{origin}）。请压缩后再上传，或调大环境变量 AI_CHEF_MAX_IMAGE_MB。"
        )


# --------------------------------------------------------------------------- #
# 尺寸解析（仅解析文件头，不解码整张图，无需 Pillow）
# --------------------------------------------------------------------------- #


def _parse_dimensions(data: bytes, mime_type: str) -> tuple[int | None, int | None]:
    """从图片文件头解析宽高。失败时返回 (None, None)，不影响主流程。"""
    try:
        if mime_type == "image/png":
            return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
        if mime_type == "image/gif":
            return int.from_bytes(data[6:8], "little"), int.from_bytes(data[8:10], "little")
        if mime_type == "image/bmp":
            return int.from_bytes(data[18:22], "little"), int.from_bytes(data[22:26], "little")
        if mime_type == "image/jpeg":
            return _parse_jpeg_dimensions(data)
        if mime_type == "image/webp" and data[12:16] == b"VP8X":
            width = int.from_bytes(data[24:27], "little") + 1
            height = int.from_bytes(data[27:30], "little") + 1
            return width, height
    except Exception as exc:  # noqa: BLE001 - 尺寸只是锦上添花，绝不因此失败
        logger.debug("解析图片尺寸失败：%s", exc)
    return None, None


def _parse_jpeg_dimensions(data: bytes) -> tuple[int | None, int | None]:
    """扫描 JPEG 的 SOF 段读取宽高。"""
    index = 2  # 跳过 SOI(0xFFD8)
    total = len(data)
    while index + 9 < total:
        if data[index] != 0xFF:
            index += 1
            continue
        marker = data[index + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            index += 2
            continue
        segment_length = int.from_bytes(data[index + 2 : index + 4], "big")
        if segment_length < 2:
            break
        # SOF0..SOF15（排除 DHT=0xC4 / JPG=0xC8 / DAC=0xCC）
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            height = int.from_bytes(data[index + 5 : index + 7], "big")
            width = int.from_bytes(data[index + 7 : index + 9], "big")
            return width, height
        index += 2 + segment_length
    return None, None


# --------------------------------------------------------------------------- #
# 核心转换：字节 / 本地文件 / URL / 剪贴板 / 上传对象
# --------------------------------------------------------------------------- #


def prepare_from_bytes(
    data: bytes,
    *,
    origin: str = "<bytes>",
    mime_hint: str | None = None,
    source_kind: str = "bytes",
) -> ImagePayload:
    """把原始字节封装成 :class:`ImagePayload`。

    Args:
        data: 图片字节。
        origin: 来源描述，用于报错与界面显示。
        mime_hint: 魔数识别失败时的 MIME 提示。
        source_kind: 来源类别标签。

    Returns:
        ImagePayload 实例。
    """
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise ImageHandlerError(f"prepare_from_bytes 需要字节类型，收到 {type(data).__name__}。")
    raw = bytes(data)
    _check_size(len(raw), origin)
    mime_type = _resolve_mime(raw, origin, mime_hint)
    width, height = _parse_dimensions(raw, mime_type)
    return ImagePayload(
        mime_type=mime_type,
        source_kind=source_kind,
        origin=origin,
        raw_bytes=raw,
        width=width,
        height=height,
    )


def _normalize_local_path(path: "str | os.PathLike[str]") -> Path:
    """清洗用户给的路径：去首尾引号/空白、展开 %VAR%、转绝对路径。"""
    text = os.fspath(path).strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        text = text[1:-1]  # 粘贴路径时经常带上引号
    text = os.path.expandvars(text)
    return Path(text).expanduser().resolve()


def prepare_image_from_local(
    path: "str | os.PathLike[str]",
    *,
    max_bytes: int | None = None,
) -> ImagePayload:
    """读取本地图片文件并转成 :class:`ImagePayload`。

    Args:
        path: 本地图片路径（支持带引号、支持 ``%USERPROFILE%`` 这类变量）。
        max_bytes: 额外的字节上限；为 None 时用全局上限。

    Raises:
        ImageHandlerError: 文件不存在、不是文件、无权限、格式不支持或过大。
    """
    resolved = _normalize_local_path(path)

    if not resolved.exists():
        hint = ""
        if resolved.parent.exists():
            siblings = [
                item.name
                for item in resolved.parent.iterdir()
                if item.is_file() and item.suffix.lower() in _EXTENSION_FALLBACK
            ][:5]
            if siblings:
                hint = f" 该目录下的图片文件有：{'、'.join(siblings)}。"
        raise ImageHandlerError(f"找不到本地图片：{resolved}。{hint}")

    if resolved.is_dir():
        raise ImageHandlerError(
            f"这是一个文件夹而不是图片文件：{resolved}。请指向具体的图片文件（如 xxx.jpg）。"
        )

    try:
        raw = resolved.read_bytes()
    except PermissionError as exc:
        raise ImageHandlerError(
            f"没有权限读取该文件：{resolved}。请检查文件是否被其他程序占用，或换个位置再试。"
        ) from exc
    except OSError as exc:
        raise ImageHandlerError(f"读取本地图片失败：{resolved}（{exc}）。") from exc

    if max_bytes is not None and len(raw) > max_bytes:
        raise ImageHandlerError(
            f"图片过大：{len(raw) / 1024 / 1024:.1f} MB，超过上限 {max_bytes / 1024 / 1024:.1f} MB"
            f"（来源：{resolved}）。"
        )

    payload = prepare_from_bytes(
        raw, origin=str(resolved), mime_hint=mimetypes.guess_type(resolved.name)[0], source_kind="local"
    )
    logger.info(
        "已读取本地图片：%s（%s，%.1f KB，%sx%s）",
        resolved,
        payload.mime_type,
        payload.size_kb,
        payload.width,
        payload.height,
    )
    return payload


def _assert_public_url(url: str, *, allow_private_hosts: bool = False) -> None:
    """拦掉明显不该由服务端去请求的 URL（file://、内网地址等）。

    Args:
        url: 待检查的地址。
        allow_private_hosts: 允许访问 127.0.0.1 这类内网/回环地址。仅用于
            本机自测（本地起 HTTP 服务），线上调用一律保持 False，防止
            「用户填个内网地址让服务器去请求」这类 SSRF 风险。
    """
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ImageHandlerError(f"只支持 http/https 图片地址，收到：{parsed.scheme or '空'}。")
    host = parsed.hostname
    if not host:
        raise ImageHandlerError(f"图片地址缺少主机名：{url}")
    if allow_private_hosts:
        return
    # 只对字面量 IP 做内网校验；域名解析交给运维层面的网络策略
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return
    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
        raise ImageHandlerError(f"出于安全考虑，不允许请求内网地址：{host}。")


def prepare_image_from_url(
    url: str,
    *,
    prefer_base64: bool = True,
    timeout: float = _HTTP_TIMEOUT,
    allow_private_hosts: bool = False,
) -> ImagePayload:
    """处理网络图片地址。

    Args:
        url: http/https 图片地址。
        prefer_base64: True（默认）→ 下载后转 Base64 DataURL，兼容所有模型；
            False → 直接把 URL 交给模型，省流量（部分厂商要求图片可公网访问）。
        timeout: 下载超时秒数。
        allow_private_hosts: 仅本机自测时置 True，允许请求 127.0.0.1 等内网地址。

    Raises:
        ImageHandlerError: 地址非法、网络不通、非图片内容或过大。
    """
    target = (url or "").strip()
    if not target:
        raise ImageHandlerError("图片地址为空。")

    if not prefer_base64:
        _assert_public_url(target, allow_private_hosts=allow_private_hosts)
        return ImagePayload(
            mime_type=mimetypes.guess_type(target)[0] or "image/jpeg",
            source_kind="url",
            origin=target,
            raw_bytes=None,
            extra={"mode": "url_passthrough"},
        )

    _assert_public_url(target, allow_private_hosts=allow_private_hosts)
    request = urllib.request.Request(
        target,
        headers={"User-Agent": _DEFAULT_USER_AGENT, "Accept": "image/*,*/*;q=0.8"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            declared_type = response.headers.get("Content-Type", "")
            raw = response.read(_max_image_bytes() + 1)
    except urllib.error.HTTPError as exc:
        raise ImageHandlerError(
            f"下载图片失败：服务器返回 HTTP {exc.code}（{target}）。请确认图片链接是否失效。"
        ) from exc
    except urllib.error.URLError as exc:
        raise ImageHandlerError(
            f"下载图片失败：网络不可达（{exc.reason}）。请检查网络或代理设置。"
        ) from exc
    except (socket.timeout, TimeoutError) as exc:
        raise ImageHandlerError(f"下载图片超时（{timeout:.0f} 秒）：{target}。") from exc
    except OSError as exc:
        raise ImageHandlerError(f"下载图片失败：{exc}（{target}）。") from exc

    declared_mime = (declared_type or "").split(";")[0].strip().lower()
    if declared_mime and declared_mime not in SUPPORTED_MIME_TYPES and not sniff_mime_type(raw):
        raise ImageHandlerError(
            f"该地址返回的不是图片（Content-Type: {declared_mime}）：{target}。"
            "常见原因：链接指向的是网页而不是图片文件。"
        )

    payload = prepare_from_bytes(
        raw,
        origin=target,
        mime_hint=declared_mime or mimetypes.guess_type(target)[0],
        source_kind="url",
    )
    logger.info("已下载网络图片：%s（%s，%.1f KB）", target, payload.mime_type, payload.size_kb)
    return payload


def _grab_clipboard_image_bytes() -> bytes:
    """用 Windows 原生剪贴板 API 取图，返回 BMP 字节。仅支持 Windows。"""
    if os.name != "nt":
        raise ImageHandlerError("读取剪贴板目前仅支持 Windows 系统。")

    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    CF_DIB = 8  # 与 CF_BITMAP 相比，DIB 能保留真实像素数据，且不依赖调色板句柄

    # ---- 函数签名（64 位下句柄必须声明为 c_void_p，否则会被截断成 32 位） ----
    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.OpenClipboard.restype = wintypes.BOOL
    user32.CloseClipboard.argtypes = []
    user32.CloseClipboard.restype = wintypes.BOOL
    user32.IsClipboardFormatAvailable.argtypes = [wintypes.UINT]
    user32.IsClipboardFormatAvailable.restype = wintypes.BOOL
    user32.GetClipboardData.argtypes = [wintypes.UINT]
    user32.GetClipboardData.restype = wintypes.HANDLE
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalUnlock.restype = wintypes.BOOL
    kernel32.GlobalSize.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalSize.restype = ctypes.c_size_t

    if not user32.OpenClipboard(None):
        raise ImageHandlerError(
            "无法打开剪贴板（可能被其他程序占用）。请稍等 1 秒重试，或直接上传图片文件。"
        )

    try:
        if not user32.IsClipboardFormatAvailable(CF_DIB):
            raise ImageHandlerError(
                "剪贴板里没有图片。请先截图（Win+Shift+S）或复制一张图片，再点「读取剪贴板」。"
            )
        handle = user32.GetClipboardData(CF_DIB)
        if not handle:
            raise ImageHandlerError("读取剪贴板图片数据失败（GetClipboardData 返回空）。")

        # CF_DIB 返回的是一块 HGLOBAL 内存，锁住后可直接按字节读出 DIB 数据，
        # 不需要创建位图句柄，也就没有 GDI 对象泄漏的风险。
        pointer = kernel32.GlobalLock(handle)
        if not pointer:
            raise ImageHandlerError("锁定剪贴板内存失败，无法读取图片数据。")
        try:
            size = kernel32.GlobalSize(handle)
            if not size:
                raise ImageHandlerError("剪贴板图片数据大小为 0。")
            dib = ctypes.string_at(pointer, size)
        finally:
            kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()

    dib = _trim_dib_padding(dib)
    dib_header_size = int.from_bytes(dib[0:4], "little")
    if dib_header_size < 12 or len(dib) < dib_header_size:
        raise ImageHandlerError("剪贴板图片数据格式异常，无法解析。")
    # CF_DIB 不含 14 字节 BMP 文件头，这里补上，让下游按标准 BMP 解析
    file_header = b"BM" + (14 + len(dib)).to_bytes(4, "little") + b"\x00\x00\x00\x00" + (14 + dib_header_size).to_bytes(4, "little")
    return file_header + dib


def _trim_dib_padding(dib: bytes) -> bytes:
    """部分程序（如微信截图工具）在 DIB 尾部补了 4 字节对齐位，这里按位深裁掉。"""
    if len(dib) < 40:
        return dib
    header_size = int.from_bytes(dib[0:4], "little")
    if header_size < 40:
        return dib
    bit_count = int.from_bytes(dib[14:16], "little")
    compression = int.from_bytes(dib[16:20], "little")
    width = int.from_bytes(dib[4:8], "little")
    height = abs(int.from_bytes(dib[8:12], "little"))
    if compression != 0 or bit_count not in (1, 4, 8, 16, 24, 32) or width <= 0 or height <= 0:
        return dib
    palette_count = 0 if bit_count > 8 else (1 << bit_count)
    palette_bytes = palette_count * 4
    stride = ((width * bit_count + 31) // 32) * 4
    expected = header_size + palette_bytes + stride * height
    if expected <= len(dib):
        return dib[:expected]
    return dib


def _grab_clipboard_image_bytes_pillow() -> bytes:
    """Pillow 兜底方案：原生 API 失败且环境里恰好装了 Pillow 时使用。"""
    try:
        from PIL import ImageGrab  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - 取决于是否安装 Pillow
        raise ImageHandlerError(
            "剪贴板读取失败，且当前环境没有 Pillow 作为兜底。"
            "请改用「上传图片文件」方式，或安装 Pillow 后重试。"
        ) from exc

    import io

    grabbed = ImageGrab.grabclipboard()
    if grabbed is None:
        raise ImageHandlerError("剪贴板里没有图片。请先截图或复制一张图片再试。")
    if isinstance(grabbed, list):  # 复制的是文件列表
        raise ImageHandlerError(
            "剪贴板里是文件路径而不是图片数据。请直接上传该图片文件，或复制图片本身。"
        )
    buffer = io.BytesIO()
    grabbed.convert("RGB").save(buffer, format="PNG")
    return buffer.getvalue()


def prepare_image_from_clipboard(*, source_kind: str = "clipboard") -> ImagePayload:
    """读取剪贴板中的图片（截图后直接粘贴，不必先存盘）。

    Returns:
        ImagePayload 实例。

    Raises:
        ImageHandlerError: 剪贴板为空、被占用或平台不支持。
    """
    origin = "剪贴板图片"
    try:
        raw = _grab_clipboard_image_bytes()
    except ImageHandlerError as native_error:
        logger.debug("原生剪贴板读取失败，尝试 Pillow 兜底：%s", native_error)
        try:
            raw = _grab_clipboard_image_bytes_pillow()
        except ImageHandlerError:
            raise native_error from None

    payload = prepare_from_bytes(raw, origin=origin, mime_hint="image/bmp", source_kind=source_kind)
    logger.info("已读取剪贴板图片（%s，%.1f KB）", payload.mime_type, payload.size_kb)
    return payload


def prepare_image_from_memory(obj: Any) -> ImagePayload:
    """处理内存中的图片对象：文件流、BytesIO、或 Streamlit 的 UploadedFile。

    优先使用 ``getvalue()``（不改变读指针），其次 ``getbuffer()``，最后 ``read()``。
    """
    origin = getattr(obj, "name", None) or f"<{type(obj).__name__}>"

    getvalue = getattr(obj, "getvalue", None)
    if callable(getvalue):
        data = getvalue()
        if isinstance(data, (bytes, bytearray, memoryview)):
            return prepare_from_bytes(bytes(data), origin=str(origin), source_kind="upload")

    getbuffer = getattr(obj, "getbuffer", None)
    if callable(getbuffer):
        try:
            return prepare_from_bytes(bytes(getbuffer()), origin=str(origin), source_kind="upload")
        except (TypeError, ValueError) as exc:
            logger.debug("getbuffer() 不可用：%s", exc)

    read = getattr(obj, "read", None)
    if callable(read):
        data = read()
        if isinstance(data, (bytes, bytearray, memoryview)):
            seek = getattr(obj, "seek", None)
            if callable(seek):
                try:
                    seek(0)  # 复位，方便重复使用同一个上传对象
                except (OSError, ValueError):
                    pass
            return prepare_from_bytes(bytes(data), origin=str(origin), source_kind="upload")

    if isinstance(obj, (bytes, bytearray, memoryview)):
        return prepare_from_bytes(bytes(obj), origin="<bytes>", source_kind="bytes")

    raise ImageHandlerError(
        f"无法从 {type(obj).__name__} 对象中读取图片字节。"
        "请传入文件路径、图片 URL、字节内容，或带 read() 的文件对象。"
    )


def prepare_image_from_uploaded_file(uploaded_file: Any) -> ImagePayload:
    """处理 Streamlit ``st.file_uploader`` 返回的上传对象（本模块只要鸭子类型即可）。"""
    if uploaded_file is None:
        raise ImageHandlerError("没有收到上传的图片文件。")
    return prepare_image_from_memory(uploaded_file)


def prepare_image(
    source: Any,
    *,
    prefer_base64: bool = True,
    allow_private_hosts: bool = False,
) -> ImagePayload:
    """统一入口：自动判断来源并完成转换。

    Args:
        source: 支持以下任意一种
            - 本地路径（str / Path）
            - 网络地址（以 http:// 或 https:// 开头）
            - DataURL（以 data:image/ 开头）
            - 原始字节（bytes / bytearray）
            - 上传对象 / 文件流（带 read() 或 getvalue()）
        prefer_base64: 仅对网络 URL 生效，True 表示下载后转 Base64。
        allow_private_hosts: 仅本机自测时置 True，允许请求 127.0.0.1 等内网地址。

    Returns:
        ImagePayload 实例。
    """
    if source is None:
        raise ImageHandlerError("图片来源为空，请提供本地路径、图片 URL 或上传文件。")

    if isinstance(source, (bytes, bytearray, memoryview)):
        return prepare_from_bytes(bytes(source), origin="<bytes>", source_kind="bytes")

    if isinstance(source, (str, os.PathLike)):
        text = os.fspath(source).strip()
        lowered = text.lower()
        if lowered.startswith(("http://", "https://")):
            return prepare_image_from_url(
                text, prefer_base64=prefer_base64, allow_private_hosts=allow_private_hosts
            )
        if lowered.startswith("data:image/"):
            return data_url_to_image_payload(text)
        if lowered.startswith("data:"):
            raise ImageHandlerError("只支持 data:image/... 形式的 DataURL。")
        return prepare_image_from_local(text)

    return prepare_image_from_memory(source)


def data_url_to_image_payload(data_url: str) -> ImagePayload:
    """把 ``data:image/...;base64,xxxx`` 反解成 :class:`ImagePayload`。"""
    text = (data_url or "").strip()
    if not text.startswith("data:"):
        raise ImageHandlerError("这不是一个 DataURL（应以 data: 开头）。")
    header, _, payload = text.partition(",")
    if not payload:
        raise ImageHandlerError("DataURL 缺少 base64 数据部分。")
    if ";base64" not in header.lower():
        raise ImageHandlerError("仅支持 base64 编码的 DataURL。")
    declared = header[5:].split(";")[0].strip().lower() or None
    try:
        raw = base64.b64decode(payload, validate=False)
    except (binascii.Error, ValueError) as exc:
        raise ImageHandlerError(f"DataURL 的 base64 数据无法解码：{exc}") from exc
    return prepare_from_bytes(raw, origin="<data_url>", mime_hint=declared, source_kind="data_url")


# --------------------------------------------------------------------------- #
# 与 LangChain 消息对接
# --------------------------------------------------------------------------- #


def build_image_content_block(image: "ImagePayload | str | os.PathLike[str] | bytes") -> dict[str, Any]:
    """把图片转成多模态消息里的 image_url 内容块。

    既可以直接传 :class:`ImagePayload`，也可以传路径/URL/字节，函数会帮你转换。
    """
    payload = image if isinstance(image, ImagePayload) else prepare_image(image)
    return payload.to_content_block()


def build_human_message(
    prompt: str,
    images: "ImagePayload | str | os.PathLike[str] | bytes | list[Any] | None" = None,
) -> Any:
    """构造一条「文本 + 若干图片」的 HumanMessage，可直接丢给 Agent。

    Args:
        prompt: 给模型的文字指令。
        images: 单张图片或图片列表，元素可为 ImagePayload / 路径 / URL / 字节。

    Returns:
        ``langchain_core.messages.HumanMessage`` 实例。
    """
    from langchain_core.messages import HumanMessage

    content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]

    if images is not None:
        items = images if isinstance(images, (list, tuple)) else [images]
        for item in items:
            content.append(build_image_content_block(item))

    return HumanMessage(content=content)


def describe_image(image: ImagePayload) -> str:
    """生成一行人类可读的图片摘要，用于 Web UI 展示。"""
    size = f"{image.size_kb} KB"
    dimension = f"{image.width}×{image.height}" if image.width and image.height else "尺寸未知"
    return f"{image.source_kind}｜{image.mime_type}｜{size}｜{dimension}"

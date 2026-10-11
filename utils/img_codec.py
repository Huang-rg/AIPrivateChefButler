"""img_codec.py：极简图片编解码工具（零第三方依赖，不需要 Pillow）。

【为什么存在这个文件】
    第六步搭建 Streamlit 界面之前，项目暂时不安装 Pillow。但第一步
    （image_handler）的开发与自测需要「造一张真实 PNG / 读出真实像素 / 转出
    真实 BMP」。这里用标准库 zlib + struct 手写最小 PNG 编解码器，只服务
    测试与占位图生成，不参与线上业务逻辑。

    装了 Pillow 之后，业务代码应优先用 Pillow；本模块保持零依赖，长期作为
    「环境没装 Pillow 时的降级方案」。

提供能力：
    - encode_png / decode_png_rgb：RGB 像素矩阵 <-> PNG 字节
    - make_solid_png / make_gradient_png：生成纯色或渐变 PNG
    - write_png：把 PNG 字节写入磁盘
    - read_png_size：只读宽高（不解码像素）
    - png_to_simple_bmp：PNG -> 24 位 BMP（用于验证 BMP 解析链路）
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

__all__ = [
    "ImageCodecError",
    "decode_png_rgb",
    "encode_png",
    "make_gradient_png",
    "make_solid_png",
    "png_to_simple_bmp",
    "png_to_view_dib",
    "read_png_size",
    "write_png",
]

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


class ImageCodecError(RuntimeError):
    """图片编解码失败。"""


# --------------------------------------------------------------------------- #
# 编码
# --------------------------------------------------------------------------- #


def _png_chunk(tag: bytes, data: bytes) -> bytes:
    """构造一个 PNG chunk：长度 + 类型 + 数据 + CRC32。"""
    return (
        struct.pack(">I", len(data))
        + tag
        + data
        + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    )


def encode_png(width: int, height: int, pixels: "list[list[tuple[int, int, int]]]") -> bytes:
    """把 RGB 像素矩阵编码为 8 位真彩色 PNG 字节。

    Args:
        width: 图片宽度（像素）。
        height: 图片高度（像素）。
        pixels: ``height`` 行、每行 ``width`` 个 ``(r, g, b)`` 元组。

    Returns:
        PNG 文件字节。
    """
    if width <= 0 or height <= 0:
        raise ImageCodecError(f"图片尺寸必须为正数，收到 {width}x{height}。")
    if len(pixels) != height or any(len(row) != width for row in pixels):
        raise ImageCodecError("像素矩阵尺寸与 width/height 不一致。")

    raw = bytearray()
    for row in pixels:
        raw.append(0)  # 每行前面的滤波器类型：0 = None（我们编码时不做滤波）
        for r, g, b in row:
            raw.extend((r & 0xFF, g & 0xFF, b & 0xFF))

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)  # 8bit, truecolor RGB
    return (
        _PNG_SIGNATURE
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + _png_chunk(b"IEND", b"")
    )


def make_solid_png(width: int = 64, height: int = 64, color: tuple[int, int, int] = (200, 60, 40)) -> bytes:
    """生成一张纯色 PNG（默认番茄红，用作「食材图」占位）。"""
    return encode_png(width, height, [[color] * width for _ in range(height)])


def make_gradient_png(width: int = 96, height: int = 64) -> bytes:
    """生成一张彩色渐变 PNG（像素值各不相同，可顺便验证 Base64 无损传输）。"""
    pixels = [
        [
            (int(255 * x / max(width - 1, 1)), int(180 * y / max(height - 1, 1)), 90)
            for x in range(width)
        ]
        for y in range(height)
    ]
    return encode_png(width, height, pixels)


def write_png(path: "str | Path", data: bytes) -> Path:
    """把 PNG 字节写入磁盘，返回写入的绝对路径。"""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return target.resolve()


# --------------------------------------------------------------------------- #
# 解码
# --------------------------------------------------------------------------- #


def _iter_chunks(data: bytes):
    """遍历 PNG chunk，产出 (类型, 数据) 二元组。"""
    if not data.startswith(_PNG_SIGNATURE):
        raise ImageCodecError("这不是一张 PNG 图片（缺少 PNG 文件头）。")
    offset = len(_PNG_SIGNATURE)
    total = len(data)
    while offset + 8 <= total:
        length = struct.unpack(">I", data[offset : offset + 4])[0]
        tag = data[offset + 4 : offset + 8]
        start = offset + 8
        end = start + length
        if end > total:
            raise ImageCodecError("PNG 数据不完整（chunk 长度越界）。")
        yield tag, data[start:end]
        offset = end + 4  # 跳过 CRC


def read_png_size(data: bytes) -> tuple[int, int]:
    """从 PNG 字节中读取 (宽, 高)，不解码像素。"""
    if not data.startswith(_PNG_SIGNATURE):
        raise ImageCodecError("这不是一张 PNG 图片（文件头不匹配）。")
    if len(data) < 24:
        raise ImageCodecError("PNG 数据不完整，无法读取 IHDR。")
    width, height = struct.unpack(">II", data[16:24])
    return int(width), int(height)


def _paeth(a: int, b: int, c: int) -> int:
    """PNG 标准 Paeth 预测函数。"""
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c


def decode_png_rgb(data: bytes) -> tuple[int, int, "list[list[tuple[int, int, int]]]"]:
    """解码 8 位真彩色（非隔行）PNG，返回 (宽, 高, 像素矩阵)。

    仅支持本项目自己生成的 PNG 以及绝大多数截图工具导出的标准 PNG；
    调色板 / 灰度 / 16 位 / Adam7 隔行图片会明确报错，不做静默降级。

    Raises:
        ImageCodecError: 格式不支持或数据损坏。
    """
    width = height = 0
    bit_depth = color_type = interlace = -1
    idat = bytearray()

    for tag, chunk in _iter_chunks(data):
        if tag == b"IHDR":
            width, height, bit_depth, color_type, _comp, _filter, interlace = struct.unpack(
                ">IIBBBBB", chunk
            )
        elif tag == b"IDAT":
            idat.extend(chunk)
        elif tag == b"IEND":
            break

    if width <= 0 or height <= 0:
        raise ImageCodecError("PNG 缺少有效的 IHDR（宽高非法）。")
    if bit_depth != 8 or color_type != 2:
        raise ImageCodecError(
            f"只支持 8 位真彩色 PNG，当前 bit_depth={bit_depth}, color_type={color_type}。"
        )
    if interlace != 0:
        raise ImageCodecError("不支持 Adam7 隔行 PNG。")
    if not idat:
        raise ImageCodecError("PNG 缺少 IDAT 数据块。")

    try:
        raw = zlib.decompress(bytes(idat))
    except zlib.error as exc:
        raise ImageCodecError(f"PNG 的 IDAT 解压失败：{exc}") from exc

    bpp = 3  # 每像素字节数（RGB）
    stride = width * bpp
    expected = (stride + 1) * height
    if len(raw) < expected:
        raise ImageCodecError(f"PNG 像素数据不完整：期望 {expected} 字节，实际 {len(raw)} 字节。")

    rows: list[bytearray] = []
    previous = bytearray(stride)
    offset = 0
    for _ in range(height):
        filter_type = raw[offset]
        offset += 1
        line = bytearray(raw[offset : offset + stride])
        offset += stride

        if filter_type == 1:  # Sub
            for i in range(bpp, stride):
                line[i] = (line[i] + line[i - bpp]) & 0xFF
        elif filter_type == 2:  # Up
            for i in range(stride):
                line[i] = (line[i] + previous[i]) & 0xFF
        elif filter_type == 3:  # Average
            for i in range(stride):
                left = line[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + ((left + previous[i]) >> 1)) & 0xFF
        elif filter_type == 4:  # Paeth
            for i in range(stride):
                left = line[i - bpp] if i >= bpp else 0
                up_left = previous[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + _paeth(left, previous[i], up_left)) & 0xFF
        elif filter_type != 0:
            raise ImageCodecError(f"不支持的 PNG 行滤波器类型：{filter_type}。")

        rows.append(line)
        previous = line

    pixels = [
        [(row[x * 3], row[x * 3 + 1], row[x * 3 + 2]) for x in range(width)] for row in rows
    ]
    return width, height, pixels


def png_to_view_dib(data: bytes, *, max_side: int = 32) -> bytes:
    """把 PNG 转成 ``CF_DIB`` 剪贴板格式（自底向上 BMP + ``BI_BITFIELDS`` 掩码）。

    与 :func:`png_to_simple_bmp` 的区别：DIB 没有 14 字节 BMP 文件头，且用
    40 字节 ``BITMAPINFOHEADER`` + 12 字节 RGB 掩码声明通道顺序。按
    ``CF_DIB`` 规范，没有掩码时应视为 24 位 BGR，但很多程序倾向显式声明，
    这里跟随主流做法，保证「别人的程序能读我们放的图，我们也能读回来」。

    Returns:
        ``CF_DIB`` 字节（可直接交给 ``SetClipboardData(CF_DIB, ...)``）。
    """
    width, height, pixels = decode_png_rgb(data)
    width = max(1, min(width, max_side))
    height = max(1, min(height, max_side))

    stride = ((width * 3 + 3) // 4) * 4
    padding = b"\x00" * (stride - width * 3)
    body = bytearray()
    for y in range(height - 1, -1, -1):  # 自底向上
        for r, g, b in pixels[y][:width]:
            body.extend((b, g, r))  # BGR
        body.extend(padding)

    header_size = 40
    masks = struct.pack("<III", 0x00FF0000, 0x0000FF00, 0x000000FF)  # R / G / B
    info_header = struct.pack(
        "<IiiHHIIiiII",
        header_size,
        width,
        height,
        1,
        24,
        3,  # BI_BITFIELDS
        len(body),
        2835,
        2835,
        0,
        0,
    )
    return info_header + masks + bytes(body)


def png_to_simple_bmp(data: bytes, *, max_side: int = 32) -> bytes:
    """把 PNG 转成 24 位 BMP（真实像素，自底向上，行按 4 字节对齐）。

    用于验证 image_handler 的 BMP 魔数嗅探与尺寸解析链路。

    Args:
        data: PNG 字节。
        max_side: 宽高上限，超过则只取左上角一块，避免测试文件过大。
    """
    width, height, pixels = decode_png_rgb(data)
    width = max(1, min(width, max_side))
    height = max(1, min(height, max_side))

    stride = ((width * 3 + 3) // 4) * 4
    padding = b"\x00" * (stride - width * 3)
    body = bytearray()
    for y in range(height - 1, -1, -1):  # BMP 是自底向上存储
        row = pixels[y][:width]
        for r, g, b in row:
            body.extend((b, g, r))  # BMP 的字节序是 BGR
        body.extend(padding)

    header_size = 40
    offset = 14 + header_size
    file_header = b"BM" + struct.pack("<IHHI", offset + len(body), 0, 0, offset)
    info_header = struct.pack(
        "<IiiHHIIiiII",
        header_size,
        width,
        height,
        1,  # 颜色平面数
        24,  # 位深
        0,  # 无压缩
        len(body),
        2835,  # 水平分辨率（像素/米）
        2835,  # 垂直分辨率
        0,
        0,
    )
    return file_header + info_header + bytes(body)

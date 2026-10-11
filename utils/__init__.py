"""utils 包：AI私厨管家的通用工具函数集合。

包含两个模块：
    - image_handler：本地图片 / 网络 URL 图片 / 剪贴板图片 -> Base64 / DataURL 转换，
      供多模态模型（Qwen-VL 等）识别食材使用。
    - img_codec：零依赖（不需要 Pillow）的最小 PNG/BMP 编解码器 + 剪贴板
      ``CF_DIB`` 格式构造，供 image_handler 自测与界面占位图使用。

用法示例::

    from utils.image_handler import prepare_image, build_human_message

    payload = prepare_image(r"E:\\图片\\冰箱.jpg")
    message = build_human_message("请识别这张图里的食材", payload)
"""

from .image_handler import (
    DEFAULT_MAX_IMAGE_BYTES,
    SUPPORTED_MIME_TYPES,
    ImageHandlerError,
    ImagePayload,
    build_human_message,
    build_image_content_block,
    data_url_to_image_payload,
    describe_image,
    prepare_from_bytes,
    prepare_image,
    prepare_image_from_clipboard,
    prepare_image_from_local,
    prepare_image_from_memory,
    prepare_image_from_uploaded_file,
    prepare_image_from_url,
    sniff_mime_type,
)

__all__ = [
    "DEFAULT_MAX_IMAGE_BYTES",
    "SUPPORTED_MIME_TYPES",
    "ImageHandlerError",
    "ImagePayload",
    "build_human_message",
    "build_image_content_block",
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

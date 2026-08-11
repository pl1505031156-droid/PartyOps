"""浏览器文件响应的保守内容类型边界。"""

from __future__ import annotations


SAFE_INLINE_MEDIA_TYPES = {
    "application/pdf",
    "image/avif",
    "image/bmp",
    "image/gif",
    "image/jpeg",
    "image/png",
    "image/webp",
}


def may_render_inline(media_type: str) -> bool:
    """只允许不会作为活动 HTML/XML 文档执行的明确类型内联。"""

    return media_type.lower().split(";", 1)[0].strip() in SAFE_INLINE_MEDIA_TYPES

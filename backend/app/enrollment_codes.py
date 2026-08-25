"""设备入网码的生成格式与剪贴板兼容规范。"""

from __future__ import annotations

import re
import unicodedata

ENROLLMENT_SECRET_LENGTH = 24
ENROLLMENT_FINGERPRINT_LENGTH = 64
MAX_ENROLLMENT_INPUT_LENGTH = 1024
_INVISIBLE_CHARACTERS = str.maketrans(
    {
        "\u200b": None,  # 零宽空格
        "\u200c": None,  # 零宽非连接符
        "\u200d": None,  # 零宽连接符
        "\u2060": None,  # 单词连接符
        "\ufeff": None,  # BOM/零宽不换行空格
    }
)
_CODE_PATTERN = re.compile(
    rf"(?<![A-Za-z0-9_-])"
    rf"([A-Za-z0-9_-]{{{ENROLLMENT_SECRET_LENGTH}}}"
    rf"\.[0-9A-Fa-f]{{{ENROLLMENT_FINGERPRINT_LENGTH}}})"
    rf"(?![0-9A-Fa-f])"
)


def normalize_enrollment_code(value: str) -> str:
    """从纯入网码或带中文标签的剪贴板文本中提取唯一规范入网码。

    浏览器在非安全 HTTP 页面使用兼容复制方式，部分国产浏览器或输入法会
    把标签、换行、全角标点或零宽字符一并放进剪贴板。服务端必须与终端使用
    同一规范化逻辑，否则任何一个不可见字符都会令 SHA-256 无法匹配。
    """

    if not isinstance(value, str) or not value.strip():
        raise ValueError("入网码为空")
    if len(value) > MAX_ENROLLMENT_INPUT_LENGTH:
        raise ValueError("粘贴内容过长")

    normalized = unicodedata.normalize("NFKC", value).translate(_INVISIBLE_CHARACTERS)
    compact = "".join(normalized.split())
    candidates = list(dict.fromkeys(_CODE_PATTERN.findall(compact)))
    if len(candidates) != 1:
        raise ValueError("未找到唯一完整入网码")

    secret, fingerprint = candidates[0].rsplit(".", 1)
    return f"{secret}.{fingerprint.lower()}"

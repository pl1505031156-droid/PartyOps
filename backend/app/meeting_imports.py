"""党委会议程/记录的本地解析与低误判候选提取。

解析器先检查真实文件头，再决定是否按 OOXML 读取或通过本机受控办公套件
转换 OLE 文档。原始文件与正文只存在于当前请求的私有临时目录，返回值只
包含需要用户确认的结构化候选、证据类型和警告。
"""

from __future__ import annotations

import os
import re
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .intake import _extract_docx
from .official_format import OfficialFormatError, prepare_docx
from .problems import ProblemException

MAX_MEETING_IMPORT_BYTES = 50 * 1024 * 1024
OLE_HEADER = bytes.fromhex("D0CF11E0A1B11AE1")
ZIP_HEADERS = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")
BEIJING = timezone(timedelta(hours=8))

_TOPIC_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("中文一级序号", re.compile(r"^(?:议题\s*)?([一二三四五六七八九十]+)[、.．]\s*(.+)$")),
    ("中文括号序号", re.compile(r"^(?:议题\s*)?（([一二三四五六七八九十]+)）\s*(.+)$")),
    ("数字一级序号", re.compile(r"^(?:议题\s*)?(\d{1,2})[、.．]\s*(.+)$")),
    ("数字括号序号", re.compile(r"^(?:议题\s*)?[（(](\d{1,2})[）)]\s*(.+)$")),
)
_DATE_COMPACT = re.compile(r"(?<!\d)(20\d{2})(0[1-9]|1[0-2])([0-2]\d|3[01])(?!\d)")
_DATE_TEXT = re.compile(r"(20\d{2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]")
_TIME_TEXT = re.compile(r"(?:(上午|下午|晚上)\s*)?(\d{1,2})\s*[：:]\s*(\d{2})")


def detect_meeting_container(data: bytes) -> str:
    if data.startswith(ZIP_HEADERS):
        return "ooxml"
    if data.startswith(OLE_HEADER):
        return "ole"
    raise ProblemException(
        422,
        "MEETING_IMPORT_CONTAINER_UNSUPPORTED",
        "会议文件格式无法识别",
        "仅支持真实的 DOCX、DOC 或 WPS 文档；系统不会只根据扩展名猜测格式。",
    )


def extract_meeting_text(data: bytes) -> tuple[str, str]:
    if not data or len(data) > MAX_MEETING_IMPORT_BYTES:
        raise ProblemException(
            413,
            "MEETING_IMPORT_SIZE_LIMIT",
            "会议文件超过限制",
            "单个会议导入文件不得超过 50 MiB。",
        )
    container = detect_meeting_container(data)
    if container == "ooxml":
        return _extract_docx(data), container
    try:
        with tempfile.TemporaryDirectory(prefix="partyops-meeting-import-") as raw:
            workspace = Path(raw).resolve()
            if os.name != "nt":
                workspace.chmod(0o700)
            source = workspace / "meeting-source.doc"
            source.write_bytes(data)
            if os.name != "nt":
                source.chmod(0o600)
            converted, _changed = prepare_docx(source, workspace)
            return _extract_docx(converted.read_bytes()), container
    except OfficialFormatError as exc:
        raise ProblemException(422, exc.code, exc.title, exc.detail) from exc


def _date_candidate(text: str, filename_hint: str) -> tuple[str | None, str | None]:
    for source, pattern, value in (
        ("文件名", _DATE_COMPACT, filename_hint),
        ("正文", _DATE_TEXT, text),
    ):
        match = pattern.search(value)
        if not match:
            continue
        try:
            candidate = datetime(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except ValueError:
            continue
        return candidate.date().isoformat(), source
    return None, None


def _scheduled_at(text: str, date_value: str | None) -> str | None:
    if not date_value:
        return None
    match = _TIME_TEXT.search(text)
    if not match:
        return None
    hour = int(match.group(2))
    minute = int(match.group(3))
    if match.group(1) in {"下午", "晚上"} and hour < 12:
        hour += 12
    try:
        value = datetime.fromisoformat(date_value).replace(hour=hour, minute=minute, tzinfo=BEIJING)
    except ValueError:
        return None
    return value.isoformat()


def _label_value(lines: list[str], labels: tuple[str, ...]) -> str:
    for line in lines:
        for label in labels:
            match = re.match(rf"^{re.escape(label)}\s*[：:]\s*(.+)$", line)
            if match:
                return match.group(1).strip()[:240]
    return ""


def _chinese_number(value: str) -> int | None:
    digits = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    if value == "十":
        return 10
    if "十" in value:
        left, right = value.split("十", 1)
        tens = digits.get(left, 1) if left else 1
        ones = digits.get(right, 0) if right else 0
        return tens * 10 + ones
    return digits.get(value)


def _topic_candidates(lines: list[str]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for index, line in enumerate(lines):
        for style, pattern in _TOPIC_PATTERNS:
            match = pattern.match(line)
            if not match:
                continue
            raw_number = match.group(1)
            number = int(raw_number) if raw_number.isdigit() else _chinese_number(raw_number)
            title = match.group(2).strip(" ：:;；。")[:240]
            if number is not None and len(title) >= 2:
                candidates.append({"style": style, "number": number, "title": title, "line": index + 1})
            break
    return candidates


def _dominant_topic_style(candidates: list[dict[str, Any]]) -> str | None:
    """只在存在明确连续层级时选择主序号，避免把议题内的小项当成议题。"""

    for style, _pattern in _TOPIC_PATTERNS:
        values: list[int] = []
        for item in candidates:
            if item["style"] != style or item["number"] in values:
                continue
            values.append(item["number"])
        if len(values) >= 2 and values[0] == 1 and values == list(range(1, len(values) + 1)):
            return style
    return None


def propose_meeting(text: str, filename_hint: str = "") -> dict[str, Any]:
    lines = [" ".join(item.split()) for item in text.splitlines() if item.strip()]
    filename_title = Path(filename_hint).stem[:240]
    title_candidates: list[tuple[int, str]] = []
    if "党委会" in filename_title:
        title_candidates.append((12, filename_title))
    for index, item in enumerate(lines[:40]):
        if "党委会" not in item or len(item) > 120:
            continue
        score = 4 + (3 if index < 10 else 0)
        if any(keyword in item for keyword in ("议程", "会议记录", "会议纪要")):
            score += 6
        if any(keyword in item for keyword in ("提请", "建议", "审议。", "审议；")):
            score -= 8
        title_candidates.append((score, item[:240]))
    title = max(title_candidates, default=(0, filename_title or "党委会议草稿"))[1]
    date_value, date_source = _date_candidate(text, filename_hint)
    scheduled = _scheduled_at(text, date_value)
    topics: list[dict[str, Any]] = []
    seen: set[str] = set()
    candidates = _topic_candidates(lines)
    dominant_style = _dominant_topic_style(candidates)
    for candidate in candidates:
        if dominant_style and candidate["style"] != dominant_style:
            continue
        topic = candidate["title"]
        if len(topic) < 2 or topic in seen:
            continue
        seen.add(topic)
        topics.append(
            {
                "title": topic,
                "confidence": 0.96,
                "source": f"正文第 {candidate['line']} 行的{candidate['style']}",
                "confirmed": False,
            }
        )
    warnings: list[str] = []
    if not date_value:
        warnings.append("未识别到明确会议日期，请人工填写。")
    elif not scheduled:
        warnings.append(f"已从{date_source}识别日期 {date_value}，未猜测会议时间，请人工确认。")
    if not topics:
        warnings.append("未识别到带明确序号的议题，系统未从普通正文猜测，请人工添加。")
    return {
        "meeting_type": "party_committee",
        "title": title,
        "organization": "",
        "scheduled_at": scheduled,
        "date_candidate": date_value,
        "date_candidate_source": date_source,
        "venue": _label_value(lines, ("会议地点", "地点")),
        "host_name": _label_value(lines, ("主持人", "主持")),
        "attendee_text": _label_value(lines, ("参会人员", "出席人员", "参会人")),
        "topics": topics,
        "warnings": warnings,
        "requires_confirmation": True,
    }


__all__ = [
    "MAX_MEETING_IMPORT_BYTES",
    "detect_meeting_container",
    "extract_meeting_text",
    "propose_meeting",
]

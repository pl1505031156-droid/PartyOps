"""领域规则单元测试。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import io

import pytest
import fitz
from PIL import Image

from app.backups import sha256_file
from app.enums import RecurrenceKind, TaskStatus
from app.intake import _extract_image, _extract_pdf, parse_text
from app.models import RecurrenceRule
from app.problems import ProblemException
from app.recurrence import add_months, next_occurrence
from app.state_machine import transition
from app.storage import resolve_blob_path


@pytest.mark.parametrize(
    ("current", "action", "target"),
    [
        (TaskStatus.PENDING_RECEIPT, "accept", TaskStatus.IN_PROGRESS),
        (TaskStatus.IN_PROGRESS, "wait_feedback", TaskStatus.WAITING_FEEDBACK),
        (TaskStatus.WAITING_FEEDBACK, "resume", TaskStatus.IN_PROGRESS),
        (TaskStatus.IN_PROGRESS, "submit_review", TaskStatus.PENDING_REVIEW),
        (TaskStatus.PENDING_REVIEW, "return", TaskStatus.RETURNED),
        (TaskStatus.PENDING_REVIEW, "approve", TaskStatus.COMPLETED),
        (TaskStatus.COMPLETED, "archive", TaskStatus.ARCHIVED),
        (TaskStatus.ARCHIVED, "reopen", TaskStatus.IN_PROGRESS),
    ],
)
def test_state_machine(current: TaskStatus, action: str, target: TaskStatus) -> None:
    assert transition(current, action) == target


def test_invalid_transition() -> None:
    with pytest.raises(ProblemException) as error:
        transition(TaskStatus.ARCHIVED, "approve")
    assert error.value.code == "INVALID_TRANSITION"


def test_intake_extracts_title_date_and_requirements(monkeypatch) -> None:
    candidate = parse_text(
        "关于报送七月党建台账的通知\n请于2026年8月12日前报送材料。\n提交盖章版和电子版。"
    )
    assert candidate.title.startswith("关于报送")
    assert candidate.formal_due_at == datetime(
        2026, 8, 12, 18, 0, tzinfo=timezone(timedelta(hours=8))
    )
    assert len(candidate.requirements) == 2
    assert candidate.warnings == []


def test_intake_requires_manual_confirmation_without_date() -> None:
    candidate = parse_text("主题党日活动通知\n请按要求准备。")
    assert candidate.formal_due_at is None
    assert candidate.warnings


def test_path_traversal_is_rejected() -> None:
    with pytest.raises(ProblemException) as error:
        resolve_blob_path("../../outside.db")
    assert error.value.code == "INVALID_FILE_PATH"


def test_sha256_file(tmp_path) -> None:
    sample = tmp_path / "sample.bin"
    sample.write_bytes(b"PartyOps")
    assert sha256_file(sample) == "168bc5fbf62a09051e2150ba40791090ace04e22e3a6114b70be543db1f8bed8"


def test_date_edge_cases_and_recurrence_variants() -> None:
    anchor = datetime(2026, 12, 20, tzinfo=timezone.utc)
    candidate = parse_text("跨年通知\n请于1月2日前报送。")
    assert candidate.formal_due_at is not None
    assert add_months(datetime(2024, 1, 31), 1) == datetime(2024, 2, 29)
    base = datetime(2026, 1, 31)
    expected = {
        RecurrenceKind.QUARTERLY: datetime(2026, 4, 30),
        RecurrenceKind.HALF_YEARLY: datetime(2026, 7, 31),
        RecurrenceKind.YEARLY: datetime(2027, 1, 31),
        RecurrenceKind.CUSTOM_DAYS: base + timedelta(days=7),
    }
    for kind, target in expected.items():
        rule = RecurrenceRule(
            name="规则",
            template_id="template",
            owner_id="owner",
            kind=kind,
            custom_days=7,
            next_run_at=base,
        )
        assert next_occurrence(rule, base) == target


def test_intake_relative_dates_and_explicit_times(monkeypatch) -> None:
    from app import intake

    anchor = datetime(2026, 7, 28, 9, 0, tzinfo=timezone(timedelta(hours=8)))
    tomorrow = intake._extract_date("请于明天下午3点前反馈", anchor)
    assert tomorrow == datetime(
        2026, 7, 29, 15, 0, tzinfo=timezone(timedelta(hours=8))
    )
    friday = intake._extract_date("本周五17:30前报送", anchor)
    assert friday.weekday() == 4 and friday.hour == 17 and friday.minute == 30
    next_monday = intake._extract_date("下周一上午9点提交", anchor)
    assert next_monday.weekday() == 0 and next_monday.hour == 9
    month_end = intake._extract_date("月底前形成材料", anchor)
    assert month_end.day == 31
    assert intake._extract_date("2026年2月31日前", anchor) is None


def test_image_and_scanned_pdf_ocr_fallback(monkeypatch) -> None:
    image = Image.new("RGB", (32, 24), "white")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")

    monkeypatch.setattr(
        "app.intake.pytesseract.image_to_string",
        lambda *_args, **_kwargs: "识别结果",
    )
    text, warnings = _extract_image(buffer.getvalue())
    assert text == "识别结果"
    assert warnings == []
    invalid_text, invalid_warnings = _extract_image(b"not-an-image")
    assert invalid_text == ""
    assert invalid_warnings

    pdf = fitz.open()
    pdf.new_page()
    pdf_bytes = pdf.tobytes()
    pdf.close()
    scanned_text, scanned_warnings = _extract_pdf(pdf_bytes)
    assert "识别结果" in scanned_text
    assert scanned_warnings == []

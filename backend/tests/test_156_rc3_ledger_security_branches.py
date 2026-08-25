"""通用台账解析器的对抗式边界与低误判分支回归。"""

from __future__ import annotations

import io
import json
import math
import stat
import uuid
import zipfile
from datetime import date, datetime

import pytest

from app import ledger_imports as ledger
from app.ledger_imports import FieldSpec
from app.problems import ProblemException


def _zip(entries: list[tuple[str | zipfile.ZipInfo, bytes]], *, compression: int = zipfile.ZIP_STORED) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=compression) as package:
        for name, content in entries:
            package.writestr(name, content)
    return output.getvalue()


def _problem_code(callable_, *args) -> str:
    with pytest.raises(ProblemException) as raised:
        callable_(*args)
    return raised.value.code


def test_filename_signatures_and_size_guards(monkeypatch: pytest.MonkeyPatch) -> None:
    assert ledger.validate_filename("人员台账.CSV") == ".csv"
    assert _problem_code(ledger.validate_filename, "人员台账.docx") == "LEDGER_FILE_TYPE_UNSUPPORTED"
    assert _problem_code(ledger.validate_file_bytes, b"", ".csv") == "LEDGER_FILE_EMPTY"
    monkeypatch.setattr(ledger, "MAX_FILE_BYTES", 2)
    assert _problem_code(ledger.validate_file_bytes, b"123", ".csv") == "LEDGER_FILE_TOO_LARGE"
    monkeypatch.setattr(ledger, "MAX_FILE_BYTES", 50 * 1024 * 1024)
    assert _problem_code(ledger.validate_file_bytes, b"not-a-zip", ".xlsx") == "LEDGER_FILE_SIGNATURE_INVALID"
    assert _problem_code(ledger.validate_file_bytes, b"not-an-xls", ".xls") == "LEDGER_FILE_SIGNATURE_INVALID"
    ledger.validate_file_bytes("姓名,年度\n张三,2026".encode(), ".csv")


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("../evil.xml", "LEDGER_ARCHIVE_PATH_TRAVERSAL"),
        ("xl/vbaProject.bin", "LEDGER_MACRO_BLOCKED"),
        ("xl/externalLinks/externalLink1.xml", "LEDGER_EXTERNAL_LINK_BLOCKED"),
        ("xl/connections.xml", "LEDGER_EXTERNAL_LINK_BLOCKED"),
    ],
)
def test_zip_container_rejects_unsafe_members(name: str, expected: str) -> None:
    assert _problem_code(ledger._validate_zip_container, _zip([(name, b"x")])) == expected


def test_zip_container_limits_symlink_ratio_and_corruption(monkeypatch: pytest.MonkeyPatch) -> None:
    link = zipfile.ZipInfo("xl/link")
    link.create_system = 3
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    assert _problem_code(ledger._validate_zip_container, _zip([(link, b"target")])) == "LEDGER_ARCHIVE_SYMLINK"

    normal = _zip([("xl/workbook.xml", b"A" * 4096)], compression=zipfile.ZIP_DEFLATED)
    monkeypatch.setattr(ledger, "MAX_ZIP_RATIO", 1)
    assert _problem_code(ledger._validate_zip_container, normal) == "LEDGER_ARCHIVE_RATIO_INVALID"
    monkeypatch.setattr(ledger, "MAX_ZIP_RATIO", 100)
    monkeypatch.setattr(ledger, "MAX_ZIP_UNCOMPRESSED", 2)
    assert _problem_code(ledger._validate_zip_container, _zip([("xl/workbook.xml", b"123")])) == "LEDGER_ARCHIVE_EXPANSION_LIMIT"
    monkeypatch.setattr(ledger, "MAX_ZIP_UNCOMPRESSED", 200 * 1024 * 1024)
    monkeypatch.setattr(ledger, "MAX_ZIP_MEMBERS", 0)
    assert _problem_code(ledger._validate_zip_container, _zip([("xl/workbook.xml", b"x")])) == "LEDGER_ARCHIVE_TOO_MANY_FILES"
    assert _problem_code(ledger._validate_zip_container, b"not-a-zip") == "LEDGER_CONTAINER_INVALID"


def test_cell_cleaning_and_csv_encoding_guards(monkeypatch: pytest.MonkeyPatch) -> None:
    assert ledger._clean_cell(None) is None
    assert ledger._clean_cell(datetime(2026, 8, 25, 10, 30)) == "2026-08-25T10:30:00"
    assert ledger._clean_cell(date(2026, 8, 25)) == "2026-08-25"
    assert ledger._clean_cell(True) is True
    assert ledger._clean_cell(12) == 12
    assert ledger._clean_cell(math.nan) is None
    assert ledger._clean_cell(math.inf) is None
    assert ledger._clean_cell("\x00 Ａ ") == "A"
    monkeypatch.setattr(ledger, "MAX_CELL_CHARS", 2)
    assert _problem_code(ledger._clean_cell, "内容过长") == "LEDGER_CELL_TOO_LONG"
    monkeypatch.setattr(ledger, "MAX_CELL_CHARS", 20_000)

    assert "姓名" in ledger._decode_csv("姓名".encode("gb18030"))
    assert _problem_code(ledger._decode_csv, b"\x81") == "LEDGER_CSV_ENCODING_INVALID"


def test_csv_row_and_column_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ledger, "MAX_ROWS_PER_SHEET", 1)
    assert _problem_code(ledger._parse_csv, b"a\n1\n") == "LEDGER_ROW_LIMIT"
    monkeypatch.setattr(ledger, "MAX_ROWS_PER_SHEET", 50_000)
    monkeypatch.setattr(ledger, "MAX_COLUMNS", 1)
    assert _problem_code(ledger._parse_csv, b"a,b\n") == "LEDGER_COLUMN_LIMIT"
    monkeypatch.setattr(ledger, "MAX_COLUMNS", 200)
    sheets, metadata = ledger.parse_table(b"\xef\xbb\xbf\xe5\xa7\x93\xe5\x90\x8d\n\xe5\xbc\xa0\xe4\xb8\x89\n", ".csv")
    assert sheets["CSV"][1] == ["张三"]
    assert metadata == [{"name": "CSV", "rows": 2, "columns": 1, "hidden": False}]


def test_header_formula_date_and_type_inference() -> None:
    assert ledger.detect_header_row([]) == 1
    assert ledger.detect_header_row([[None, ""], ["姓名", "年度"]]) == 2
    assert ledger._formula_like(None) is False
    assert ledger._formula_like("") is False
    assert ledger._formula_like("普通文字") is False
    assert ledger._formula_like("-12.5") is False
    assert ledger._formula_like("=HYPERLINK(\"https://example.invalid\")") is True

    assert ledger._parse_iso_date(datetime(2026, 8, 25, 10, 0)) == date(2026, 8, 25)
    assert ledger._parse_iso_date(date(2026, 8, 25)) == date(2026, 8, 25)
    assert ledger._parse_iso_date("") is None
    assert ledger._parse_iso_date("2026年8月25日") == date(2026, 8, 25)
    assert ledger._parse_iso_date("2026/8") == date(2026, 8, 1)
    assert ledger._parse_iso_date("20260825") == date(2026, 8, 25)
    assert ledger._parse_iso_date("2026-02-30") is None
    assert ledger._parse_iso_date("25/08/2026") is None

    assert ledger.infer_type([])["type"] == "empty"
    assert ledger.infer_type(["2026-08-25", "2026-08-26"])["type"] == "date"
    assert ledger.infer_type([1, 2, 3])["type"] == "number"
    assert ledger.infer_type(["是", "否"])["type"] == "boolean"
    ambiguous = ledger.infer_type(["08/09/2026", "普通文字"])
    assert ambiguous == {"type": "text", "confidence": 1.0, "date_ambiguous": True}


def test_target_fields_and_mapping_confidence() -> None:
    assert any(item.key == "name" for item in ledger.fields_for_target("party_development"))
    custom = ledger.fields_for_target(
        "archive",
        [
            {"key": "custom_level", "label": "档案等级", "type": "text", "aliases": ["等级"], "required": True},
            {"key": "INVALID-KEY", "label": "忽略"},
            {"key": "", "label": "忽略"},
        ],
    )
    assert custom[-1] == FieldSpec("custom_level", "档案等级", "text", ("等级", "档案等级"), True)
    assert _problem_code(ledger.fields_for_target, "unknown") == "LEDGER_TARGET_INVALID"

    fields = [
        FieldSpec("name", "姓名", "text", ("人员姓名",)),
        FieldSpec("alias_name", "别名", "text", ("人员姓名",)),
        FieldSpec("organization", "所属单位", "text", ("组织单位",)),
    ]
    assert ledger.suggest_field("", fields) is None
    assert ledger.suggest_field("姓名", fields)["confidence"] == "high"
    conflict = ledger.suggest_field("人员姓名", fields)
    assert conflict and conflict["confidence"] == "conflict"
    medium = ledger.suggest_field("所属单位表", [fields[2]])
    assert medium and medium["confidence"] == "medium" and medium["confirmed"] is False
    assert ledger.suggest_field("完全无关字段", fields) is None


def test_profile_sheet_duplicate_empty_and_formula_columns() -> None:
    fields = ledger.fields_for_target("party_development")
    assert _problem_code(ledger.profile_sheet, [["姓名"]], 0, fields) == "LEDGER_HEADER_ROW_INVALID"
    assert _problem_code(ledger.profile_sheet, [[None, ""]], 1, fields) == "LEDGER_HEADER_EMPTY"
    profile = ledger.profile_sheet(
        [["姓名", " 姓名 ", "公式", ""], ["张三", "张三", "=1+1"]],
        1,
        fields,
    )
    assert profile["duplicate_headers"] == ["姓名"]
    assert len(profile["columns"]) == 3
    assert profile["columns"][2]["formula_like"] == 1


def test_private_stage_mapping_and_value_parsers(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ledger, "_staging_root", lambda: tmp_path)
    job_id = str(uuid.uuid4())
    assert _problem_code(ledger.stage_path, "not-a-uuid") == "LEDGER_JOB_ID_INVALID"
    assert _problem_code(ledger.load_stage, job_id) == "LEDGER_STAGE_MISSING"

    path = ledger.stage_path(job_id)
    path.write_text("not-json", encoding="utf-8")
    assert _problem_code(ledger.load_stage, job_id) == "LEDGER_STAGE_INVALID"
    path.write_text(json.dumps({"sheets": []}), encoding="utf-8")
    assert _problem_code(ledger.load_stage, job_id) == "LEDGER_STAGE_INVALID"

    ledger.save_stage(job_id, {"台账": [["姓名", "备注"], ["张三", ""], ["", ""]]})
    assert _problem_code(ledger.mapped_rows, job_id, "不存在", 1, []) == "LEDGER_SHEET_NOT_FOUND"
    assert _problem_code(ledger.mapped_rows, job_id, "台账", 4, []) == "LEDGER_HEADER_ROW_INVALID"
    rows = ledger.mapped_rows(
        job_id,
        "台账",
        1,
        [
            {"source_column": "忽略", "action": "ignore"},
            {"source_column": "不存在", "action": "map", "target_field": "missing"},
            {"source_column": "备注", "action": "map", "target_field": ""},
            {"source_column": "姓名", "action": "map", "target_field": "name"},
        ],
    )
    assert rows == [{"row_number": 2, "values": {"name": "张三"}}]
    ledger.delete_stage(job_id)
    assert not path.exists()

    assert ledger.parse_date(None, "日期") is None
    assert ledger.parse_date("2026.08.25", "日期") == date(2026, 8, 25)
    with pytest.raises(ValueError, match="不是可识别日期"):
        ledger.parse_date("二〇二六年", "申请时间")
    assert ledger.parse_list(None) == []
    assert ledger.parse_list(["甲", "甲", "乙"]) == ["甲", "乙"]
    assert ledger.parse_list("甲、乙；甲") == ["甲", "乙"]
    assert ledger.formula_like("@SUM(A1:A2)") is True

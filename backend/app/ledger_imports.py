"""通用台账导入的安全解析、剖析和字段映射。

该模块只把经过限制的单元格值写入当前用户数据根下的私有暂存区；不执行
公式、不保留原文件名，也不在确认前写业务表。真正的权限、事务和撤销由
路由层完成。
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import os
import re
import stat
import unicodedata
import uuid
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from difflib import SequenceMatcher
from pathlib import Path, PurePosixPath
from typing import Any

from python_calamine import CalamineWorkbook

from .config import get_settings
from .problems import ProblemException

MAX_FILE_BYTES = 50 * 1024 * 1024
MAX_SHEETS = 20
MAX_ROWS_PER_SHEET = 50_000
MAX_TOTAL_ROWS = 200_000
MAX_COLUMNS = 200
MAX_CELL_CHARS = 20_000
MAX_ZIP_MEMBERS = 20_000
MAX_ZIP_UNCOMPRESSED = 200 * 1024 * 1024
MAX_ZIP_RATIO = 100
JOB_TTL_SECONDS = 24 * 60 * 60
SUPPORTED_SUFFIXES = {".xlsx", ".xls", ".xlsb", ".ods", ".csv"}


@dataclass(frozen=True)
class FieldSpec:
    key: str
    label: str
    field_type: str
    aliases: tuple[str, ...]
    required: bool = False


PARTY_FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec("party_committee", "所属党委", "text", ("党委", "所属党委", "上级党委"), True),
    FieldSpec("party_branch", "所属党支部", "text", ("党支部", "所属党支部", "支部"), True),
    FieldSpec("name", "姓名", "text", ("姓名", "人员姓名", "名字"), True),
    FieldSpec("gender", "性别", "text", ("性别",)),
    FieldSpec("ethnicity", "民族", "text", ("民族",)),
    FieldSpec("birth_date", "出生日期", "date", ("出生日期", "出生年月", "出生年月日")),
    FieldSpec("education", "文化程度", "text", ("文化程度", "学历", "教育程度")),
    FieldSpec("application_date", "提交入党申请时间", "date", ("入党申请时间", "申请入党时间", "提交入党申请书时间", "申请书时间"), True),
    FieldSpec("conversation_date", "谈话时间", "date", ("谈话时间", "派人谈话时间")),
    FieldSpec("activist_date", "列为积极分子时间", "date", ("积极分子时间", "列为积极分子时间", "确定积极分子时间")),
    FieldSpec("publicity_start_date", "积极分子公示开始时间", "date", ("积极分子公示时间", "公示开始时间")),
    FieldSpec("training_contacts", "培养联系人", "list", ("培养联系人", "联系人")),
    FieldSpec("development_object_date", "列为发展对象时间", "date", ("发展对象时间", "列为发展对象时间", "确定发展对象时间")),
    FieldSpec("training_completed_date", "集中培训完成时间", "date", ("培训完成时间", "集中培训时间")),
    FieldSpec("political_review_completed_date", "政治审查完成时间", "date", ("政审完成时间", "政治审查时间", "政审时间")),
    FieldSpec("pre_review_approved_date", "预审通过时间", "date", ("预审时间", "预审通过时间")),
    FieldSpec("introducers", "入党介绍人", "list", ("入党介绍人", "介绍人")),
    FieldSpec("branch_acceptance_date", "支部大会接收时间", "date", ("支部大会时间", "接收预备党员大会时间", "支部大会接收时间")),
    FieldSpec("committee_approval_date", "党委审批时间", "date", ("党委审批时间", "审批时间")),
    FieldSpec("oath_date", "入党宣誓时间", "date", ("宣誓时间", "入党宣誓时间")),
    FieldSpec("probationary_date", "列为预备党员时间", "date", ("预备党员时间", "列为预备党员时间")),
    FieldSpec("transition_application_date", "转正申请时间", "date", ("转正申请时间", "提交转正申请时间")),
    FieldSpec("transition_branch_meeting_date", "转正支部大会时间", "date", ("转正大会时间", "转正支部大会时间")),
    FieldSpec("transition_approval_date", "转正审批时间", "date", ("转正审批时间",)),
    FieldSpec("converted_date", "预备党员转正时间", "date", ("转正时间", "预备党员转正时间", "正式党员时间")),
)

ARCHIVE_FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec("archive_year", "年度", "number", ("年度", "年份", "考核年度", "归档年度"), True),
    FieldSpec("title", "标题", "text", ("标题", "文件名称", "档案名称", "事项名称"), True),
    FieldSpec("document_no", "文号", "text", ("文号", "文件编号", "档案编号")),
    FieldSpec("summary", "摘要", "textarea", ("摘要", "备注", "说明")),
    FieldSpec("involved_persons", "涉及人员", "list", ("涉及人员", "相关人员")),
    FieldSpec("source_unit", "来源单位", "text", ("来源单位", "发文单位", "原单位")),
    FieldSpec("document_date", "文件日期", "date", ("文件日期", "发文日期", "形成日期")),
    FieldSpec("person_name", "人员姓名", "text", ("姓名", "人员姓名", "被考核人")),
    FieldSpec("person_identifier", "人员标识", "text", ("身份证号", "人员编号", "工号", "唯一标识", "外部ID", "外部编号")),
    FieldSpec("personnel_type", "人员类型", "text", ("人员类型", "编制类型", "身份类别")),
    FieldSpec("organization", "组织单位", "text", ("单位", "部门", "组织", "所在单位", "调入单位")),
    FieldSpec("assessment_result", "考核结果", "text", ("考核结果", "等次", "考核等次")),
    FieldSpec("tags", "标签", "list", ("标签", "分类标签")),
)

PARTY_PROGRESS_FIELDS: dict[str, str] = {
    "conversation_date": "conversation",
    "activist_date": "activist",
    "publicity_start_date": "activist_publicity",
    "development_object_date": "development_object",
    "training_completed_date": "training_completed",
    "political_review_completed_date": "political_review_completed",
    "pre_review_approved_date": "pre_review_approved",
    "branch_acceptance_date": "branch_acceptance",
    "committee_approval_date": "committee_approval",
    "oath_date": "oath",
    "transition_application_date": "transition_application",
    "transition_branch_meeting_date": "transition_branch_meeting",
    "transition_approval_date": "transition_approval",
}


def normalize_header(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).strip().lower()
    return re.sub(r"[\s\-—_./（）()【】\[\]:：]+", "", text)


def safe_field_key(label: str) -> str:
    digest = hashlib.sha256(label.strip().encode("utf-8")).hexdigest()[:12]
    return f"custom_{digest}"


def _problem(code: str, title: str, detail: str, *, status_code: int = 422) -> ProblemException:
    return ProblemException(status_code, code, title, detail)


def validate_filename(filename: str) -> str:
    suffix = Path(filename or "").suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise _problem(
            "LEDGER_FILE_TYPE_UNSUPPORTED",
            "台账格式不支持",
            "请选择 xlsx、xls、xlsb、ods 或 csv 文件；不从 Word 正文猜测台账。",
            status_code=415,
        )
    return suffix


def _validate_zip_container(data: bytes) -> None:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as package:
            members = package.infolist()
            if len(members) > MAX_ZIP_MEMBERS:
                raise _problem("LEDGER_ARCHIVE_TOO_MANY_FILES", "表格容器异常", "压缩成员数量超过安全上限。")
            total = 0
            for member in members:
                path = PurePosixPath(member.filename.replace("\\", "/"))
                if path.is_absolute() or ".." in path.parts:
                    raise _problem("LEDGER_ARCHIVE_PATH_TRAVERSAL", "表格容器异常", "文件包含不安全的内部路径。")
                mode = member.external_attr >> 16
                if stat.S_ISLNK(mode):
                    raise _problem("LEDGER_ARCHIVE_SYMLINK", "表格容器异常", "文件包含不允许的符号链接。")
                total += member.file_size
                if total > MAX_ZIP_UNCOMPRESSED:
                    raise _problem("LEDGER_ARCHIVE_EXPANSION_LIMIT", "表格容器过大", "解压后的内容超过安全上限。")
                if member.file_size and member.compress_size == 0:
                    raise _problem("LEDGER_ARCHIVE_RATIO_INVALID", "表格容器异常", "压缩比例异常。")
                if member.compress_size and member.file_size / member.compress_size > MAX_ZIP_RATIO:
                    raise _problem("LEDGER_ARCHIVE_RATIO_INVALID", "表格容器异常", "压缩比例超过安全上限。")
                lowered = member.filename.lower()
                if "vbaproject.bin" in lowered or "/macros/" in lowered:
                    raise _problem("LEDGER_MACRO_BLOCKED", "检测到宏内容", "为避免执行风险，含宏台账不允许导入。")
                if "/externallinks/" in lowered or lowered.endswith("connections.xml"):
                    raise _problem("LEDGER_EXTERNAL_LINK_BLOCKED", "检测到外部连接", "请先在表格软件中断开外部链接后再导入。")
    except zipfile.BadZipFile as exc:
        raise _problem("LEDGER_CONTAINER_INVALID", "表格文件损坏", "无法读取表格容器。") from exc


def validate_file_bytes(data: bytes, suffix: str) -> None:
    if not data:
        raise _problem("LEDGER_FILE_EMPTY", "台账为空", "请选择包含数据的台账文件。")
    if len(data) > MAX_FILE_BYTES:
        raise _problem("LEDGER_FILE_TOO_LARGE", "台账过大", "单个台账文件不能超过 50 MiB。", status_code=413)
    if suffix in {".xlsx", ".xlsb", ".ods"}:
        if not data.startswith(b"PK"):
            raise _problem("LEDGER_FILE_SIGNATURE_INVALID", "台账格式不匹配", "文件扩展名与实际容器不一致。")
        _validate_zip_container(data)
    elif suffix == ".xls" and not data.startswith(bytes.fromhex("D0CF11E0A1B11AE1")):
        raise _problem("LEDGER_FILE_SIGNATURE_INVALID", "台账格式不匹配", "该文件不是有效的 Excel 97-2003 工作簿。")


def _clean_cell(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
            return None
        return value
    text = unicodedata.normalize("NFKC", str(value)).replace("\x00", "").strip()
    if len(text) > MAX_CELL_CHARS:
        raise _problem("LEDGER_CELL_TOO_LONG", "单元格内容过长", "单个单元格超过 20000 个字符。")
    return text


def _decode_csv(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise _problem("LEDGER_CSV_ENCODING_INVALID", "CSV 编码无法识别", "请将 CSV 保存为 UTF-8 或 GB18030 后重试。")


def _parse_csv(data: bytes) -> dict[str, list[list[Any]]]:
    csv.field_size_limit(MAX_CELL_CHARS * MAX_COLUMNS)
    reader = csv.reader(io.StringIO(_decode_csv(data), newline=""))
    rows: list[list[Any]] = []
    for index, row in enumerate(reader, start=1):
        if index > MAX_ROWS_PER_SHEET:
            raise _problem("LEDGER_ROW_LIMIT", "台账行数过多", "单个工作表最多导入 50000 行。")
        if len(row) > MAX_COLUMNS:
            raise _problem("LEDGER_COLUMN_LIMIT", "台账列数过多", "单个工作表最多导入 200 列。")
        rows.append([_clean_cell(value) for value in row])
    return {"CSV": rows}


def _parse_workbook(data: bytes) -> tuple[dict[str, list[list[Any]]], list[dict[str, Any]]]:
    try:
        workbook = CalamineWorkbook.from_filelike(io.BytesIO(data))
    except Exception as exc:
        raise _problem("LEDGER_WORKBOOK_INVALID", "台账无法读取", "文件可能损坏、加密或使用了不受支持的容器。") from exc
    try:
        names = list(workbook.sheet_names)
        if not names:
            raise _problem("LEDGER_NO_SHEET", "台账没有工作表", "请至少保留一个可读取工作表。")
        if len(names) > MAX_SHEETS:
            raise _problem("LEDGER_SHEET_LIMIT", "工作表过多", "单个台账最多包含 20 个工作表。")
        metadata_by_name: dict[str, Any] = {
            str(getattr(item, "name", "")): item for item in workbook.sheets_metadata
        }
        sheets: dict[str, list[list[Any]]] = {}
        metadata: list[dict[str, Any]] = []
        total_rows = 0
        for name in names:
            sheet = workbook.get_sheet_by_name(name)
            rows: list[list[Any]] = []
            for index, row in enumerate(sheet.iter_rows(), start=1):
                if index > MAX_ROWS_PER_SHEET:
                    raise _problem("LEDGER_ROW_LIMIT", "台账行数过多", f"工作表“{name}”超过 50000 行。")
                values = list(row)
                if len(values) > MAX_COLUMNS:
                    raise _problem("LEDGER_COLUMN_LIMIT", "台账列数过多", f"工作表“{name}”超过 200 列。")
                rows.append([_clean_cell(value) for value in values])
            total_rows += len(rows)
            if total_rows > MAX_TOTAL_ROWS:
                raise _problem("LEDGER_TOTAL_ROW_LIMIT", "台账总体过大", "所有工作表合计不能超过 200000 行。")
            item = metadata_by_name.get(name)
            visible = str(getattr(item, "visible", getattr(item, "state", "visible"))).lower()
            hidden = "hidden" in visible and "visible" not in visible
            sheets[name] = rows
            metadata.append({"name": name, "rows": len(rows), "columns": max((len(row) for row in rows), default=0), "hidden": hidden})
        return sheets, metadata
    finally:
        workbook.close()


def parse_table(data: bytes, suffix: str) -> tuple[dict[str, list[list[Any]]], list[dict[str, Any]]]:
    validate_file_bytes(data, suffix)
    if suffix == ".csv":
        sheets = _parse_csv(data)
        return sheets, [{"name": "CSV", "rows": len(sheets["CSV"]), "columns": max((len(row) for row in sheets["CSV"]), default=0), "hidden": False}]
    return _parse_workbook(data)


def detect_header_row(rows: list[list[Any]]) -> int:
    if not rows:
        return 1
    best_row = 1
    best_score = -1.0
    for index, row in enumerate(rows[:20], start=1):
        values = [str(value).strip() for value in row if value not in (None, "")]
        if not values:
            continue
        distinct = len({normalize_header(value) for value in values if normalize_header(value)})
        text_like = sum(not re.fullmatch(r"[-+]?\d+(?:\.\d+)?", value) for value in values)
        score = distinct * 3 + text_like - index * 0.05
        if score > best_score:
            best_score = score
            best_row = index
    return best_row


def _formula_like(value: Any) -> bool:
    if not isinstance(value, str) or not value:
        return False
    if value[0] not in "=+-@":
        return False
    return not bool(re.fullmatch(r"[-+]?\d+(?:\.\d+)?", value))


def _parse_iso_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    normalized = text.replace("年", "-").replace("月", "-").replace("日", "").replace("/", "-").replace(".", "-")
    try:
        if re.fullmatch(r"\d{4}-\d{1,2}-\d{1,2}", normalized):
            year, month, day = (int(item) for item in normalized.split("-"))
            return date(year, month, day)
        if re.fullmatch(r"\d{4}-\d{1,2}", normalized):
            year, month = (int(item) for item in normalized.split("-"))
            return date(year, month, 1)
        if re.fullmatch(r"\d{8}", normalized):
            return date(int(normalized[:4]), int(normalized[4:6]), int(normalized[6:]))
    except ValueError:
        return None
    return None


def infer_type(values: Iterable[Any]) -> dict[str, Any]:
    non_empty = [value for value in values if value not in (None, "")]
    if not non_empty:
        return {"type": "empty", "confidence": 1.0, "date_ambiguous": False}
    date_hits = sum(_parse_iso_date(value) is not None for value in non_empty)
    number_hits = 0
    boolean_hits = 0
    ambiguous = False
    for value in non_empty:
        text = str(value).strip()
        try:
            float(value)
            number_hits += 1
        except (TypeError, ValueError):
            pass
        if text.lower() in {"true", "false", "是", "否", "有", "无"}:
            boolean_hits += 1
        if re.fullmatch(r"\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}", text):
            ambiguous = True
    total = len(non_empty)
    candidates = [(date_hits, "date"), (number_hits, "number"), (boolean_hits, "boolean")]
    hits, kind = max(candidates)
    if hits / total < 0.8:
        kind, hits = "text", total
    return {"type": kind, "confidence": round(hits / total, 3), "date_ambiguous": ambiguous}


def fields_for_target(target_type: str, custom_fields: Iterable[dict[str, Any]] = ()) -> list[FieldSpec]:
    if target_type == "party_development":
        base = list(PARTY_FIELDS)
    elif target_type == "archive":
        base = list(ARCHIVE_FIELDS)
    else:
        raise _problem("LEDGER_TARGET_INVALID", "归档目标无效", "请选择发展党员或重要档案目标。")
    for item in custom_fields:
        key = str(item.get("key", "")).strip()
        label = str(item.get("label", key)).strip()
        field_type = str(item.get("type", item.get("field_type", "text"))).strip()
        if key and label and re.fullmatch(r"[a-z][a-z0-9_]*", key):
            aliases = tuple(str(value) for value in item.get("aliases", []) if str(value).strip())
            base.append(FieldSpec(key, label, field_type, aliases + (label,), bool(item.get("required"))))
    return base


def suggest_field(header: str, fields: Iterable[FieldSpec]) -> dict[str, Any] | None:
    normalized = normalize_header(header)
    if not normalized:
        return None
    exact: list[FieldSpec] = []
    scored: list[tuple[float, FieldSpec]] = []
    for field in fields:
        candidates = {normalize_header(field.label), *(normalize_header(alias) for alias in field.aliases)}
        if normalized in candidates:
            exact.append(field)
            continue
        score = max((SequenceMatcher(None, normalized, candidate).ratio() for candidate in candidates if candidate), default=0.0)
        scored.append((score, field))
    if len(exact) == 1:
        field = exact[0]
        return {"target_field": field.key, "label": field.label, "field_type": field.field_type, "confidence": "high", "score": 1.0, "confirmed": True}
    if len(exact) > 1:
        return {"target_field": None, "confidence": "conflict", "score": 1.0, "confirmed": False, "candidates": [item.key for item in exact]}
    scored.sort(key=lambda item: item[0], reverse=True)
    if scored and scored[0][0] >= 0.78:
        score, field = scored[0]
        tied = [item for item in scored if score - item[0] < 0.04]
        if len(tied) > 1:
            return {"target_field": None, "confidence": "conflict", "score": round(score, 3), "confirmed": False, "candidates": [item[1].key for item in tied[:4]]}
        return {"target_field": field.key, "label": field.label, "field_type": field.field_type, "confidence": "medium", "score": round(score, 3), "confirmed": False}
    return None


def profile_sheet(rows: list[list[Any]], header_row: int, fields: Iterable[FieldSpec]) -> dict[str, Any]:
    if header_row < 1 or header_row > len(rows):
        raise _problem("LEDGER_HEADER_ROW_INVALID", "表头行无效", "所选表头行超出工作表范围。")
    raw_headers = rows[header_row - 1]
    headers = [str(value or "").strip() for value in raw_headers]
    non_empty_headers = [header for header in headers if header]
    if not non_empty_headers:
        raise _problem("LEDGER_HEADER_EMPTY", "表头为空", "请选择包含字段名称的行。")
    normalized_headers = [normalize_header(header) for header in non_empty_headers]
    duplicates = sorted({value for value in normalized_headers if value and normalized_headers.count(value) > 1})
    data_rows = rows[header_row:]
    columns: list[dict[str, Any]] = []
    for index, header in enumerate(headers):
        if not header:
            continue
        values = [row[index] if index < len(row) else None for row in data_rows]
        non_empty = [value for value in values if value not in (None, "")]
        unique_values = {json.dumps(value, ensure_ascii=False, sort_keys=True) for value in non_empty}
        inferred = infer_type(non_empty)
        columns.append({
            "index": index,
            "header": header,
            "non_empty": len(non_empty),
            "empty": len(values) - len(non_empty),
            "unique": len(unique_values),
            "samples": non_empty[:5],
            "inferred_type": inferred["type"],
            "type_confidence": inferred["confidence"],
            "date_ambiguous": inferred["date_ambiguous"],
            "formula_like": sum(_formula_like(value) for value in non_empty),
            "suggestion": suggest_field(header, fields),
        })
    signature = hashlib.sha256("\x1f".join(normalize_header(item["header"]) for item in columns).encode("utf-8")).hexdigest()
    return {
        "header_row": header_row,
        "total_rows": len(data_rows),
        "columns": columns,
        "duplicate_headers": duplicates,
        "header_signature": signature,
    }


def _staging_root() -> Path:
    root = get_settings().data_dir / ".ledger-import-staging"
    root.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(root, 0o700)
    except OSError:
        pass
    return root.resolve()


def stage_path(job_id: str) -> Path:
    try:
        normalized = str(uuid.UUID(job_id))
    except ValueError as exc:
        raise _problem("LEDGER_JOB_ID_INVALID", "导入任务无效", "导入任务标识格式不正确。", status_code=400) from exc
    return _staging_root() / f"{normalized}.json"


def save_stage(job_id: str, sheets: dict[str, list[list[Any]]]) -> None:
    path = stage_path(job_id)
    temporary = path.with_suffix(".tmp")
    payload = json.dumps({"sheets": sheets}, ensure_ascii=False, separators=(",", ":"))
    temporary.write_text(payload, encoding="utf-8")
    try:
        os.chmod(temporary, 0o600)
    except OSError:
        pass
    temporary.replace(path)


def load_stage(job_id: str) -> dict[str, list[list[Any]]]:
    path = stage_path(job_id)
    if not path.is_file():
        raise _problem("LEDGER_STAGE_MISSING", "台账暂存已失效", "请重新选择文件并开始导入。", status_code=410)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise _problem("LEDGER_STAGE_INVALID", "台账暂存损坏", "请重新选择文件并开始导入。", status_code=410) from exc
    sheets = payload.get("sheets")
    if not isinstance(sheets, dict):
        raise _problem("LEDGER_STAGE_INVALID", "台账暂存损坏", "请重新选择文件并开始导入。", status_code=410)
    return sheets


def delete_stage(job_id: str) -> None:
    path = stage_path(job_id)
    try:
        path.unlink(missing_ok=True)
    except OSError:
        # 暂存过期清理由下一次启动继续处理；业务提交状态不受影响。
        return


def mapped_rows(job_id: str, sheet_name: str, header_row: int, mappings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sheets = load_stage(job_id)
    rows = sheets.get(sheet_name)
    if rows is None:
        raise _problem("LEDGER_SHEET_NOT_FOUND", "工作表不存在", "所选工作表已不在暂存数据中。")
    if header_row < 1 or header_row > len(rows):
        raise _problem("LEDGER_HEADER_ROW_INVALID", "表头行无效", "所选表头行超出工作表范围。")
    headers = [str(value or "").strip() for value in rows[header_row - 1]]
    index_by_header: dict[str, int] = {}
    for index, header in enumerate(headers):
        normalized = normalize_header(header)
        if normalized and normalized not in index_by_header:
            index_by_header[normalized] = index
    result: list[dict[str, Any]] = []
    for row_number, row in enumerate(rows[header_row:], start=header_row + 1):
        values: dict[str, Any] = {}
        for mapping in mappings:
            if mapping.get("action") == "ignore":
                continue
            source = normalize_header(mapping.get("source_column", ""))
            index = index_by_header.get(source)
            if index is None:
                continue
            target = str(mapping.get("target_field") or "")
            if target:
                values[target] = row[index] if index < len(row) else None
        if any(value not in (None, "") for value in values.values()):
            result.append({"row_number": row_number, "values": values})
    return result


def parse_date(value: Any, field_label: str) -> date | None:
    if value in (None, ""):
        return None
    parsed = _parse_iso_date(value)
    if parsed is None:
        raise ValueError(f"{field_label}不是可识别日期")
    return parsed


def parse_list(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, list):
        candidates = value
    else:
        candidates = re.split(r"[,，;；、\n]+", str(value))
    return list(dict.fromkeys(item.strip() for item in map(str, candidates) if item.strip()))[:100]


def formula_like(value: Any) -> bool:
    return _formula_like(value)

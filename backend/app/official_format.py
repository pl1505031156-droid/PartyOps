"""GB/T 9704-2012 公文本机排版工具。

该模块只由 ``partyops-client://official-format/<随机事务号>`` 的本机协议入口
调用。文档字节、文件名、路径、摘要和排版结果不会进入 PartyOps 主机 API、
协同链路或数据库。DOCX 直接修改 OOXML；DOC/WPS 仅调用本机办公套件转换。
"""

from __future__ import annotations

import html
import ipaddress
import json
import os
import re
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import urllib.parse
import uuid
import webbrowser
import zipfile
from dataclasses import asdict, dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

from lxml import etree

VERSION = "1.4.5-rc.3"
MAX_FILE_BYTES = 50 * 1024 * 1024
MAX_PACKAGE_BYTES = 512 * 1024 * 1024
MAX_ZIP_RATIO = 200
IDLE_TIMEOUT_SECONDS = 15 * 60
SUPPORTED_EXTENSIONS = {".docx", ".doc", ".wps"}
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PR = "http://schemas.openxmlformats.org/package/2006/relationships"
CT = "http://schemas.openxmlformats.org/package/2006/content-types"
NS = {"w": W, "r": R}
XML_PARSER = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False)

# 版心宽 156 mm，正文三号字按 16 pt 计算；WordprocessingML 的
# charSpace 单位是“与 Normal 字号之差 × 4096”。该值把每行稳定限定为
# 28 个网格字符，而不是仅设置一个实际上不约束字数的 linesAndChars 标记。
GRID_CHARACTER_SPACE = -848
GRID_LINE_PITCH = "560"
PAGE_FOOTER_DISTANCE = "1417"  # 约 25 mm，使 4 号页码上缘距版心下缘约 7 mm。


class OfficialFormatError(RuntimeError):
    def __init__(self, code: str, title: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.title = title
        self.detail = detail


@dataclass(frozen=True)
class FormatIssue:
    code: str
    severity: str
    title: str
    detail: str
    clause: str


@dataclass(frozen=True)
class FormatReport:
    compliant: bool
    paragraph_count: int
    table_count: int
    changed_count: int
    issues: tuple[FormatIssue, ...]

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["issues"] = [asdict(item) for item in self.issues]
        return payload


@dataclass
class LocalDocument:
    source: Path
    original_stem: str
    converted: bool
    output: Path | None = None
    report: FormatReport | None = None


def _qn(local: str) -> str:
    return f"{{{W}}}{local}"


def _safe_xml(payload: bytes, part: str) -> etree._Element:
    if len(payload) > 32 * 1024 * 1024:
        raise OfficialFormatError(
            "OOXML_PART_TOO_LARGE", "文档结构异常", f"{part} 超出安全解析上限。"
        )
    try:
        return etree.fromstring(payload, parser=XML_PARSER)
    except etree.XMLSyntaxError as exc:
        raise OfficialFormatError(
            "OOXML_XML_INVALID", "文档结构损坏", f"{part} 不是有效的 OOXML。"
        ) from exc


def _validated_members(package: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    total = 0
    members: list[zipfile.ZipInfo] = []
    for info in package.infolist():
        path = PurePosixPath(info.filename)
        if path.is_absolute() or ".." in path.parts or "\\" in info.filename:
            raise OfficialFormatError(
                "OOXML_PATH_UNSAFE", "文档包路径异常", "压缩包包含越界路径，已拒绝处理。"
            )
        total += info.file_size
        if total > MAX_PACKAGE_BYTES:
            raise OfficialFormatError(
                "OOXML_EXPANSION_LIMIT", "文档解压体积异常", "文档展开后超过 512 MiB 安全上限。"
            )
        if info.compress_size > 0 and info.file_size / info.compress_size > MAX_ZIP_RATIO:
            raise OfficialFormatError(
                "OOXML_COMPRESSION_RATIO_UNSAFE", "文档压缩比异常", "文档疑似压缩炸弹，已拒绝处理。"
            )
        members.append(info)
    required = {"[Content_Types].xml", "word/document.xml"}
    if not required.issubset({item.filename for item in members}):
        raise OfficialFormatError(
            "OOXML_REQUIRED_PART_MISSING", "DOCX 结构不完整", "缺少正文或内容类型定义。"
        )
    return members


def _read_core_parts(path: Path) -> tuple[etree._Element, etree._Element | None]:
    try:
        with zipfile.ZipFile(path) as package:
            _validated_members(package)
            document = _safe_xml(package.read("word/document.xml"), "word/document.xml")
            settings = (
                _safe_xml(package.read("word/settings.xml"), "word/settings.xml")
                if "word/settings.xml" in package.namelist()
                else None
            )
            return document, settings
    except zipfile.BadZipFile as exc:
        raise OfficialFormatError(
            "DOCX_PACKAGE_INVALID", "DOCX 文件损坏", "文件不是有效的 OOXML 压缩包。"
        ) from exc


def _paragraph_text(paragraph: etree._Element) -> str:
    return "".join(paragraph.xpath(".//w:t/text()", namespaces=NS)).strip()


def _paragraph_role(text: str, *, first_body: bool) -> str:
    compact = text.strip()
    if re.fullmatch(r"\d{6}", compact):
        return "copy_number"
    if re.match(r"^(?:绝密|机密|秘密)(?:\s*[★☆]\s*|\s+).+", compact):
        return "security"
    if compact in {"特急", "加急", "平急"}:
        return "urgency"
    if len(compact) <= 50 and re.search(r"(?:文件|命令|令|纪要)$", compact):
        return "issuing_authority"
    if re.fullmatch(r"(?:.+〔\d{4}〕\d+号|第\s*\d+\s*号)", compact):
        return "document_number"
    if compact.startswith("签发人：") or compact.startswith("签发人:"):
        return "signatory"
    if first_body and compact and len(compact) <= 80 and not re.search(r"[。；！？!?]$", compact):
        return "title"
    if re.match(r"^[一二三四五六七八九十百]+、", compact):
        return "heading1"
    if re.match(r"^（[一二三四五六七八九十百]+）", compact):
        return "heading2"
    if re.match(r"^\d{1,3}[.]", compact):
        return "heading3"
    if re.match(r"^（\d{1,3}）", compact):
        return "heading4"
    if re.fullmatch(r"附件\s*\d*", compact):
        return "attachment_heading"
    if re.match(r"^附件(?:\s*[:：]|\s*\d+[.．、])", compact):
        return "attachment"
    if compact.startswith("抄送：") or compact.startswith("抄送:"):
        return "copy_recipient"
    if re.search(r"\d{4}年\d{1,2}月\d{1,2}日印发$", compact):
        return "imprint"
    if re.match(r"^(?:出席|请假|列席)[：:]", compact):
        return "attendance"
    if compact.startswith("（") and compact.endswith("）") and len(compact) <= 120:
        return "note"
    if compact.endswith(("：", ":")) and len(compact) <= 120:
        return "addressee"
    if re.fullmatch(r"[〇○零一二三四五六七八九十百千\d]{4}年[〇○零一二三四五六七八九十百千\d]{1,3}月[〇○零一二三四五六七八九十百千\d]{1,3}日", compact):
        return "date"
    return "body"


def _classify_document_paragraphs(paragraphs: list[etree._Element]) -> list[tuple[etree._Element, str]]:
    """按上下文识别公文要素，避免把份号、密级或发文机关误当标题。

    规则只依据标准中可验证的外观信号。无法可靠判定的段落保持为正文，
    不猜测政治语义、机关层级或印章位置。
    """

    texts = [_paragraph_text(item) for item in paragraphs]
    kind = "general"
    for text in texts:
        compact = text.strip()
        if compact.endswith(("命令", "令")):
            kind = "order"
            break
        if compact.endswith("纪要"):
            kind = "minutes"
            break
        if compact.endswith("文件"):
            kind = "letter" if "函" in compact else "general"
            break

    classified: list[tuple[etree._Element, str]] = []
    title_seen = False
    for index, paragraph in enumerate(paragraphs):
        text = texts[index]
        if not text:
            continue
        allow_title = not title_seen and kind not in {"order", "minutes"}
        role = _paragraph_role(text, first_body=allow_title)
        if role == "title":
            title_seen = True
        classified.append((paragraph, role))

    # 成文日期上一条短机构名称通常是署名；只有同时满足位置、长度和机关后缀
    # 才识别，避免把普通正文静默右对齐。
    signature_suffixes = (
        "委员会", "人民政府", "党组", "党委", "党支部", "办公室", "工作部", "管理局", "中心", "机关",
    )
    for index, (paragraph, role) in enumerate(classified[:-1]):
        next_role = classified[index + 1][1]
        text = _paragraph_text(paragraph)
        if role == "body" and next_role == "date" and len(text) <= 50 and text.endswith(signature_suffixes):
            classified[index] = (paragraph, "signature")
    return classified


_CJK = r"\u3400-\u9fff"


def normalize_chinese_punctuation(text: str) -> tuple[str, int]:
    """只改 CJK 邻接的明确标点，保护 URL、邮箱、小数、缩写和条款编号。"""

    protected: dict[str, str] = {}

    def shelter(match: re.Match[str]) -> str:
        key = f"\ue000{len(protected)}\ue001"
        protected[key] = match.group(0)
        return key

    value = re.sub(
        r"(?:https?://|www\.)[^\s<>]+|[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}|\b\d+(?:\.\d+)+\b|\b[A-Z](?:\.[A-Z])+(?:\.)?",
        shelter,
        text,
    )
    before = value
    replacements = {",": "，", ":": "：", ";": "；", "?": "？", "!": "！"}
    for source, target in replacements.items():
        value = re.sub(f"(?<=[{_CJK}）》”’]){re.escape(source)}(?=[{_CJK}（《“‘]|$)", target, value)
    value = re.sub(rf"(?<=[{_CJK}])\((?=[{_CJK}])", "（", value)
    value = re.sub(rf"(?<=[{_CJK}])\)(?=[{_CJK}，。；：！？]|$)", "）", value)
    value = re.sub(f'(?<=[{_CJK}])"(?=[{_CJK}])', "“", value)
    value = re.sub(f'(?<=[{_CJK}])"(?=[{_CJK}，。；：！？]|$)', "”", value)
    changes = sum(1 for left, right in zip(before, value) if left != right) + abs(len(before) - len(value))
    for key, original in protected.items():
        value = value.replace(key, original)
    return value, changes


def _get_or_add(parent: etree._Element, local: str, *, first: bool = False) -> etree._Element:
    node = parent.find(_qn(local))
    if node is None:
        node = etree.Element(_qn(local))
        if first:
            parent.insert(0, node)
        else:
            parent.append(node)
    return node


def _set_value(parent: etree._Element, local: str, value: str) -> etree._Element:
    node = _get_or_add(parent, local)
    node.set(_qn("val"), value)
    return node


def _remove_children(parent: etree._Element, names: Iterable[str]) -> None:
    for name in names:
        for node in list(parent.findall(_qn(name))):
            parent.remove(node)


def _format_run(
    run: etree._Element,
    *,
    font: str,
    size: int,
    bold: bool,
    color: str = "000000",
    preserve_emphasis: bool = False,
) -> None:
    properties = _get_or_add(run, "rPr", first=True)
    fonts = _get_or_add(properties, "rFonts")
    for name in ("ascii", "hAnsi", "eastAsia", "cs"):
        fonts.set(_qn(name), font)
    _set_value(properties, "sz", str(size))
    _set_value(properties, "szCs", str(size))
    if bold or not preserve_emphasis:
        _set_value(properties, "b", "1" if bold else "0")
        _set_value(properties, "bCs", "1" if bold else "0")
    if not preserve_emphasis:
        _set_value(properties, "i", "0")
        _set_value(properties, "iCs", "0")
        _set_value(properties, "u", "none")
    _set_value(properties, "color", color)
    _set_value(properties, "vanish", "0")


def _format_paragraph(paragraph: etree._Element, role: str) -> int:
    properties = _get_or_add(paragraph, "pPr", first=True)
    _remove_children(properties, ("jc", "spacing", "ind", "keepNext", "keepLines", "pageBreakBefore"))
    font, size, bold, alignment, first_indent, left_indent, color = {
        "copy_number": ("仿宋_GB2312", 32, False, "left", 0, 0, "000000"),
        "security": ("黑体", 32, False, "left", 0, 0, "000000"),
        "urgency": ("黑体", 32, False, "left", 0, 0, "000000"),
        "issuing_authority": ("方正小标宋简体", 54, False, "center", 0, 0, "FF0000"),
        "document_number": ("仿宋_GB2312", 32, False, "center", 0, 0, "000000"),
        "signatory": ("仿宋_GB2312", 32, False, "right", 0, 0, "000000"),
        "title": ("方正小标宋简体", 44, False, "center", 0, 0, "000000"),
        "addressee": ("仿宋_GB2312", 32, False, "left", 0, 0, "000000"),
        "heading1": ("黑体", 32, False, "left", 0, 0, "000000"),
        "heading2": ("楷体_GB2312", 32, False, "left", 0, 0, "000000"),
        "heading3": ("仿宋_GB2312", 32, True, "left", 0, 0, "000000"),
        "heading4": ("仿宋_GB2312", 32, False, "left", 0, 0, "000000"),
        "attachment": ("仿宋_GB2312", 32, False, "left", 0, 640, "000000"),
        "attachment_heading": ("黑体", 32, False, "left", 0, 0, "000000"),
        "signature": ("仿宋_GB2312", 32, False, "right", 0, 0, "000000"),
        "date": ("仿宋_GB2312", 32, False, "right", 0, 0, "000000"),
        "note": ("仿宋_GB2312", 32, False, "left", 0, 640, "000000"),
        "copy_recipient": ("仿宋_GB2312", 28, False, "left", 0, 320, "000000"),
        "imprint": ("仿宋_GB2312", 28, False, "right", 0, 320, "000000"),
        "attendance": ("仿宋_GB2312", 32, False, "left", 0, 640, "000000"),
        "body": ("仿宋_GB2312", 32, False, "both", 640, 0, "000000"),
    }[role]
    _set_value(properties, "jc", alignment)
    spacing = _get_or_add(properties, "spacing")
    spacing.set(_qn("before"), "0")
    spacing.set(_qn("after"), "0")
    spacing.set(_qn("line"), "560")
    spacing.set(_qn("lineRule"), "exact")
    indentation = _get_or_add(properties, "ind")
    indentation.set(_qn("firstLine"), str(first_indent))
    indentation.set(_qn("left"), str(left_indent))
    if role.startswith("heading") or role == "attachment_heading":
        _get_or_add(properties, "keepNext")
        _get_or_add(properties, "keepLines")
    if role == "attachment_heading":
        _get_or_add(properties, "pageBreakBefore")
    changes = 0
    for text_node in paragraph.xpath(".//w:t", namespaces=NS):
        normalized, count = normalize_chinese_punctuation(text_node.text or "")
        text_node.text = normalized
        changes += count
    for run in paragraph.xpath(".//w:r", namespaces=NS):
        _format_run(
            run,
            font=font,
            size=size,
            bold=bold,
            color=color,
            preserve_emphasis=role == "body",
        )
    return changes + 1


def _configure_sections(document: etree._Element) -> int:
    changed = 0
    for section in document.xpath(".//w:sectPr", namespaces=NS):
        size = _get_or_add(section, "pgSz")
        size.set(_qn("w"), "11906")
        size.set(_qn("h"), "16838")
        size.attrib.pop(_qn("orient"), None)
        margins = _get_or_add(section, "pgMar")
        for name, value in {
            "top": "2098", "bottom": "1984", "left": "1587", "right": "1474",
            "header": "851", "footer": PAGE_FOOTER_DISTANCE, "gutter": "0",
        }.items():
            margins.set(_qn(name), value)
        grid = _get_or_add(section, "docGrid")
        grid.set(_qn("type"), "linesAndChars")
        grid.set(_qn("linePitch"), GRID_LINE_PITCH)
        grid.set(_qn("charSpace"), str(GRID_CHARACTER_SPACE))
        changed += 1
    return changed


def _configure_normal_style(styles: etree._Element | None) -> int:
    """校准 Normal 字号，使 docGrid 的 28 字计算有确定基准。"""

    if styles is None:
        return 0
    normal = next(
        (
            item
            for item in styles.findall(_qn("style"))
            if item.get(_qn("styleId")) == "Normal" or item.get(_qn("default")) == "1"
        ),
        None,
    )
    if normal is None:
        normal = etree.Element(_qn("style"))
        normal.set(_qn("type"), "paragraph")
        normal.set(_qn("default"), "1")
        normal.set(_qn("styleId"), "Normal")
        styles.insert(0, normal)
    run_properties = _get_or_add(normal, "rPr")
    fonts = _get_or_add(run_properties, "rFonts")
    for name in ("ascii", "hAnsi", "eastAsia", "cs"):
        fonts.set(_qn(name), "仿宋_GB2312")
    _set_value(run_properties, "sz", "32")
    _set_value(run_properties, "szCs", "32")
    return 1


def _configure_east_asian_typography(settings: etree._Element) -> None:
    """启用中文行首行尾禁则与标点压缩，避免标点孤悬到下一行。"""

    _get_or_add(settings, "kinsoku")
    _set_value(settings, "characterSpacingControl", "compressPunctuation")
    _set_value(settings, "noPunctuationKerning", "0")


def _format_tables(document: etree._Element) -> int:
    changed = 0
    for table in document.xpath(".//w:tbl", namespaces=NS):
        properties = _get_or_add(table, "tblPr", first=True)
        layout = _get_or_add(properties, "tblLayout")
        layout.set(_qn("type"), "fixed")
        for cell in table.xpath(".//w:tc", namespaces=NS):
            cell_properties = _get_or_add(cell, "tcPr", first=True)
            _set_value(cell_properties, "vAlign", "center")
            margins = _get_or_add(cell_properties, "tcMar")
            for side, width in (("top", "0"), ("bottom", "0"), ("left", "72"), ("right", "72")):
                node = _get_or_add(margins, side)
                node.set(_qn("w"), width)
                node.set(_qn("type"), "dxa")
            for paragraph in cell.xpath("./w:p", namespaces=NS):
                current_properties = paragraph.find(_qn("pPr"))
                current_alignment = None
                if current_properties is not None and current_properties.find(_qn("jc")) is not None:
                    current_alignment = current_properties.find(_qn("jc")).get(_qn("val"))
                _format_paragraph(paragraph, "body")
                ppr = _get_or_add(paragraph, "pPr", first=True)
                _set_value(ppr, "jc", current_alignment or "left")
                _get_or_add(ppr, "ind").set(_qn("firstLine"), "0")
        changed += 1
    return changed


def _footer_xml(alignment: str) -> bytes:
    root = etree.Element(_qn("ftr"), nsmap={"w": W})
    _append_page_footer_paragraph(root, alignment)
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


def _append_page_footer_paragraph(root: etree._Element, alignment: str) -> etree._Element:
    paragraph = etree.SubElement(root, _qn("p"))
    ppr = etree.SubElement(paragraph, _qn("pPr"))
    jc = etree.SubElement(ppr, _qn("jc"))
    jc.set(_qn("val"), alignment)
    for text, field_type in (("— ", None), ("PAGE", "field"), (" —", None)):
        run = etree.SubElement(paragraph, _qn("r"))
        _format_run(run, font="宋体", size=28, bold=False)
        if field_type:
            start = etree.SubElement(run, _qn("fldChar"))
            start.set(_qn("fldCharType"), "begin")
            instruction = etree.SubElement(run, _qn("instrText"))
            instruction.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
            instruction.text = " PAGE "
            separator = etree.SubElement(run, _qn("fldChar"))
            separator.set(_qn("fldCharType"), "separate")
            result = etree.SubElement(run, _qn("t"))
            result.text = "1"
            end = etree.SubElement(run, _qn("fldChar"))
            end.set(_qn("fldCharType"), "end")
        else:
            node = etree.SubElement(run, _qn("t"))
            node.text = text
    return paragraph


def _add_page_footers(
    document: etree._Element,
    settings: etree._Element,
    relationships: etree._Element,
    content_types: etree._Element,
    package: zipfile.ZipFile,
) -> dict[str, bytes]:
    """补齐奇偶页外侧页码，并保留已有页脚的文字、图片和关系。"""

    relationship_ids = {
        item.get("Id", "") for item in relationships.findall(f"{{{PR}}}Relationship")
    }
    relationship_targets = {
        item.get("Id", ""): item.get("Target", "")
        for item in relationships.findall(f"{{{PR}}}Relationship")
        if item.get("Type", "") == f"{R}/footer" and item.get("TargetMode", "") != "External"
    }
    next_index = 1

    def next_id() -> str:
        nonlocal next_index
        while f"rIdPartyOpsFooter{next_index}" in relationship_ids:
            next_index += 1
        value = f"rIdPartyOpsFooter{next_index}"
        relationship_ids.add(value)
        next_index += 1
        return value

    outputs: dict[str, bytes] = {}
    references: dict[str, str] = {}
    sections = document.xpath(".//w:sectPr", namespaces=NS)
    for kind, filename, alignment in (
        ("default", "footer-partyops-odd.xml", "right"),
        ("even", "footer-partyops-even.xml", "left"),
    ):
        existing_refs = [
            ref
            for section in sections
            for ref in section.findall(_qn("footerReference"))
            if ref.get(_qn("type"), "default") == kind
        ]
        usable_ids: list[str] = []
        for reference in existing_refs:
            rel_id = reference.get(f"{{{R}}}id", "")
            target_name = relationship_targets.get(rel_id, "")
            target_path = PurePosixPath(target_name.lstrip("/"))
            if target_path.is_absolute() or ".." in target_path.parts:
                continue
            part_name = str(target_path)
            if not part_name.startswith("word/"):
                part_name = f"word/{part_name}"
            if part_name not in package.namelist():
                continue
            root = _safe_xml(outputs.get(part_name, package.read(part_name)), part_name)
            if not root.xpath(".//w:instrText[contains(translate(., 'page', 'PAGE'), 'PAGE')]", namespaces=NS):
                _append_page_footer_paragraph(root, alignment)
            outputs[part_name] = etree.tostring(
                root, xml_declaration=True, encoding="UTF-8", standalone=True
            )
            usable_ids.append(rel_id)
        if usable_ids:
            references[kind] = usable_ids[0]
            continue

        rel_id = next_id()
        relationship = etree.SubElement(relationships, f"{{{PR}}}Relationship")
        relationship.set("Id", rel_id)
        relationship.set("Type", f"{R}/footer")
        relationship.set("Target", filename)
        references[kind] = rel_id
        outputs[f"word/{filename}"] = _footer_xml(alignment)
        if not content_types.xpath(
            "./ct:Override[@PartName=$part]",
            namespaces={"ct": CT},
            part=f"/word/{filename}",
        ):
            override = etree.SubElement(content_types, f"{{{CT}}}Override")
            override.set("PartName", f"/word/{filename}")
            override.set("ContentType", "application/vnd.openxmlformats-officedocument.wordprocessingml.footer+xml")
    for section in sections:
        present = {
            ref.get(_qn("type"), "default")
            for ref in section.findall(_qn("footerReference"))
        }
        insert_at = 0
        for kind, rel_id in references.items():
            if kind in present:
                continue
            reference = etree.Element(_qn("footerReference"))
            reference.set(_qn("type"), kind)
            reference.set(f"{{{R}}}id", rel_id)
            section.insert(insert_at, reference)
            insert_at += 1
    _get_or_add(settings, "evenAndOddHeaders")
    return outputs


def _font_inventory() -> str:
    if os.name == "nt":
        try:
            import winreg

            values: list[str] = []
            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts",
            ) as key:
                index = 0
                while True:
                    try:
                        name, value, _ = winreg.EnumValue(key, index)
                    except OSError:
                        break
                    values.extend((str(name), str(value)))
                    index += 1
            return "\n".join(values).lower()
        except OSError:
            return ""
    matcher = shutil.which("fc-list")
    if matcher:
        try:
            return subprocess.run(
                [matcher, ":", "family"], capture_output=True, text=True,
                encoding="utf-8", errors="ignore", timeout=10, check=False,
            ).stdout.lower()
        except (OSError, subprocess.TimeoutExpired):
            return ""
    font_dirs = [Path("/System/Library/Fonts"), Path("/Library/Fonts"), Path.home() / "Library/Fonts"]
    return "\n".join(path.name for root in font_dirs if root.is_dir() for path in root.rglob("*")).lower()


def _font_issues() -> list[FormatIssue]:
    inventory = _font_inventory()
    if not inventory:
        return [FormatIssue("FONT_CHECK_UNAVAILABLE", "error", "无法验证公文字体", "系统无法读取字体清单，不能判定成品符合标准。", "GB/T 9704-2012 5.2.2—5.2.5")]
    groups = {
        "方正小标宋简体": ("方正小标宋", "fzxiaobiaosong"),
        "仿宋_GB2312": ("仿宋", "fangsong"),
        "楷体_GB2312": ("楷体", "kaiti"),
        "黑体": ("黑体", "simhei", "heiti"),
    }
    missing = [name for name, aliases in groups.items() if not any(alias.lower() in inventory for alias in aliases)]
    if not missing:
        return []
    return [FormatIssue("REQUIRED_FONT_MISSING", "error", "缺少公文所需字体", "未检测到：" + "、".join(missing) + "。已写入标准字体名，但导出前必须安装并复核。", "GB/T 9704-2012 5.2.2—5.2.5")]


def diagnose_docx(path: Path, *, changed_count: int = 0) -> FormatReport:
    document, _ = _read_core_parts(path)
    paragraphs = document.xpath(".//w:body/w:p", namespaces=NS)
    tables = document.xpath(".//w:tbl", namespaces=NS)
    issues: list[FormatIssue] = []
    sections = document.xpath(".//w:sectPr", namespaces=NS)
    if not sections:
        issues.append(FormatIssue("SECTION_MISSING", "error", "缺少页面节设置", "无法校准 A4、版心和页码。", "GB/T 9704-2012 5.1"))
    else:
        for section in sections:
            size = section.find(_qn("pgSz"))
            margins = section.find(_qn("pgMar"))
            expected_size = size is not None and size.get(_qn("w")) == "11906" and size.get(_qn("h")) == "16838"
            expected_margins = margins is not None and all(
                margins.get(_qn(name)) == value
                for name, value in {
                    "top": "2098", "bottom": "1984", "left": "1587", "right": "1474",
                    "footer": PAGE_FOOTER_DISTANCE,
                }.items()
            )
            if not expected_size or not expected_margins:
                issues.append(FormatIssue("PAGE_GEOMETRY_INVALID", "error", "页面尺寸或版心不符合预设", "需要校准 A4、天头 37 mm、订口 28 mm 和 156×225 mm 版心。", "GB/T 9704-2012 5.1"))
                break
            grid = section.find(_qn("docGrid"))
            if (
                grid is None
                or grid.get(_qn("type")) != "linesAndChars"
                or grid.get(_qn("linePitch")) != GRID_LINE_PITCH
                or grid.get(_qn("charSpace")) != str(GRID_CHARACTER_SPACE)
            ):
                issues.append(FormatIssue(
                    "DOCUMENT_GRID_INVALID", "error" if changed_count else "warning",
                    "文档网格未锁定为每面 22 行、每行 28 字",
                    "需要同时校准 Normal 字号、28 磅行距与字符网格，不能只设置页边距。",
                    "GB/T 9704-2012 5.2.3",
                ))
                break
    nonempty = [item for item in paragraphs if _paragraph_text(item)]
    if not nonempty:
        issues.append(FormatIssue("DOCUMENT_EMPTY", "error", "正文为空", "未识别到可排版正文。", "输入完整性"))
    else:
        classified = _classify_document_paragraphs(nonempty)
        title = next((paragraph for paragraph, role in classified if role == "title"), None)
        if title is not None:
            title_runs = title.xpath(".//w:r/w:rPr", namespaces=NS)
            title_properties = title.find(_qn("pPr"))
            title_centered = (
                title_properties is not None
                and title_properties.find(_qn("jc")) is not None
                and title_properties.find(_qn("jc")).get(_qn("val")) == "center"
            )
            title_standard = bool(title_runs) and all(
                properties.find(_qn("rFonts")) is not None
                and properties.find(_qn("rFonts")).get(_qn("eastAsia")) == "方正小标宋简体"
                and properties.find(_qn("sz")) is not None
                and properties.find(_qn("sz")).get(_qn("val")) == "44"
                for properties in title_runs
            )
            if not title_centered or not title_standard:
                issues.append(FormatIssue("TITLE_STYLE_INVALID", "warning", "标题样式需要校准", "标题未完整使用 2 号小标宋和居中规则。", "GB/T 9704-2012 7.3.1"))
        body_invalid = False
        for paragraph, role in classified:
            if role != "body":
                continue
            properties = paragraph.find(_qn("pPr"))
            runs = paragraph.xpath(".//w:r/w:rPr", namespaces=NS)
            if properties is None or not runs:
                body_invalid = True
                break
            spacing = properties.find(_qn("spacing"))
            indent = properties.find(_qn("ind"))
            if (
                spacing is None
                or spacing.get(_qn("line")) != "560"
                or indent is None
                or indent.get(_qn("firstLine")) != "640"
                or any(
                    run.find(_qn("rFonts")) is None
                    or run.find(_qn("rFonts")).get(_qn("eastAsia")) != "仿宋_GB2312"
                    or run.find(_qn("sz")) is None
                    or run.find(_qn("sz")).get(_qn("val")) != "32"
                    for run in runs
                )
            ):
                body_invalid = True
                break
        if body_invalid:
            issues.append(FormatIssue("BODY_STYLE_INVALID", "warning", "正文段落需要校准", "正文未完整使用 3 号仿宋、28 磅行距和首行二字符缩进。", "GB/T 9704-2012 5.2.3、5.2.4"))
    with zipfile.ZipFile(path) as package:
        footer_parts = [name for name in package.namelist() if re.fullmatch(r"word/footer[^/]*\.xml", name)]
        has_page_field = any(b"PAGE" in package.read(name) for name in footer_parts)
    if not document.xpath(".//w:sectPr/w:footerReference", namespaces=NS) or not has_page_field:
        issues.append(FormatIssue("PAGE_NUMBER_MISSING", "error" if changed_count else "warning", "未识别到有效页码", "排版时将按奇偶页分别置于版心下边缘。", "GB/T 9704-2012 7.5"))
    if document.xpath(".//w:txbxContent", namespaces=NS):
        issues.append(FormatIssue("TEXTBOX_REVIEW_REQUIRED", "warning", "包含文本框或浮动文字", "工具不移动文本框，需人工确认其字体、位置和遮挡关系。", "特殊对象复核"))
    if any(re.match(r"^\d+[、)]", _paragraph_text(item)) for item in nonempty):
        issues.append(FormatIssue("NUMBERING_REVIEW_REQUIRED", "warning", "发现非标准数字序号", "请确认是否应使用“1.”或“（1）”，法规条号不会自动强改。", "GB/T 9704-2012 5.2.3"))
    special_marks = [
        _paragraph_text(item)
        for item in nonempty
        if _paragraph_role(_paragraph_text(item), first_body=False) == "issuing_authority"
    ]
    if special_marks:
        issues.append(FormatIssue(
            "SPECIAL_LAYOUT_VISUAL_REVIEW_REQUIRED", "warning", "识别到版头或特定公文版式",
            "发文机关标志、红色分隔线、印章、信函、命令（令）或纪要必须按最终渲染页逐页复核；工具不会伪造机关标志或印章。",
            "GB/T 9704-2012 7.2、7.3.5、10",
        ))
    if any(_paragraph_role(_paragraph_text(item), first_body=False) == "signatory" for item in nonempty):
        issues.append(FormatIssue(
            "SIGNATORY_VISUAL_REVIEW_REQUIRED", "warning", "签发人区域需要人工复核",
            "请确认签发人姓名使用三号楷体，并核对多签发人换行与对齐；系统不猜测签发权限。",
            "GB/T 9704-2012 7.2.6",
        ))
    issues.extend(_font_issues())
    compliant = not any(item.severity == "error" for item in issues)
    return FormatReport(compliant, len(paragraphs), len(tables), changed_count, tuple(issues))


def format_docx(source: Path, target: Path) -> FormatReport:
    """直接改写必要 OOXML 部件，所有未触碰部件逐项复制。"""

    with zipfile.ZipFile(source) as package:
        members = _validated_members(package)
        payloads: dict[str, bytes] = {}
        document = _safe_xml(package.read("word/document.xml"), "word/document.xml")
        settings = _safe_xml(package.read("word/settings.xml"), "word/settings.xml") if "word/settings.xml" in package.namelist() else etree.Element(_qn("settings"), nsmap={"w": W})
        styles = (
            _safe_xml(package.read("word/styles.xml"), "word/styles.xml")
            if "word/styles.xml" in package.namelist()
            else None
        )
        relationships = _safe_xml(package.read("word/_rels/document.xml.rels"), "word/_rels/document.xml.rels") if "word/_rels/document.xml.rels" in package.namelist() else etree.Element(f"{{{PR}}}Relationships", nsmap={None: PR})
        content_types = _safe_xml(package.read("[Content_Types].xml"), "[Content_Types].xml")

        changed = 0
        body_paragraphs = document.xpath(
            ".//w:body/w:p | .//w:body/w:sdt/w:sdtContent/w:p", namespaces=NS
        )
        for paragraph, role in _classify_document_paragraphs(body_paragraphs):
            changed += _format_paragraph(paragraph, role)
        changed += _format_tables(document)
        changed += _configure_sections(document)
        changed += _configure_normal_style(styles)
        _configure_east_asian_typography(settings)
        payloads.update(
            _add_page_footers(
                document,
                settings,
                relationships,
                content_types,
                package,
            )
        )
        payloads["word/document.xml"] = etree.tostring(document, xml_declaration=True, encoding="UTF-8", standalone=True)
        payloads["word/settings.xml"] = etree.tostring(settings, xml_declaration=True, encoding="UTF-8", standalone=True)
        if styles is not None:
            payloads["word/styles.xml"] = etree.tostring(
                styles, xml_declaration=True, encoding="UTF-8", standalone=True
            )
        payloads["word/_rels/document.xml.rels"] = etree.tostring(relationships, xml_declaration=True, encoding="UTF-8", standalone=True)
        payloads["[Content_Types].xml"] = etree.tostring(content_types, xml_declaration=True, encoding="UTF-8", standalone=True)

        target.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as output:
            written: set[str] = set()
            for info in members:
                data = payloads.get(info.filename)
                if data is None:
                    data = package.read(info.filename)
                output.writestr(info, data)
                written.add(info.filename)
            for name, data in payloads.items():
                if name not in written:
                    output.writestr(name, data)
    return diagnose_docx(target, changed_count=changed)


def _office_candidates() -> list[Path]:
    values = [shutil.which("soffice"), shutil.which("libreoffice")]
    if os.name == "nt":
        for root in (os.getenv("PROGRAMFILES"), os.getenv("PROGRAMFILES(X86)")):
            if root:
                values.append(str(Path(root) / "LibreOffice" / "program" / "soffice.exe"))
    return [Path(value) for value in values if value and Path(value).is_file()]


def _convert_with_libreoffice(source: Path, workspace: Path) -> Path | None:
    candidates = _office_candidates()
    if not candidates:
        return None
    profile = workspace / "office-profile"
    profile.mkdir(mode=0o700, exist_ok=True)
    environment = os.environ.copy()
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        environment[key] = ""
    environment["NO_PROXY"] = "*"
    command = [
        str(candidates[0]), "--headless", "--nologo", "--nodefault", "--nofirststartwizard",
        f"-env:UserInstallation={profile.as_uri()}", "--convert-to", "docx", "--outdir", str(workspace), str(source),
    ]
    try:
        completed = subprocess.run(command, capture_output=True, timeout=90, env=environment, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise OfficialFormatError("OFFICE_CONVERSION_FAILED", "本机格式转换失败", "LibreOffice 未能在 90 秒内完成本地转换。") from exc
    expected = workspace / f"{source.stem}.docx"
    if completed.returncode != 0 or not expected.is_file():
        raise OfficialFormatError("OFFICE_CONVERSION_FAILED", "本机格式转换失败", "办公套件没有生成可验证的 DOCX；原文件未改变。")
    return expected


def _convert_with_windows_office(source: Path, workspace: Path) -> Path | None:
    if os.name != "nt":
        return None
    try:
        import win32com.client  # type: ignore[import-untyped]
    except ImportError:
        return None
    target = workspace / f"{source.stem}.docx"
    for program_id in ("Word.Application", "Kwps.Application", "Wps.Application"):
        application = None
        document = None
        try:
            application = win32com.client.DispatchEx(program_id)
            application.Visible = False
            application.DisplayAlerts = 0
            document = application.Documents.Open(str(source), ReadOnly=True, AddToRecentFiles=False)
            try:
                document.SaveAs2(str(target), FileFormat=16)
            except AttributeError:
                document.SaveAs(str(target), FileFormat=16)
            if target.is_file():
                return target
        except Exception:  # noqa: BLE001 - 逐个尝试本机办公套件，最终统一给出脱敏错误。
            continue
        finally:
            if document is not None:
                try:
                    document.Close(False)
                except Exception:  # noqa: BLE001
                    pass
            if application is not None:
                try:
                    application.Quit()
                except Exception:  # noqa: BLE001
                    pass
    return None


def prepare_docx(source: Path, workspace: Path) -> tuple[Path, bool]:
    extension = source.suffix.lower()
    if extension == ".docx":
        return source, False
    converted = _convert_with_libreoffice(source, workspace)
    if converted is None:
        converted = _convert_with_windows_office(source, workspace)
    if converted is None:
        raise OfficialFormatError(
            "OFFICE_SUITE_REQUIRED", "缺少可用的本机办公套件",
            "DOC/WPS 需要本机已安装的 LibreOffice、Microsoft Office 或 WPS 完成本地转换；当前未检测到可用套件。",
        )
    return converted, True


def _private_write(path: Path, payload: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    descriptor = os.open(path, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
    except Exception:
        path.unlink(missing_ok=True)
        raise


def _install_loopback_only_network_guard() -> Any:
    """阻止本机助手进程主动连接非回环地址，并返回恢复函数。"""

    original_create_connection = socket.create_connection
    original_connect = socket.socket.connect

    def is_loopback(host: Any) -> bool:
        if str(host).lower() == "localhost":
            return True
        try:
            return ipaddress.ip_address(str(host).split("%", 1)[0]).is_loopback
        except ValueError:
            return False

    def guarded_create_connection(address: Any, *args: Any, **kwargs: Any) -> Any:
        if not isinstance(address, tuple) or not address or not is_loopback(address[0]):
            raise OSError("OFFICIAL_FORMAT_NETWORK_DENIED")
        return original_create_connection(address, *args, **kwargs)

    def guarded_connect(instance: socket.socket, address: Any) -> Any:
        if instance.family in {socket.AF_INET, socket.AF_INET6}:
            if not isinstance(address, tuple) or not address or not is_loopback(address[0]):
                raise OSError("OFFICIAL_FORMAT_NETWORK_DENIED")
        return original_connect(instance, address)

    socket.create_connection = guarded_create_connection
    socket.socket.connect = guarded_connect

    def restore() -> None:
        socket.create_connection = original_create_connection
        socket.socket.connect = original_connect

    return restore


def _append_stage_log(config_dir: Path, stage: str, started_at: float, code: str) -> None:
    """只记录阶段、耗时和错误码；不接收文档相关参数。"""

    path = config_dir / "official-format.log"
    try:
        if path.is_file() and path.stat().st_size > 512 * 1024:
            rotated = config_dir / "official-format.log.1"
            rotated.unlink(missing_ok=True)
            path.replace(rotated)
        line = json.dumps(
            {
                "version": VERSION,
                "stage": stage,
                "duration_ms": max(0, int((time.monotonic() - started_at) * 1000)),
                "result_code": code,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        with path.open("a", encoding="utf-8") as stream:
            stream.write(line + "\n")
        if os.name != "nt":
            path.chmod(0o600)
    except OSError:
        return


def _safe_stem(filename: str) -> str:
    stem = Path(filename.replace("\\", "/")).stem
    stem = re.sub(r"[\x00-\x1f<>:\"/\\|?*]", "_", stem).strip(" ._")
    return (stem or "公文")[:80]


def _extract_upload(handler: BaseHTTPRequestHandler) -> tuple[str, bytes]:
    try:
        length = int(handler.headers.get("Content-Length", "0"))
    except ValueError as exc:
        raise OfficialFormatError("UPLOAD_LENGTH_INVALID", "文件请求无效", "无法确认上传大小。") from exc
    if length <= 0 or length > MAX_FILE_BYTES + 1024 * 1024:
        raise OfficialFormatError("FILE_SIZE_LIMIT", "文件大小不符合要求", "单个文件不得超过 50 MiB。")
    content_type = handler.headers.get("Content-Type", "")
    match = re.search(r"boundary=(?:\"([^\"]+)\"|([^;]+))", content_type)
    if not content_type.lower().startswith("multipart/form-data") or not match:
        raise OfficialFormatError("UPLOAD_FORMAT_INVALID", "文件请求无效", "请选择 DOC、DOCX 或 WPS 文件。")
    boundary = (match.group(1) or match.group(2)).encode("ascii", "strict")
    body = handler.rfile.read(length)
    for part in body.split(b"--" + boundary):
        if b"Content-Disposition:" not in part or b'name="document"' not in part:
            continue
        header_bytes, separator, data = part.partition(b"\r\n\r\n")
        if not separator:
            continue
        filename_match = re.search(br'filename="([^"\r\n]*)"', header_bytes)
        filename = (filename_match.group(1) if filename_match else b"").decode("utf-8", "replace")
        payload = data[:-2] if data.endswith(b"\r\n") else data
        if not payload or len(payload) > MAX_FILE_BYTES:
            raise OfficialFormatError("FILE_SIZE_LIMIT", "文件大小不符合要求", "文件为空或超过 50 MiB。")
        extension = Path(filename).suffix.lower()
        if extension not in SUPPORTED_EXTENSIONS:
            raise OfficialFormatError("FORMAT_UNSUPPORTED", "文件格式不支持", "仅支持 DOC、DOCX 和 WPS；不接受宏文档或任意压缩包。")
        return filename, payload
    raise OfficialFormatError("UPLOAD_FILE_MISSING", "没有收到文件", "请重新选择文件。")


def _escape_issue(issue: FormatIssue) -> str:
    level = "严重" if issue.severity == "error" else "需复核"
    return (
        f'<li class="issue {html.escape(issue.severity)}"><span>{level}</span>'
        f'<div><strong>{html.escape(issue.title)}</strong><p>{html.escape(issue.detail)}</p>'
        f'<small>{html.escape(issue.clause)} · {html.escape(issue.code)}</small></div></li>'
    )


def _page(*, token: str, title: str, body: str) -> bytes:
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)} · PartyOps</title><style>
*{{box-sizing:border-box}}body{{margin:0;color:#322820;background:#f5efe4;font:14px/1.7 system-ui,"Microsoft YaHei",sans-serif}}main{{width:min(1080px,94vw);margin:4vh auto;background:#fffaf0;border:1px solid #d8c9b5;box-shadow:0 24px 70px #5f39201a}}header{{display:flex;justify-content:space-between;gap:24px;padding:28px 36px;border-bottom:3px solid #a52b23}}header p{{margin:4px 0 0;color:#7d6a5b}}h1,h2{{margin:0;color:#463329;font-family:"Noto Serif SC","Songti SC",serif}}.version{{color:#a52b23;font:700 12px Georgia,serif}}section{{padding:30px 36px}}.security{{margin-bottom:20px;padding:14px 16px;border-left:4px solid #a52b23;background:#f7e9e4;color:#74251f}}.security strong,.security span{{display:block}}.security span{{margin-top:4px;color:#775d52;font-size:12px}}.flow{{display:grid;grid-template-columns:repeat(4,1fr);margin:20px 0;border:1px solid #dacdbb;background:#dacdbb;gap:1px}}.flow div{{padding:14px;background:#faf4e9}}.flow b{{display:block;color:#a52b23;font:700 11px Georgia,serif}}.flow span{{font-size:12px}}input[type=file]{{width:100%;padding:18px;border:1px dashed #bca88e;background:#fffdf8}}button,.button{{display:inline-flex;align-items:center;justify-content:center;min-height:44px;margin-top:16px;padding:0 22px;border:0;color:#fff;background:#a52b23;text-decoration:none;font-weight:700;cursor:pointer}}.secondary{{margin-left:8px;color:#704c35;background:#eadfce}}.summary{{display:grid;grid-template-columns:repeat(4,1fr);margin:18px 0;border:1px solid #ded0bd}}.summary div{{padding:15px;border-right:1px solid #ded0bd}}.summary div:last-child{{border:0}}.summary span,.summary strong{{display:block}}.summary span{{color:#857262;font-size:11px}}.summary strong{{margin-top:5px;font:700 22px Georgia,serif}}ul.issues{{display:grid;gap:8px;padding:0;list-style:none}}.issue{{display:grid;grid-template-columns:64px 1fr;gap:12px;padding:14px;border:1px solid #ddcfbc;background:#fffdf8}}.issue>span{{color:#9a2c25;font-weight:700}}.issue strong,.issue p,.issue small{{display:block;margin:0}}.issue p,.issue small{{color:#7b6859}}.issue small{{margin-top:4px;font-size:11px}}.ok{{padding:16px;border-left:4px solid #4d7656;background:#edf3e9;color:#31593b}}footer{{padding:18px 36px;border-top:1px solid #ded0bd;color:#76675d;background:#f3eadc;font-size:12px}}@media(max-width:700px){{header{{display:block}}section,header{{padding:22px}}.flow,.summary{{grid-template-columns:1fr}}.summary div{{border-right:0;border-bottom:1px solid #ded0bd}}}}
</style></head><body><main><header><div><h1>{html.escape(title)}</h1><p>GB/T 9704-2012 单一预设 · 本机一次性处理</p></div><span class="version">PartyOps {VERSION}</span></header><section>{body}</section><footer>普通删除不等同于取证级擦除。工具在导出、取消、异常退出或空闲 15 分钟后清理本次临时副本。</footer></main></body></html>""".encode("utf-8")


def _start_body(token: str) -> str:
    return f"""<div class="security"><strong>不建议在涉密、敏感电脑上使用本功能，也不得使用 PartyOps 处理涉密文件。</strong><span>文件只发送到当前电脑的 127.0.0.1 临时助手，不进入主机、协同机、AI 服务或数据库。</span></div>
<div class="flow"><div><b>01</b><span>选择文件</span></div><div><b>02</b><span>查看诊断</span></div><div><b>03</b><span>一键排版</span></div><div><b>04</b><span>校验并导出</span></div></div>
<h2>选择待排版公文</h2><p>仅支持 DOCX；DOC/WPS 由本机 WPS、Office 或 LibreOffice 转换。单文件上限 50 MiB，永不覆盖原文件。</p>
<form method="post" action="/diagnose?t={html.escape(token)}" enctype="multipart/form-data"><input type="file" name="document" accept=".doc,.docx,.wps" required><button type="submit">开始本机诊断</button></form><form method="post" action="/cancel?t={html.escape(token)}"><button class="secondary" type="submit">退出并清理本次临时文件</button></form>"""


def _report_body(token: str, document_id: str, report: FormatReport, *, formatted: bool) -> str:
    issue_html = "".join(_escape_issue(item) for item in report.issues)
    if not issue_html:
        issue_html = '<div class="ok">未发现阻断性版式问题；仍应由公文责任人对内容和特殊版式进行最终复核。</div>'
    status = "可导出，仍需人工终审" if report.compliant else "存在阻断项，不得标记为符合标准"
    actions = (
        f'<a class="button" href="/download/{document_id}?t={html.escape(token)}">下载“公文规范版”DOCX</a>'
        if formatted
        else f'<form method="post" action="/format/{document_id}?t={html.escape(token)}"><button type="submit">按 GB/T 9704-2012 一键排版</button></form>'
    )
    return f"""<div class="security"><strong>{html.escape(status)}</strong><span>系统只校验版式，不判断公文内容、政治表述或审批程序。</span></div>
<div class="summary"><div><span>正文段落</span><strong>{report.paragraph_count}</strong></div><div><span>表格</span><strong>{report.table_count}</strong></div><div><span>本次调整</span><strong>{report.changed_count}</strong></div><div><span>问题</span><strong>{len(report.issues)}</strong></div></div>
<h2>{'排版后复核' if formatted else '排版前诊断'}</h2><ul class="issues">{issue_html}</ul>{actions}<a class="button secondary" href="/?t={html.escape(token)}">重新选择</a><form method="post" action="/cancel?t={html.escape(token)}"><button class="secondary" type="submit">退出并清理</button></form>"""


def run_official_format_tool(
    transaction_id: str,
    *,
    open_browser: bool,
    config_dir: Path,
) -> int:
    """运行单次回环工具；协议参数只允许 UUID，不接受路径或文件信息。"""

    try:
        token = str(uuid.UUID(transaction_id))
    except (ValueError, AttributeError) as exc:
        raise OfficialFormatError("FORMAT_TRANSACTION_INVALID", "排版事务无效", "请从 PartyOps 公文规范排版页面重新发起。") from exc

    workspace = Path(tempfile.mkdtemp(prefix="partyops-official-format-"))
    if os.name != "nt":
        workspace.chmod(0o700)
    documents: dict[str, LocalDocument] = {}
    last_activity = time.monotonic()

    def clear_documents() -> None:
        for item in documents.values():
            for path in (item.source, item.output):
                if path is not None and path.is_file() and workspace in path.parents:
                    path.unlink(missing_ok=True)
        documents.clear()

    class Handler(BaseHTTPRequestHandler):
        server_version = "PartyOpsLocalFormatter/1"

        def _authorized(self) -> bool:
            nonlocal last_activity
            host = self.headers.get("Host", "")
            expected = f"127.0.0.1:{self.server.server_address[1]}"
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            authorized = host == expected and query.get("t") == [token]
            if authorized:
                last_activity = time.monotonic()
            return authorized

        def _send(self, status: int, body: bytes, content_type: str = "text/html; charset=utf-8") -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store, max-age=0")
            self.send_header("Pragma", "no-cache")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(body)

        def _failure(self, error: OfficialFormatError) -> None:
            detail = f'<div class="security"><strong>{html.escape(error.title)}</strong><span>{html.escape(error.detail)} · {html.escape(error.code)}</span></div><a class="button secondary" href="/?t={html.escape(token)}">返回重新选择</a>'
            self._send(422, _page(token=token, title="公文排版未完成", body=detail))

        def do_GET(self) -> None:  # noqa: N802
            if not self._authorized():
                self._send(403, b"forbidden", "text/plain; charset=utf-8")
                return
            path = urllib.parse.urlsplit(self.path).path
            if path == "/":
                self._send(200, _page(token=token, title="公文规范排版", body=_start_body(token)))
                return
            match = re.fullmatch(r"/download/([0-9a-f]{32})", path)
            if not match or match.group(1) not in documents:
                self._send(404, b"not found", "text/plain; charset=utf-8")
                return
            item = documents[match.group(1)]
            if item.output is None or not item.output.is_file():
                self._send(410, b"gone", "text/plain; charset=utf-8")
                return
            payload = item.output.read_bytes()
            filename = urllib.parse.quote(f"{item.original_stem}-公文规范版.docx")
            self.send_response(200)
            self.send_header("Content-Type", "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
            self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{filename}")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(payload)
            _append_stage_log(config_dir, "download", last_activity, "OK")
            threading.Thread(target=self.server.shutdown, daemon=True).start()

        def do_POST(self) -> None:  # noqa: N802
            if not self._authorized():
                self._send(403, b"forbidden", "text/plain; charset=utf-8")
                return
            path = urllib.parse.urlsplit(self.path).path
            try:
                if path == "/cancel":
                    clear_documents()
                    self._send(200, _page(token=token, title="临时文件已清理", body='<div class="ok">本次临时副本已删除，可以关闭此页面。</div>'))
                    _append_stage_log(config_dir, "cancel", last_activity, "OK")
                    threading.Thread(target=self.server.shutdown, daemon=True).start()
                    return
                if path == "/diagnose":
                    started = time.monotonic()
                    filename, payload = _extract_upload(self)
                    document_id = uuid.uuid4().hex
                    source = workspace / f"{document_id}{Path(filename).suffix.lower()}"
                    _private_write(source, payload)
                    prepared, converted = prepare_docx(source, workspace)
                    report = diagnose_docx(prepared)
                    clear_documents()
                    documents[document_id] = LocalDocument(prepared, _safe_stem(filename), converted, report=report)
                    _append_stage_log(config_dir, "diagnose", started, "OK")
                    self._send(200, _page(token=token, title="排版前诊断", body=_report_body(token, document_id, report, formatted=False)))
                    return
                match = re.fullmatch(r"/format/([0-9a-f]{32})", path)
                if match and match.group(1) in documents:
                    started = time.monotonic()
                    item = documents[match.group(1)]
                    output = workspace / f"{match.group(1)}-formatted.docx"
                    report = format_docx(item.source, output)
                    item.output = output
                    item.report = report
                    _append_stage_log(config_dir, "format", started, "OK")
                    self._send(200, _page(token=token, title="排版后复核", body=_report_body(token, match.group(1), report, formatted=True)))
                    return
                self._send(404, b"not found", "text/plain; charset=utf-8")
            except OfficialFormatError as exc:
                _append_stage_log(config_dir, "process", last_activity, exc.code)
                self._failure(exc)
            except (OSError, ValueError, etree.Error) as exc:
                _append_stage_log(config_dir, "process", last_activity, "FORMAT_PROCESS_FAILED")
                self._failure(OfficialFormatError("FORMAT_PROCESS_FAILED", "本机排版未完成", f"文档结构或本机办公套件返回异常：{type(exc).__name__}。原文件未改变。"))

        def log_message(self, _format: str, *_args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    restore_network = _install_loopback_only_network_guard()
    url = f"http://127.0.0.1:{server.server_address[1]}/?t={token}"
    marker = config_dir / "official-format.url"
    config_dir.mkdir(parents=True, exist_ok=True)
    marker.write_text(url + "\n", encoding="utf-8")
    if os.name != "nt":
        marker.chmod(0o600)

    def idle_watch() -> None:
        while time.monotonic() - last_activity < IDLE_TIMEOUT_SECONDS:
            time.sleep(5)
        server.shutdown()

    watcher = threading.Thread(target=idle_watch, daemon=True)
    watcher.start()
    try:
        _append_stage_log(config_dir, "start", time.monotonic(), "OK")
        if open_browser and not webbrowser.open(url, new=1):
            raise OfficialFormatError("BROWSER_OPEN_FAILED", "无法打开本机排版页面", "请检查系统默认浏览器关联后重试。")
        server.serve_forever(poll_interval=0.5)
        return 0
    finally:
        server.server_close()
        restore_network()
        try:
            if marker.read_text(encoding="utf-8").strip() == url:
                marker.unlink(missing_ok=True)
        except OSError:
            pass
        _append_stage_log(config_dir, "cleanup", last_activity, "OK")
        shutil.rmtree(workspace, ignore_errors=True)


__all__ = [
    "FormatIssue", "FormatReport", "OfficialFormatError", "diagnose_docx",
    "format_docx", "normalize_chinese_punctuation", "prepare_docx",
    "run_official_format_tool",
]

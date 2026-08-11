"""开源发布前的安全边界回归测试。"""

from __future__ import annotations

import zipfile

from app.intake import extract_path_content


def test_presentation_xml_entities_are_rejected(tmp_path) -> None:
    """回归：不可信 Office XML 不能触发实体扩展。"""

    presentation = tmp_path / "unsafe.pptx"
    with zipfile.ZipFile(presentation, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "ppt/slides/slide1.xml",
            """<?xml version="1.0"?>
<!DOCTYPE slide [<!ENTITY payload "expanded">]>
<p:sld xmlns:p="urn:p" xmlns:a="urn:a"><a:t>&payload;</a:t></p:sld>
""",
        )

    result = extract_path_content(presentation)

    assert result.content_status == "error"
    assert result.error_code == "CONTENT_PARSE_FAILED"
    assert "expanded" not in result.text

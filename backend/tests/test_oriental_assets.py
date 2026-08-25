from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ART_ROOT = PROJECT_ROOT / "frontend" / "src" / "assets" / "oriental"
SEASONS = ("spring", "summer", "autumn", "winter")
VARIANTS = (
    "header-landscape",
    "lower-scroll",
    "section-divider",
    "empty-center",
    "corner-accent",
)


def _alpha_centroid_ratio(path: Path) -> tuple[float, int]:
    """返回透明画面的横向视觉重心及最大透明度，用于防止素材偏在角落。"""
    with Image.open(path) as image:
        alpha = image.convert("RGBA").getchannel("A")
        width = min(alpha.width, 400)
        height = min(alpha.height, 100)
        sample = alpha.resize((width, height))
        values = sample.tobytes()

    column_weights = [sum(values[x::width]) for x in range(width)]
    total = sum(column_weights)
    assert total > 0, f"东方画卷不能是全透明图片：{path.name}"
    centroid = sum(index * weight for index, weight in enumerate(column_weights)) / total
    return centroid / max(width - 1, 1), max(values)


def test_oriental_art_pack_is_complete_and_lightweight() -> None:
    generated: list[Path] = []
    for season in SEASONS:
        season_files = [ART_ROOT / f"{season}-{variant}.webp" for variant in VARIANTS]
        assert all(path.is_file() for path in season_files)
        assert sum(path.stat().st_size for path in season_files) < 4_000_000
        generated.extend(season_files)

    assert len(generated) == 20
    manifest = json.loads((ART_ROOT / "asset-manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "1.3.3"


def test_header_art_is_centered_visible_and_complete() -> None:
    for season in SEASONS:
        path = ART_ROOT / f"{season}-header-landscape.webp"
        with Image.open(path) as image:
            assert image.width >= 1800
            assert image.height >= 360
            assert image.mode in {"RGBA", "LA"}

        centroid, maximum_alpha = _alpha_centroid_ratio(path)
        assert 0.35 <= centroid <= 0.70, (season, centroid)
        assert maximum_alpha >= 150, (season, maximum_alpha)


def test_runtime_scroll_assets_keep_expected_aspect_ratio() -> None:
    """标准画布比例固定，前端使用 contain，避免山水被横向或纵向压缩。"""
    for season in SEASONS:
        with Image.open(ART_ROOT / f"{season}-header-landscape.webp") as header:
            assert header.size == (1800, 360)
            assert header.width / header.height == 5
        with Image.open(ART_ROOT / f"{season}-lower-scroll.webp") as lower:
            assert lower.size == (2200, 360)
            assert round(lower.width / lower.height, 4) == round(2200 / 360, 4)


def test_scene_accents_have_real_transparency() -> None:
    for scene in ("archive", "relay", "scholar", "water"):
        path = ART_ROOT / f"scene-{scene}-accent.webp"
        assert path.is_file()
        with Image.open(path) as image:
            alpha = image.convert("RGBA").getchannel("A")
            assert alpha.getextrema()[0] == 0
            assert alpha.getextrema()[1] >= 128

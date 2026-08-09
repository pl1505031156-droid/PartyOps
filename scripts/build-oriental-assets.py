"""校验并派生 PartyOps 1.3.3 东方四时长卷运行素材。

核心页头与底部长卷由 ImageGen 生成并经统一去色、柔边和透明通道处理；
本脚本只执行可重复的尺寸派生、透明边缘检查和资源预算校验，不重绘美术。
"""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageChops, ImageFilter


ROOT = Path(__file__).resolve().parents[1]
ASSET_DIR = ROOT / "frontend" / "src" / "assets" / "oriental"
SEASONS = ("spring", "summer", "autumn", "winter")
SCENE_ASSETS = {
    "archive": "scene-archive-accent.webp",
    "relay-legacy": "scene-relay-accent.webp",
    "scholar": "scene-scholar-accent.webp",
    "water": "scene-water-accent.webp",
    "tasks": "scene-tasks-art.webp",
    "inbox": "scene-inbox-art.webp",
    "reports": "scene-reports-art.webp",
    "journal": "scene-journal-art.webp",
    "topic": "scene-topic-art.webp",
    "knowledge": "scene-knowledge-art.webp",
    "collaboration": "scene-collaboration-art.webp",
    "transfer": "scene-transfer-art.webp",
}
SEASONAL_SCENES = (
    "tasks",
    "inbox",
    "reports",
    "journal",
    "topic",
    "knowledge",
    "collaboration",
    "transfer",
)
ACTIVE_SEASON_BUDGET = 4 * 1024 * 1024
HEADER_SIZE = (1800, 360)
LOWER_SCROLL_SIZE = (2200, 360)


def _soft_horizontal_edges(image: Image.Image, width: int = 120) -> Image.Image:
    rgba = image.convert("RGBA")
    alpha = rgba.getchannel("A")
    mask_row = Image.new("L", (rgba.width, 1), 255)
    pixels = mask_row.load()
    for x in range(rgba.width):
        edge = min(x, rgba.width - 1 - x)
        pixels[x, 0] = 255 if edge >= width else round(255 * edge / max(1, width))
    mask = mask_row.resize(rgba.size).filter(ImageFilter.GaussianBlur(3))
    rgba.putalpha(ImageChops.multiply(alpha, mask))
    return rgba


def _save_webp(image: Image.Image, destination: Path) -> None:
    image.save(destination, "WEBP", quality=91, method=6)


def _fit_without_distortion(
    image: Image.Image,
    size: tuple[int, int],
    *,
    align_bottom: bool = True,
) -> Image.Image:
    """等比缩放后置于透明画布，任何情况下都不拉伸原始山水。"""
    source = image.convert("RGBA")
    source.thumbnail(size, Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", size, (0, 0, 0, 0))
    left = (size[0] - source.width) // 2
    top = size[1] - source.height if align_bottom else (size[1] - source.height) // 2
    canvas.alpha_composite(source, (left, top))
    return canvas


def _normalize_primary_assets(season: str) -> None:
    """把生成图放入标准横卷画布；仅等比缩放，禁止横向或纵向压缩。"""
    header_path = ASSET_DIR / f"{season}-header-landscape.webp"
    lower_path = ASSET_DIR / f"{season}-lower-scroll.webp"
    header = _fit_without_distortion(Image.open(header_path), HEADER_SIZE)
    lower = _fit_without_distortion(Image.open(lower_path), LOWER_SCROLL_SIZE)
    header = _soft_horizontal_edges(header, width=150)
    lower = _soft_horizontal_edges(lower, width=170)
    _save_webp(header, header_path)
    _save_webp(lower, lower_path)


def _derive_supporting_assets(season: str) -> None:
    header = Image.open(ASSET_DIR / f"{season}-header-landscape.webp").convert("RGBA")
    lower = Image.open(ASSET_DIR / f"{season}-lower-scroll.webp").convert("RGBA")

    # 章节分隔保留页头远山的低部轮廓，左右柔隐，避免形成矩形贴图边界。
    top = round(header.height * 0.43)
    divider = header.crop((0, top, header.width, header.height))
    divider.thumbnail((1440, 220), Image.Resampling.LANCZOS)
    divider = _soft_horizontal_edges(divider, width=max(60, divider.width // 14))
    _save_webp(divider, ASSET_DIR / f"{season}-section-divider.webp")

    # 右下植物来自同季底部长卷，保持画风一致，不创建第二套冲突构图。
    left = round(lower.width * 0.57)
    corner = lower.crop((left, 0, lower.width, lower.height))
    corner.thumbnail((720, 640), Image.Resampling.LANCZOS)
    _save_webp(corner, ASSET_DIR / f"{season}-corner-accent.webp")


def _validate_alpha_and_spill(path: Path) -> dict[str, object]:
    image = Image.open(path).convert("RGBA")
    alpha = image.getchannel("A")
    extrema = alpha.getextrema()
    if extrema[0] != 0 or extrema[1] < 128:
        raise SystemExit(f"{path.name} 透明通道不完整：{extrema}")

    visible = [pixel for pixel in image.get_flattened_data() if pixel[3] > 20]
    spill = sum(
        1
        for red, green, blue, _ in visible
        if green > 150 and green > red + 70 and green > blue + 70
    )
    spill_rate = spill / max(1, len(visible))
    if spill_rate > 0.001:
        raise SystemExit(f"{path.name} 存在明显绿色溢色：{spill_rate:.3%}")
    return {
        "file": path.name,
        "width": image.width,
        "height": image.height,
        "bytes": path.stat().st_size,
        "green_spill_rate": round(spill_rate, 6),
    }


def main() -> None:
    for season in SEASONS:
        _normalize_primary_assets(season)
        _derive_supporting_assets(season)

    manifest: dict[str, object] = {
        "version": "1.3.3",
        "seasons": {},
        "scenes": [],
        "scene_variants": {},
    }
    for season in SEASONS:
        paths = [
            ASSET_DIR / f"{season}-header-landscape.webp",
            ASSET_DIR / f"{season}-lower-scroll.webp",
            ASSET_DIR / f"{season}-corner-accent.webp",
            ASSET_DIR / f"{season}-empty-center.webp",
            ASSET_DIR / f"{season}-section-divider.webp",
        ]
        records = [_validate_alpha_and_spill(path) for path in paths]
        scene_records = []
        for scene in SEASONAL_SCENES:
            variant_path = ASSET_DIR / f"scene-{scene}-{season}.webp"
            record = _validate_alpha_and_spill(variant_path)
            record["scene"] = scene
            scene_records.append(record)
        total = sum(int(record["bytes"]) for record in records)
        total += sum(int(record["bytes"]) for record in scene_records)
        if total > ACTIVE_SEASON_BUDGET:
            raise SystemExit(
                f"{season} 活跃素材 {total / 1024 / 1024:.2f}MB，超过 4MB"
            )
        manifest["seasons"][season] = {
            "bytes": total,
            "assets": records,
            "scene_variants": scene_records,
        }
        manifest["scene_variants"][season] = [
            record["file"] for record in scene_records
        ]

    for name, filename in SCENE_ASSETS.items():
        path = ASSET_DIR / filename
        record = _validate_alpha_and_spill(path)
        record["scene"] = name
        manifest["scenes"].append(record)

    manifest_path = ASSET_DIR / "asset-manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"四季长卷校验通过，资源清单：{manifest_path}")


if __name__ == "__main__":
    main()

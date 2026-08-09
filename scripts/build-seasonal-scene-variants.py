"""为页面专属画卷生成四季色候版本。

输入图片必须已经带透明通道。本脚本只调整非透明像素的色候，不改变像素尺寸、
透明边缘或构图，因此不会造成横向/纵向拉伸，也不会引入浏览器端滤镜开销。
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
ASSET_DIR = ROOT / "frontend" / "src" / "assets" / "oriental"

SCENES = (
    "tasks",
    "inbox",
    "reports",
    "journal",
    "topic",
    "knowledge",
    "collaboration",
    "transfer",
)

# 仅改变色候，不用 CSS 滤镜临时模拟。权重保持克制，确保水墨层次和朱红点景不丢失。
PALETTES: dict[str, tuple[tuple[int, int, int], float]] = {
    "spring": ((154, 164, 126), 0.18),
    "summer": ((118, 151, 139), 0.24),
    "autumn": ((178, 132, 88), 0.17),
    "winter": ((132, 145, 151), 0.26),
}


def apply_palette(image: Image.Image, tint: tuple[int, int, int], strength: float) -> Image.Image:
    rgba = image.convert("RGBA")
    result: list[tuple[int, int, int, int]] = []
    for red, green, blue, alpha in rgba.get_flattened_data():
        if alpha == 0:
            result.append((red, green, blue, alpha))
            continue

        luminance = round(0.299 * red + 0.587 * green + 0.114 * blue)
        toned = tuple(round(luminance * 0.58 + channel * 0.42) for channel in tint)
        result.append(
            (
                round(red * (1 - strength) + toned[0] * strength),
                round(green * (1 - strength) + toned[1] * strength),
                round(blue * (1 - strength) + toned[2] * strength),
                alpha,
            )
        )
    rgba.putdata(result)
    return rgba


def main() -> None:
    generated = 0
    for scene in SCENES:
        source = ASSET_DIR / f"scene-{scene}-art.webp"
        if not source.is_file():
            raise FileNotFoundError(f"缺少页面场景源文件：{source}")
        image = Image.open(source)
        for season, (tint, strength) in PALETTES.items():
            destination = ASSET_DIR / f"scene-{scene}-{season}.webp"
            variant = apply_palette(image, tint, strength)
            variant.save(destination, "WEBP", quality=89, method=6)
            generated += 1
            print(f"已生成：{destination.name}")

    print(f"四季页面画卷生成完成：{generated} 个文件")


if __name__ == "__main__":
    main()

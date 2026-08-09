"""把 ImageGen 场景原图转换为 PartyOps 可安全叠加的透明 WebP。

脚本只做背景去除、透明边缘柔化和等比缩放，不裁切、不拉伸画面。
输入既可以是统一绿幕，也可以是统一暖白背景；背景色从左上角自动取样。
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path

from PIL import Image


def _distance(first: tuple[int, int, int], second: tuple[int, int, int]) -> float:
    return math.sqrt(sum((a - b) ** 2 for a, b in zip(first, second, strict=True)))


def remove_background(image: Image.Image) -> Image.Image:
    rgba = image.convert("RGBA")
    corner = rgba.getpixel((0, 0))[:3]
    is_green_screen = corner[1] > 180 and corner[1] > corner[0] + 90 and corner[1] > corner[2] + 90
    inner, outer = (34.0, 116.0) if is_green_screen else (16.0, 76.0)
    pixels = []
    for red, green, blue, source_alpha in rgba.get_flattened_data():
        distance = _distance((red, green, blue), corner)
        if distance <= inner:
            alpha = 0
        elif distance >= outer:
            alpha = source_alpha
        else:
            alpha = round(source_alpha * (distance - inner) / (outer - inner))

        # 绿幕边缘去色：用相邻通道上限收敛残留绿色，不改动水墨主体亮度。
        if is_green_screen and green > red + 26 and green > blue + 26:
            # 生成图的抗锯齿边缘会残留高饱和绿。此类像素不属于暖灰水墨色域，
            # 直接透明化比调色更可靠，也可避免浏览器缩放后出现荧光描边。
            alpha = 0
        elif is_green_screen and alpha > 0 and green > red + 12 and green > blue + 12:
            green = min(green, max(red, blue) + 8)
        pixels.append((red, green, blue, alpha))

    rgba.putdata(pixels)
    # 距离阈值已经产生柔边；再次模糊透明通道会把已清除的绿幕颜色带回可见区。
    # 因此保留当前逐像素透明度，避免浏览器缩放时出现绿色光边。
    return rgba


def fit_without_distortion(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    source = image.copy()
    source.thumbnail(size, Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", size, (0, 0, 0, 0))
    left = (size[0] - source.width) // 2
    top = size[1] - source.height
    canvas.alpha_composite(source, (left, top))
    return canvas


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--width", type=int, default=1800)
    parser.add_argument("--height", type=int, default=1010)
    args = parser.parse_args()

    converted = remove_background(Image.open(args.source))
    converted = fit_without_distortion(converted, (args.width, args.height))
    args.destination.parent.mkdir(parents=True, exist_ok=True)
    converted.save(args.destination, "WEBP", quality=91, method=6)
    print(f"已生成：{args.destination} ({converted.width}×{converted.height})")


if __name__ == "__main__":
    main()

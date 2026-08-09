"""生成 PartyOps 1.3.4 与 1.4.0 同状态 Chrome 视觉对照图。

输入截图均来自 1280×720 CSS 视口；Chrome 扩展会受 Windows 显示缩放影响
输出较大的物理像素图，因此先按原比例裁切并归一化，再生成左右对照画布。
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps


ROOT = Path(__file__).resolve().parents[1]
OLD_DIR = ROOT / "artifacts" / "design-qa" / "actual"
NEW_DIR = ROOT / "output" / "chrome-qa-1.4.0"
TARGET = (1280, 720)
LABEL_HEIGHT = 44
GAP = 24
PAPER = (247, 241, 231)
INK = (41, 37, 32)
LINE = (205, 191, 173)

PAIRS = {
    "archives": (
        OLD_DIR / "archives-1280x720.png",
        NEW_DIR / "archives-admin-new-raw.png",
    ),
    "collaboration": (
        OLD_DIR / "collaboration-1280x720.png",
        NEW_DIR / "fleet-admin-new-raw.png",
    ),
}


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in (Path(r"C:\Windows\Fonts\msyh.ttc"), Path(r"C:\Windows\Fonts\simhei.ttf")):
        if path.exists():
            return ImageFont.truetype(str(path), size=size)
    return ImageFont.load_default()


def _normalize(path: Path) -> Image.Image:
    with Image.open(path) as source:
        return ImageOps.fit(
            source.convert("RGB"),
            TARGET,
            method=Image.Resampling.LANCZOS,
            centering=(0.5, 0.5),
        )


def main() -> None:
    NEW_DIR.mkdir(parents=True, exist_ok=True)
    font = _font(22)
    for slug, (old_path, new_path) in PAIRS.items():
        if not old_path.exists() or not new_path.exists():
            raise SystemExit(f"缺少视觉对照输入：{slug}")

        old_image = _normalize(old_path)
        new_image = _normalize(new_path)
        old_image.save(NEW_DIR / f"{slug}-1.3.4-normalized.png", optimize=True)
        new_image.save(NEW_DIR / f"{slug}-1.4.0-normalized.png", optimize=True)

        canvas = Image.new(
            "RGB",
            (TARGET[0] * 2 + GAP, TARGET[1] + LABEL_HEIGHT),
            PAPER,
        )
        draw = ImageDraw.Draw(canvas)
        canvas.paste(old_image, (0, LABEL_HEIGHT))
        canvas.paste(new_image, (TARGET[0] + GAP, LABEL_HEIGHT))
        draw.text((14, 8), f"{slug} · 1.3.4 基线", fill=INK, font=font)
        draw.text((TARGET[0] + GAP + 14, 8), f"{slug} · 1.4.0 当前实现", fill=INK, font=font)
        draw.rectangle((0, LABEL_HEIGHT, TARGET[0] - 1, canvas.height - 1), outline=LINE)
        draw.rectangle((TARGET[0] + GAP, LABEL_HEIGHT, canvas.width - 1, canvas.height - 1), outline=LINE)
        canvas.save(NEW_DIR / f"{slug}-1.3.4-vs-1.4.0.png", optimize=True)

    print(f"已生成 {len(PAIRS)} 组视觉对照图：{NEW_DIR}")


if __name__ == "__main__":
    main()

"""生成 13 个东方主题场景的参考图/实际图并排验收画布。

该脚本只生成 QA 派生图，不修改任何产品美术资产。参考图与实际截图均
按原始宽高比缩放到相同画布，避免验收过程本身引入拉伸误判。
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps


ROOT = Path(__file__).resolve().parents[1]
ACTUAL_DIR = ROOT / "artifacts" / "design-qa" / "actual"
COMPARE_DIR = ROOT / "artifacts" / "design-qa" / "compare"
SHEET_DIR = ROOT / "artifacts" / "design-qa" / "sheets"

REFERENCES = {
    "tasks": Path(r"C:\Users\Administrator\AppData\Local\Temp\codex-clipboard-524d7f6d-d057-4d2e-9a03-e6d2c2e8a7b6.png"),
    "calendar": Path(r"C:\Users\Administrator\AppData\Local\Temp\codex-clipboard-41345061-677a-41fe-b931-b5c072bad658.png"),
    "reports": Path(r"C:\Users\Administrator\AppData\Local\Temp\codex-clipboard-b48f98e4-42ee-4895-9cce-2bbe486cf937.png"),
    "journal": Path(r"C:\Users\Administrator\AppData\Local\Temp\codex-clipboard-7ade7eed-1b60-41e3-bcc4-33856fedba47.png"),
    "inbox": Path(r"C:\Users\Administrator\AppData\Local\Temp\codex-clipboard-5f879bd2-b5d8-4e01-8167-04cf281d0df5.png"),
    "topic": Path(r"C:\Users\Administrator\AppData\Local\Temp\codex-clipboard-efd53f65-3bd1-479f-9ad3-5bc1fb46a197.png"),
    "workspace": Path(r"C:\Users\Administrator\AppData\Local\Temp\codex-clipboard-54bb019d-d3e1-49ba-bc5c-725eacb52b65.png"),
    "archives": Path(r"C:\Users\Administrator\AppData\Local\Temp\codex-clipboard-434fdbf2-8997-4c75-8dee-f09b09b584a2.png"),
    "inspection": Path(r"C:\Users\Administrator\AppData\Local\Temp\codex-clipboard-76e9640f-78c1-44b9-af1e-d585d5c5a719.png"),
    "knowledge": Path(r"C:\Users\Administrator\AppData\Local\Temp\codex-clipboard-8ce64c78-9d20-4101-86b4-f5a08646d330.png"),
    "comparison": Path(r"C:\Users\Administrator\AppData\Local\Temp\codex-clipboard-b36fbe5d-6565-41a3-8a48-52cf8ebf2454.png"),
    "collaboration": Path(r"C:\Users\Administrator\AppData\Local\Temp\codex-clipboard-1ecb0796-8b1b-499e-b5a6-7f5773d5d519.png"),
    "transfer": Path(r"C:\Users\Administrator\AppData\Local\Temp\codex-clipboard-a976df79-5242-4b65-8899-2c36efcff29b.png"),
}

CELL = (940, 530)
LABEL_HEIGHT = 34
PAPER = (247, 241, 231)
INK = (41, 37, 32)
LINE = (216, 204, 188)


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = (
        Path(r"C:\Windows\Fonts\msyh.ttc"),
        Path(r"C:\Windows\Fonts\simhei.ttf"),
    )
    for path in candidates:
        if path.exists():
            return ImageFont.truetype(str(path), size=size)
    return ImageFont.load_default()


def _fit(path: Path) -> Image.Image:
    image = Image.open(path).convert("RGB")
    fitted = ImageOps.contain(image, CELL, Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", CELL, PAPER)
    canvas.paste(fitted, ((CELL[0] - fitted.width) // 2, (CELL[1] - fitted.height) // 2))
    return canvas


def _pair(slug: str, reference: Path, actual: Path) -> Path:
    pair = Image.new("RGB", (CELL[0] * 2 + 30, CELL[1] + LABEL_HEIGHT), PAPER)
    draw = ImageDraw.Draw(pair)
    pair.paste(_fit(reference), (0, LABEL_HEIGHT))
    pair.paste(_fit(actual), (CELL[0] + 30, LABEL_HEIGHT))
    draw.rectangle((0, LABEL_HEIGHT, CELL[0] - 1, pair.height - 1), outline=LINE)
    draw.rectangle((CELL[0] + 30, LABEL_HEIGHT, pair.width - 1, pair.height - 1), outline=LINE)
    font = _font(18)
    draw.text((12, 6), f"{slug} · 参考效果", fill=INK, font=font)
    draw.text((CELL[0] + 42, 6), f"{slug} · 当前实现", fill=INK, font=font)
    destination = COMPARE_DIR / f"{slug}.png"
    pair.save(destination, optimize=True)
    return destination


def main() -> None:
    COMPARE_DIR.mkdir(parents=True, exist_ok=True)
    SHEET_DIR.mkdir(parents=True, exist_ok=True)
    pairs: list[Path] = []
    for slug, reference in REFERENCES.items():
        actual = ACTUAL_DIR / f"{slug}-1280x720.png"
        if not reference.exists() or not actual.exists():
            raise SystemExit(f"缺少逐页对照输入：{slug}")
        pairs.append(_pair(slug, reference, actual))

    for index in range(0, len(pairs), 4):
        group = pairs[index:index + 4]
        with Image.open(group[0]) as sample:
            width, height = sample.size
        sheet = Image.new("RGB", (width, height * len(group)), PAPER)
        for row, path in enumerate(group):
            with Image.open(path) as image:
                sheet.paste(image, (0, row * height))
        sheet.save(SHEET_DIR / f"comparison-{index // 4 + 1}.png", optimize=True)

    print(f"已生成 {len(pairs)} 个逐页对照图和 4 张汇总画布：{SHEET_DIR}")


if __name__ == "__main__":
    main()

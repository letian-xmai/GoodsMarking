from __future__ import annotations

from pathlib import Path


def write_mismatch_preview(path: Path, mismatches: list[dict]) -> bool:
    try:
        from PIL import Image, ImageDraw, ImageFont
    except Exception:
        return False
    width, height, label_height, cols = 220, 220, 78, 4
    rows = max(1, (len(mismatches) + cols - 1) // cols)
    canvas = Image.new("RGB", (cols * width, rows * (height + label_height)), "white")
    draw, font = ImageDraw.Draw(canvas), ImageFont.load_default()
    for index, row in enumerate(sorted(mismatches, key=lambda item: (item["outward_code"], item["sample_id"]))):
        x, y = (index % cols) * width, (index // cols) * (height + label_height)
        _paste_thumb(canvas, draw, row["image_path"], x, y, width, height, font)
        draw.rectangle((x, y + height, x + width, y + height + label_height), fill=(245, 245, 245))
        draw.text((x + 6, y + height + 5), f"{row['outward_code']}\n{row['sample_id']}\n人工:{row['label']} 模型:{row['prediction']}", fill="black", font=font)
    canvas.save(path, quality=92)
    return True


def _paste_thumb(canvas, draw, image_path: str, x: int, y: int, width: int, height: int, font) -> None:
    from PIL import Image
    try:
        with Image.open(image_path) as image:
            image.thumbnail((width, height - 8))
            canvas.paste(image.convert("RGB"), (x + (width - image.width) // 2, y + 4 + (height - 8 - image.height) // 2))
    except Exception as exc:
        draw.text((x + 8, y + 40), f"open failed: {type(exc).__name__}", fill="red", font=font)

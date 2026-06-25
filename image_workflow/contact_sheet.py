from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw


def write_contact_sheet(path: str | Path, image_dir: str | Path, *, limit: int = 40) -> None:
    images = sorted(Path(image_dir).glob("*.jpg"))[:limit]
    if not images:
        return
    thumb, cols = 120, 5
    rows = (len(images) + cols - 1) // cols
    canvas = Image.new("RGB", (cols * thumb, rows * thumb), "white")
    draw = ImageDraw.Draw(canvas)
    for idx, source in enumerate(images):
        with Image.open(source) as image:
            image.thumbnail((thumb - 12, thumb - 24))
            x, y = (idx % cols) * thumb + 6, (idx // cols) * thumb + 6
            canvas.paste(image.convert("RGB"), (x, y))
            draw.text((x, y + thumb - 18), source.name[:18], fill=(0, 0, 0))
    canvas.save(path, quality=90)

"""Tile a project's frames into sheets for review.

There is no automated test for whether a picture is any good, so the only way
to catch a bad frame is to look at all of them. At 77 shots that needs to be
one glance, not seventy-seven.

Each frame is labelled with its shot number so a bad one can be regenerated
directly:

    python -m pipeline.step3_frames <slug> --only 47 --force

    python -m tests.contact_sheet <slug> [--cols 6] [--rows 4]
"""
from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw

from pipeline import config
from pipeline.outro import load_font
from pipeline.schema import Storyboard

THUMB_W = 400


def build(slug: str, cols: int = 6, rows: int = 4) -> list[Path]:
    sb = Storyboard.load(slug)
    frames = sorted((sb.dir / "frames").glob("[0-9]*.png"))
    if not frames:
        raise SystemExit(f"no frames in {sb.dir / 'frames'}")

    thumb_h = round(THUMB_W * config.OUT_H / config.OUT_W)
    label_h = 30
    cell_h = thumb_h + label_h
    per_sheet = cols * rows
    font = load_font(20)

    out_dir = Path("C:/Users/David/AppData/Local/Temp/contact") / slug
    out_dir.mkdir(parents=True, exist_ok=True)
    sheets = []

    for index in range(0, len(frames), per_sheet):
        batch = frames[index:index + per_sheet]
        used_rows = -(-len(batch) // cols)
        sheet = Image.new("RGB", (cols * THUMB_W, used_rows * cell_h), (18, 18, 18))
        draw = ImageDraw.Draw(sheet)

        for i, path in enumerate(batch):
            img = Image.open(path).convert("RGB").resize(
                (THUMB_W, thumb_h), Image.LANCZOS)
            x, y = (i % cols) * THUMB_W, (i // cols) * cell_h
            sheet.paste(img, (x, y))

            shot_id = int(path.stem)
            shot = sb.shots[shot_id - 1]
            tag = f"{shot_id:03d}" + ("  [asset]" if shot.kind == "asset" else "")
            draw.text((x + 8, y + thumb_h + 5), tag, font=font,
                      fill=(235, 235, 235))

        dest = out_dir / f"sheet_{index // per_sheet + 1}.png"
        sheet.save(dest)
        sheets.append(dest)

    return sheets


def main() -> None:
    ap = argparse.ArgumentParser(description="Tile frames for review.")
    ap.add_argument("slug")
    ap.add_argument("--cols", type=int, default=6)
    ap.add_argument("--rows", type=int, default=4)
    a = ap.parse_args()
    for sheet in build(a.slug, a.cols, a.rows):
        print(sheet)


if __name__ == "__main__":
    main()

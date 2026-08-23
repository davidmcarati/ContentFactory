"""Render one scene in every palette style, side by side, labelled.

Style is the one decision that has to be made by eye. This renders the same
composition at the same seed under each preset, so the only variable is the
style text, then tiles the results into a single labelled sheet.

It reads `pipeline.styles.PRESETS` rather than keeping its own list, so the
sheet always shows what a video would actually be rendered with. A probe that
compares prompts nobody ships is worse than no probe.

    .venv-pipeline/Scripts/python.exe -m tests.style_probe
"""
from __future__ import annotations

import time
from pathlib import Path

from PIL import Image, ImageDraw

from pipeline import styles, workflows
from pipeline.comfy_client import ComfyClient
from pipeline.outro import load_font

OUT = Path("C:/Users/David/AppData/Local/Temp/style_probe")

# One scene with a figure, some architecture and some texture -- enough for a
# style to show what it does with all three.
SCENE = ("a scholar in a workshop examining a mechanical drawing on a bench, "
         "arched stone window behind, tools and parchment scattered around")

TILE_W = 880
LABEL_H = 86


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    client = ComfyClient()
    client.require_up()

    presets = sorted(styles.PRESETS.values(), key=lambda p: p.number)
    rendered = []

    for preset in presets:
        dest = OUT / f"{preset.number}_{preset.key}.png"
        graph = workflows.build(
            "flux-schnell",
            prompt=f"{preset.prompt}, {SCENE}",
            negative="",                   # no effect on schnell; see AGENTS.md
            seed=7777,                     # same seed everywhere
            filename_prefix="style",
        )
        t0 = time.monotonic()
        client.render(graph, dest)
        print(f"  {preset.key:12s} {time.monotonic() - t0:5.1f}s")
        rendered.append((preset, dest))

    print(f"\nsheet: {build_sheet(rendered)}")


def build_sheet(rendered: list[tuple[styles.Preset, Path]]) -> Path:
    """Tile and label with Pillow.

    Not ffmpeg: `drawtext` segfaults in this build for want of a fontconfig
    default, which is exactly the trap AGENTS.md warns about.
    """
    tiles = []
    for preset, path in rendered:
        img = Image.open(path).convert("RGB")
        height = round(TILE_W * img.height / img.width)
        tiles.append((img.resize((TILE_W, height), Image.LANCZOS), preset))

    tile_h = tiles[0][0].height
    cell_h = tile_h + LABEL_H
    cols = min(3, len(tiles))
    rows = -(-len(tiles) // cols)

    sheet = Image.new("RGB", (cols * TILE_W, rows * cell_h), (16, 16, 18))
    draw = ImageDraw.Draw(sheet)
    big, small = load_font(38), load_font(26)

    for i, (img, preset) in enumerate(tiles):
        x, y = (i % cols) * TILE_W, (i // cols) * cell_h
        sheet.paste(img, (x, y))
        draw.text((x + 20, y + tile_h + 12),
                  f"{preset.number}  {preset.label.upper()}",
                  font=big, fill=(255, 255, 255))
        draw.text((x + 20, y + tile_h + 54), preset.key,
                  font=small, fill=(160, 160, 168))

    dest = OUT / "sheet.png"
    sheet.save(dest)
    return dest


if __name__ == "__main__":
    main()

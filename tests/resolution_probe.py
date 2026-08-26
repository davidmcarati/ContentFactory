"""What does Qwen actually cost per resolution, and where does it stop helping?

The 1536x864 composition size in config was measured on FLUX.1-schnell, whose
ceiling is about 1.5 MP: above it schnell stops composing a scene and starts
fusing and duplicating structure. Qwen is a different model trained at a
different size, and that number was inherited rather than remeasured.

Inheriting it is the same mistake in reverse. The original schnell table timed
three routes and never asked whether the picture was still right, which is how
a whole video shipped composed above the ceiling. So this measures both:

  * wall time per frame, and seconds per megapixel
  * the frames themselves, enlarged to delivery size and cropped 1:1, so the
    detail that the extra pixels were supposed to buy can be looked at

Same prompt, same seed, one variable.

    .venv-pipeline/Scripts/python.exe -m tests.resolution_probe
    .venv-pipeline/Scripts/python.exe -m tests.resolution_probe --repeats 2
"""
from __future__ import annotations

import argparse
import time
from pathlib import Path

from PIL import Image, ImageDraw

from pipeline import config, styles, workflows
from pipeline.comfy_client import ComfyClient
from pipeline.outro import load_font

OUT = Path("C:/Users/David/AppData/Local/Temp/resolution_probe")

# All 16:9 and all multiples of 16, which the VAE requires. 1536x864 is what
# the pipeline uses today; 1792x1008 is roughly Qwen's native 1328x1328 area
# reshaped to widescreen, and is the one most likely to matter.
SIZES = (
    (1024, 576),
    (1280, 720),
    (1536, 864),      # current
    (1792, 1008),     # ~ Qwen's native area at 16:9
    (2048, 1152),
    (2304, 1296),     # delivery size, generated directly
)

# Busy on purpose. A flat two-colour frame looks the same at every size; the
# differences show up in small repeated structure, which is also exactly what
# a model fuses when pushed past its ceiling.
SCENE = ("a crowded ancient market lane of mud brick stalls with potters, "
         "weavers and grain sellers, awnings overhead, baskets and pots "
         "stacked along the walls, figures at every stall")

SEED = 24680
CROP = 520          # side of the 1:1 detail crop, taken from delivery size


def main() -> None:
    ap = argparse.ArgumentParser(description="Time and judge Qwen by size.")
    ap.add_argument("--model", default="qwen")
    ap.add_argument("--style", default="midcentury")
    ap.add_argument("--repeats", type=int, default=1,
                    help="render each size N times and take the median")
    a = ap.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    client = ComfyClient()
    client.require_up()

    # One throwaway render first. The 12 GB of weights load on the first
    # frame, and without this the first size in the list wears that cost and
    # comes out looking slower than sizes twice its area.
    print("  warming up (loading weights, not timed)", flush=True)
    client.render(
        workflows.build(a.model, prompt="warmup", negative="", seed=1,
                        width=1024, height=576, compose=False,
                        filename_prefix="res_warmup"),
        OUT / "_warmup.png")

    rows = []
    for w, h in SIZES:
        times = []
        dest = OUT / f"{w}x{h}.png"
        for r in range(a.repeats):
            graph = workflows.build(
                a.model,
                prompt=f"{styles.resolve(a.style)}, {SCENE}",
                negative="",
                seed=SEED,
                width=w, height=h,
                compose=False,          # render at exactly this size
                filename_prefix=f"res_{w}x{h}",
            )
            t0 = time.monotonic()
            client.render(graph, dest)
            times.append(time.monotonic() - t0)
        median = sorted(times)[len(times) // 2]
        mp = w * h / 1e6
        rows.append((w, h, mp, median, dest))
        print(f"  {w:5d}x{h:<5d} {mp:5.2f} MP  {median:7.1f}s  "
              f"{median / mp:6.1f} s/MP", flush=True)

    freed = client.free()
    if freed:
        print(f"released {freed / 1024**3:.1f} GB of VRAM")

    base = next(r for r in rows if (r[0], r[1]) == (config.COMPOSE_W, config.COMPOSE_H))
    print(f"\nagainst the current {base[0]}x{base[1]}:")
    for w, h, mp, secs, _ in rows:
        print(f"  {w:5d}x{h:<5d} {secs / base[3]:5.2f}x the time, "
              f"{mp / base[2]:5.2f}x the pixels")

    print(f"\nframes:  {build_sheet(rows)}")
    print(f"detail:  {build_crops(rows)}")


def _delivered(path: Path) -> Image.Image:
    """What the pipeline would actually ship: this render, enlarged to GEN."""
    img = Image.open(path).convert("RGB")
    if img.size != (config.GEN_W, config.GEN_H):
        img = img.resize((config.GEN_W, config.GEN_H), Image.LANCZOS)
    return img


def build_sheet(rows) -> Path:
    tile_w = 760
    tile_h = round(tile_w * config.GEN_H / config.GEN_W)
    cols, head, label = 3, 56, 58
    n = len(rows)
    sheet = Image.new("RGB", (cols * tile_w, head + -(-n // cols) * (tile_h + label)),
                      (16, 16, 18))
    draw = ImageDraw.Draw(sheet)
    big, small = load_font(30), load_font(23)
    draw.text((16, 14), "same prompt and seed, enlarged to delivery size",
              font=big, fill=(255, 255, 255))

    for i, (w, h, mp, secs, path) in enumerate(rows):
        x, y = (i % cols) * tile_w, head + (i // cols) * (tile_h + label)
        sheet.paste(_delivered(path).resize((tile_w, tile_h), Image.LANCZOS), (x, y))
        draw.text((x + 14, y + tile_h + 14),
                  f"{w}x{h}   {mp:.2f} MP   {secs:.0f}s",
                  font=small, fill=(220, 220, 228))
    dest = OUT / "sheet.png"
    sheet.save(dest)
    return dest


def build_crops(rows) -> Path:
    """A 1:1 crop of the same region, so detail is compared and not guessed."""
    cols, head, label = 3, 56, 52
    n = len(rows)
    sheet = Image.new("RGB", (cols * CROP, head + -(-n // cols) * (CROP + label)),
                      (16, 16, 18))
    draw = ImageDraw.Draw(sheet)
    big, small = load_font(30), load_font(23)
    draw.text((16, 14), f"1:1 crop, {CROP}px from the centre of each",
              font=big, fill=(255, 255, 255))

    left = (config.GEN_W - CROP) // 2
    top = (config.GEN_H - CROP) // 2
    for i, (w, h, mp, secs, path) in enumerate(rows):
        x, y = (i % cols) * CROP, head + (i // cols) * (CROP + label)
        sheet.paste(_delivered(path).crop((left, top, left + CROP, top + CROP)), (x, y))
        draw.text((x + 12, y + CROP + 12), f"{w}x{h}   {mp:.2f} MP",
                  font=small, fill=(220, 220, 228))
    dest = OUT / "crops.png"
    sheet.save(dest)
    return dest


if __name__ == "__main__":
    main()

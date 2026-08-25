"""Render the same prompts through every model, side by side, labelled.

Choosing an image model by reading about it is how this project ended up
shipping a video composed above the resolution ceiling: the argument was
sound and the pictures were wrong. So the models argue with pictures.

The prompts are not invented for the probe. Each one is a real shot from a
finished video, chosen because of how it failed on FLUX.1-schnell -- a frame
that came back with six fingers, a frame that came back reading MOLNTUR, a
frame that came back as a pleasant landscape with no relation to the idea.
A probe that compares prompts nobody ships is worse than no probe.

Same seed everywhere, so the only variable is the model.

    .venv-pipeline/Scripts/python.exe -m tests.model_probe
    .venv-pipeline/Scripts/python.exe -m tests.model_probe --models qwen chroma
"""
from __future__ import annotations

import argparse
import time
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw

from pipeline import styles, workflows
from pipeline.comfy_client import ComfyClient
from pipeline.outro import load_font

OUT = Path("C:/Users/David/AppData/Local/Temp/model_probe")

SEED = 7777
TILE_W = 760
LABEL_H = 118


@dataclass(frozen=True)
class Case:
    key: str
    style: str
    prompt: str
    tests: str          # what this frame is here to settle


CASES = (
    Case("hands", "midcentury",
         "a flat graphic column of small stacked segments beside a row of "
         "larger plain discs, muted restrained palette, clean geometric design",
         "abstraction with no scene: schnell drew a numbered bar chart"),
    Case("text", "midcentury",
         "a row of carved stone month markers standing in an ancient Roman "
         "courtyard, plainly out of sequence, muted restrained palette",
         "writing in frame: schnell wrote MOLNTUR across it"),
    Case("weekdays", "midcentury",
         "a flat graphic row of identical blocks where some carry a planetary "
         "disc and others carry a carved head instead, muted restrained palette",
         "invented labels: schnell produced Weelay / Dicky / Gunday"),
    Case("count", "cartoon",
         "a nineteenth century physician marking small crosses on a street map "
         "spread across a desk, cel animation, lamplight",
         "a map, which is an object that carries writing"),
    Case("anatomy", "cartoon",
         "a nineteenth century labourer counting coins at a table in a bare "
         "rented room, cel animation, dim lamplight",
         "hands at middle distance, and a period the model keeps modernising"),
    Case("abstract", "papercut",
         "a cut paper ring of silhouettes facing inward, every one holding up "
         "an identical plain rectangle, flat colour",
         "an idea with no physical form -- the class that failed hardest"),
    Case("scene", "papercut",
         "a cut paper ancient Mesopotamian temple courtyard with clay tablets "
         "stacked on low tables, mud brick walls, warm ochres",
         "a concrete period scene, the case schnell already handles well"),
    Case("crowd", "cartoon",
         "a crowded ancient market lane of mud brick stalls with potters, "
         "weavers and grain sellers, cel animation, warm palette",
         "many figures at once, where composition tends to fuse"),
)

# schnell ignores this; the other two do not. That is part of what is being
# compared -- the negative prompt is a lever this project has never had.
NEGATIVE = "text, watermark, signature, blurry, deformed, lowres, extra fingers"


def main() -> None:
    ap = argparse.ArgumentParser(description="Compare image models by eye.")
    ap.add_argument("--models", nargs="+", default=["flux-schnell", "chroma", "qwen"])
    ap.add_argument("--cases", nargs="+", help="only these case keys")
    a = ap.parse_args()

    cases = [c for c in CASES if not a.cases or c.key in a.cases]
    OUT.mkdir(parents=True, exist_ok=True)
    client = ComfyClient()
    client.require_up()

    timings: dict[str, list[float]] = {}
    grid: dict[tuple[str, str], Path] = {}

    ran = []
    for model in a.models:
        print(f"\n== {model} ==", flush=True)
        # Ask before rendering rather than after: a missing quant otherwise
        # shows up as a validation error on the first case, having already
        # loaded whatever else the graph needed.
        problems = workflows.verify_backend(client, model)
        if problems:
            for p in problems:
                print(f"  SKIPPED: {p}")
            continue
        ran.append(model)
        timings[model] = []
        for case in cases:
            dest = OUT / f"{case.key}_{model}.png"
            graph = workflows.build(
                model,
                prompt=f"{styles.resolve(case.style)}, {case.prompt}",
                negative=NEGATIVE,
                seed=SEED,
                filename_prefix=f"probe_{model}",
            )
            t0 = time.monotonic()
            client.render(graph, dest)
            spent = time.monotonic() - t0
            timings[model].append(spent)
            grid[(case.key, model)] = dest
            print(f"  {case.key:10s} {spent:6.1f}s", flush=True)

    print("\nmedian seconds per frame:")
    for model, times in timings.items():
        ordered = sorted(times)
        median = ordered[len(ordered) // 2]
        print(f"  {model:14s} {median:6.1f}s   "
              f"({min(ordered):.1f}-{max(ordered):.1f})")

    print(f"\nsheet: {build_sheet(cases, ran, grid, timings)}")


def build_sheet(cases, models, grid, timings) -> Path:
    """One row per prompt, one column per model, so the eye compares across."""
    first = Image.open(next(iter(grid.values())))
    tile_h = round(TILE_W * first.height / first.width)
    cell_h = tile_h + LABEL_H

    head = 96
    sheet = Image.new("RGB", (len(models) * TILE_W, head + len(cases) * cell_h),
                      (16, 16, 18))
    draw = ImageDraw.Draw(sheet)
    big, mid, small = load_font(40), load_font(30), load_font(23)

    for col, model in enumerate(models):
        times = sorted(timings.get(model) or [0])
        median = times[len(times) // 2]
        draw.text((col * TILE_W + 20, 22),
                  f"{model.upper()}   {median:.0f}s/frame",
                  font=big, fill=(255, 255, 255))

    for row, case in enumerate(cases):
        y = head + row * cell_h
        for col, model in enumerate(models):
            path = grid.get((case.key, model))
            if not path or not path.exists():
                continue
            img = Image.open(path).convert("RGB").resize(
                (TILE_W, tile_h), Image.LANCZOS)
            sheet.paste(img, (col * TILE_W, y))
        draw.text((20, y + tile_h + 10), case.key.upper(),
                  font=mid, fill=(255, 255, 255))
        draw.text((20, y + tile_h + 48), case.tests,
                  font=small, fill=(158, 158, 166))

    dest = OUT / "sheet.png"
    sheet.save(dest)
    return dest


if __name__ == "__main__":
    main()

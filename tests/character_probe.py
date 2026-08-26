"""Can the same cartoon person survive being drawn seven times?

Character consistency is the open limit in this project: every frame is
generated independently, so a recurring human has a different face in every
shot. The usual answers are a style LoRA or reference conditioning, both of
which are real work.

This probe tests a cheaper one -- picking a drawing style so simplified that
there is almost nothing left to be inconsistent about. A white oval face with
two dot eyes reads as "the same person" across shots in a way a rendered face
never will, and mitten hands with no fingers retire the other standing defect
at the same time.

Seven scenes, one character description reused verbatim, one seed. What the
sheet has to answer: does he stay recognisably himself, and does the style
hold when the scene changes.

    .venv-pipeline/Scripts/python.exe -m tests.character_probe
    .venv-pipeline/Scripts/python.exe -m tests.character_probe --seed 4242
"""
from __future__ import annotations

import argparse
import time
from pathlib import Path

from PIL import Image, ImageDraw

from pipeline import workflows
from pipeline.comfy_client import ComfyClient
from pipeline.outro import load_font

OUT = Path("C:/Users/David/AppData/Local/Temp/character_probe")

# The style under test. Every clause is doing a job:
#   thick uniform black outline    the look, and it hides small errors
#   flat white faces, dot eyes     almost no identity to get wrong
#   simple mitten hands            no fingers to count or malform
#   flat unshaded colour           nothing for the model to render badly
#   simple flat scenery, props     "plain background" emptied the frame
STYLE = ("simple 2D cartoon illustration, thick uniform black outline, "
         "flat white faces with small round dot eyes and thin eyebrows, "
         "simple white mitten hands, flat unshaded colour blocking, "
         "simple flat scenery with a few clear props, "
         "clean modern webcomic look")

# One description, reused word for word. If he drifts, he drifts here.
MAN = ("a man with short dark hair and a small dark beard, wearing a plain "
       "brown tunic")

# Framing is stated in every scene, never in the style. The first run left it
# out and came back with seven centred medium shots -- fine one at a time,
# monotonous for eleven minutes. The style says how to draw; the shot says
# where the camera is.
ONE = (
    ("wide", f"wide shot, {MAN} small in the frame, standing alone on dry "
             f"scrubland with rocks and a low bush, big empty sky"),
    ("offering", f"medium shot, {MAN} holding out a small object to another "
                 f"man with black hair in a pale tunic, dry ground, a bow and "
                 f"arrows lying between them"),
    ("desk", f"{MAN} seen from behind, sitting at a plain wooden table facing "
             f"a window, cup and bowl on the table"),
    ("wall", f"wide shot from the side, {MAN} standing before a tall wall of "
             f"pinned notes joined by threads, a lamp and a chair beside him"),
    ("crowd", f"high angle looking down, {MAN} in a dense crowd of others "
              f"drawn the same way, market stalls around them"),
    ("modern", f"{MAN} with the same hair and beard, in a modern zip jacket "
               f"and jeans, waiting at a bus stop, parked cars behind"),
    ("closeup", f"close up, {MAN}, head and shoulders, a cluttered shelf "
                f"behind him"),
)

# The set that matters for a real video. One recurring character was the
# wrong question: a script needs a different person almost every shot, and
# what has to survive is the drawing style, not the man. Deliberately spread
# across age, sex, era, dress and group size, because those are what a
# style breaks on.
CAST = (
    ("older-woman", "medium shot, an older woman with short grey hair and "
                    "glasses in a green cardigan, sitting at a kitchen table "
                    "with a newspaper and a mug, morning light"),
    ("young-man", "a young man with messy fair hair in a dark hoodie, hunched "
                  "at a laptop in a dim room at night, glow on his face"),
    ("statistician", "wide shot, a nineteen forties statistician in shirt "
                     "sleeves and braces at a broad desk, a city map pinned on "
                     "the wall behind him, filing cabinets"),
    ("street-crowd", "high angle, a busy modern street crowd of clearly "
                     "different people, various ages and clothes, shopfronts "
                     "behind them"),
    ("whisper", "two office workers in suits leaning together in a corridor, "
                "one whispering, another figure walking away down the hall"),
    ("child", "a small child in striped pyjamas sitting up in bed looking at "
              "a dark wardrobe, night, one lamp"),
    ("scientist", "a woman in a lab coat at a laboratory bench with flasks and "
                  "an instrument, bright clean room, seen from the side"),
)

SETS = {"one": ONE, "cast": CAST}

TILE_W = 640
LABEL_H = 64


def main() -> None:
    ap = argparse.ArgumentParser(description="Test cartoon character consistency.")
    ap.add_argument("--seed", type=int, default=8811)
    ap.add_argument("--model", default="qwen")
    ap.add_argument("--set", choices=sorted(SETS), default="cast",
                    help="cast: different people, one style (what a video "
                         "needs). one: the same person seven times.")
    ap.add_argument("--vary-seed", action="store_true",
                    help="a different seed per scene, to see how much the "
                         "seed alone was holding the character together")
    a = ap.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    client = ComfyClient()
    client.require_up()

    rendered = []
    scenes = SETS[a.set]
    for i, (key, scene) in enumerate(scenes):
        dest = OUT / f"{a.set}_{i + 1}_{key}.png"
        graph = workflows.build(
            a.model,
            prompt=f"{STYLE}, {scene}",
            negative="photorealistic, 3d render, detailed face, shading, gradient",
            seed=a.seed + (i if a.vary_seed else 0),
            filename_prefix="charprobe",
        )
        t0 = time.monotonic()
        client.render(graph, dest)
        print(f"  {key:10s} {time.monotonic() - t0:6.1f}s", flush=True)
        rendered.append((key, scene, dest))

    freed = client.free()
    if freed:
        print(f"released {freed / 1024**3:.1f} GB of VRAM")
    print(f"\nsheet: {build_sheet(rendered, a.seed, a.vary_seed, a.set)}")


def build_sheet(rendered, seed: int, varied: bool, name: str) -> Path:
    tiles = []
    for key, scene, path in rendered:
        img = Image.open(path).convert("RGB")
        h = round(TILE_W * img.height / img.width)
        tiles.append((img.resize((TILE_W, h), Image.LANCZOS), key))

    tile_h = tiles[0][0].height
    cell_h = tile_h + LABEL_H
    cols = 4
    rows = -(-len(tiles) // cols)
    head = 60

    sheet = Image.new("RGB", (cols * TILE_W, head + rows * cell_h), (16, 16, 18))
    draw = ImageDraw.Draw(sheet)
    big, small = load_font(32), load_font(24)
    head_text = ("different people, one style" if name == "cast"
                 else "one character")
    draw.text((18, 16),
              f"{head_text}, {'a seed each' if varied else f'seed {seed}'}",
              font=big, fill=(255, 255, 255))

    for i, (img, key) in enumerate(tiles):
        x = (i % cols) * TILE_W
        y = head + (i // cols) * cell_h
        sheet.paste(img, (x, y))
        draw.text((x + 16, y + tile_h + 16), key.upper(),
                  font=small, fill=(200, 200, 210))

    dest = OUT / f"sheet_{name}{'_varied' if varied else ''}.png"
    sheet.save(dest)
    return dest


if __name__ == "__main__":
    main()

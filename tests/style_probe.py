"""Render one scene in several style prompts, side by side, to choose from.

Style is the one decision that has to be made by eye. This renders the same
composition and seed under each candidate so the only variable is the style
text, then tiles the results into a single sheet.

    .venv-pipeline/Scripts/python.exe -m tests.style_probe
"""
from __future__ import annotations

import subprocess
import time
from pathlib import Path

from pipeline import workflows
from pipeline.comfy_client import ComfyClient

OUT = Path("C:/Users/David/AppData/Local/Temp/style_probe")

# One scene with a figure, some architecture and some texture -- enough for a
# style to show what it does with all three.
SCENE = ("a scholar in a workshop examining a mechanical drawing on a bench, "
         "arched stone window behind, tools and parchment scattered around")

STYLES = {
    "1_flat_vector": "flat vector clipart illustration, bold clean shapes, "
                     "limited flat colour palette, no gradients, crisp edges",
    "2_cel_cartoon": "2D cartoon illustration, clean black line art, cel "
                     "shading, flat colours, animation production style",
    "3_storybook": "2D children's storybook watercolour illustration, soft "
                   "washes, visible paper grain, hand painted, flat perspective",
    "4_midcentury": "mid-century modern flat illustration, textured paper, "
                    "muted retro palette, geometric shapes, screen print look",
    "5_ink_novel": "2D graphic novel ink illustration, bold linework, cross "
                   "hatching, limited spot colour, flat comic panel style",
    "6_papercut": "flat paper cut collage illustration, layered coloured "
                  "paper, simple shapes, soft drop shadows, 2D craft style",
}

NEGATIVE = ("3d render, octane, blender, photorealistic, photograph, "
            "volumetric lighting, depth of field, ray tracing, cgi")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    client = ComfyClient()
    client.require_up()

    paths = []
    for name, style in STYLES.items():
        dest = OUT / f"{name}.png"
        graph = workflows.build(
            "flux-schnell",
            prompt=f"{style}, {SCENE}",
            negative=NEGATIVE,
            seed=7777,                     # same seed everywhere
            filename_prefix="style",
        )
        t0 = time.monotonic()
        client.render(graph, dest)
        print(f"  {name:16s} {time.monotonic() - t0:5.1f}s")
        paths.append(dest)

    # Tile into a 3x2 contact sheet.
    args = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]
    for p in paths:
        args += ["-i", str(p)]
    chain = "".join(f"[{i}]scale=760:-1[s{i}];" for i in range(len(paths)))
    chain += "[s0][s1][s2]hstack=3[top];[s3][s4][s5]hstack=3[bot];[top][bot]vstack"
    args += ["-filter_complex", chain, str(OUT / "sheet.png")]
    subprocess.run(args, check=True)
    print(f"\nsheet: {OUT / 'sheet.png'}")
    print("order: " + " | ".join(STYLES))


if __name__ == "__main__":
    main()

"""End-to-end smoke test with no models involved.

Fabricates a small project -- synthetic frames, silent placeholder narration --
and drives steps 2 and 4 for real. That exercises the parts most likely to be
quietly wrong: the Ken Burns filter graph, the crossfade offset arithmetic, the
subtitle timing, and whether the finished file's duration actually matches the
timeline the storyboard predicted.

Run:  .venv-pipeline/Scripts/python.exe -m tests.smoke
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

from PIL import Image, ImageDraw

from pipeline import config
from pipeline.ffmpeg_util import duration as probe_duration
from pipeline.schema import Motion, Shot, Storyboard, Style
from pipeline.step2_voice import voice
from pipeline.step4_assemble import assemble

SLUG = "smoketest"

SHOTS = [
    ("Roman concrete has survived two thousand years of waves.", "in"),
    ("Modern concrete cracks within decades of being poured.", "left"),
    ("The difference turned out to be seawater, of all things.", "out"),
    ("It reacts with volcanic ash to grow new crystals inside the cracks.", "right"),
    ("The material heals itself, slowly, for centuries.", "none"),
]

PALETTE = [(38, 70, 83), (42, 157, 143), (233, 196, 106),
           (244, 162, 97), (231, 111, 81)]


def make_frame(path: Path, index: int, label: str) -> None:
    """A flat colour with a big number, so panning and cuts are obvious."""
    img = Image.new("RGB", (config.GEN_W, config.GEN_H), PALETTE[index % len(PALETTE)])
    d = ImageDraw.Draw(img)
    # Grid lines make any Ken Burns stutter visible at a glance.
    for x in range(0, config.GEN_W, 96):
        d.line([(x, 0), (x, config.GEN_H)], fill=(255, 255, 255), width=1)
    for y in range(0, config.GEN_H, 96):
        d.line([(0, y), (config.GEN_W, y)], fill=(255, 255, 255), width=1)
    d.text((40, 40), f"SHOT {index + 1}", fill=(255, 255, 255))
    d.text((40, 70), label, fill=(255, 255, 255))
    img.save(path)


def build() -> Storyboard:
    sb = Storyboard(
        slug=SLUG,
        title="Smoke Test",
        style=Style(base_prompt="test pattern", model="none"),
        shots=[
            Shot(id=i + 1, vo=vo, image_prompt=f"test frame {i + 1}",
                 motion=Motion(pan=pan, zoom=1.15))
            for i, (vo, pan) in enumerate(SHOTS)
        ],
    )
    if sb.dir.exists():
        shutil.rmtree(sb.dir)
    sb.ensure_dirs()

    for i, shot in enumerate(sb.shots):
        frame = sb.dir / "frames" / f"{shot.stem}.png"
        make_frame(frame, i, f"pan={shot.motion.pan}")
        shot.frame_path = str(frame)
    return sb


def main() -> int:
    print("=== validating storyboard ===")
    sb = build()
    sb.require_valid()
    print(f"  {len(sb.shots)} shots, all valid")

    print("=== step 2: narration (fake backend) ===")
    voice(sb, fake=True)

    print("=== step 4: assembly ===")
    out = assemble(sb, force=True)

    print("=== checks ===")
    ok = True

    outro_sec = config.OUTRO_SECONDS if (
        config.OUTRO_ENABLED and config.OUTRO_SECONDS > 0) else 0.0
    expected = sb.total_sec + outro_sec

    actual = probe_duration(out)
    drift = abs(actual - expected)
    print(f"  duration      {actual:6.2f}s vs predicted {expected:6.2f}s "
          f"({sb.total_sec:.2f}s narration + {outro_sec:.0f}s end card) "
          f"-- drift {drift * 1000:.0f} ms")
    if drift > 0.15:
        print("  FAIL: video and narration timelines disagree")
        ok = False

    srt = sb.dir / "subtitles.srt"
    cues = srt.read_text(encoding="utf-8").strip().split("\n\n")
    print(f"  subtitles     {len(cues)} cues for {len(sb.shots)} shots")
    if len(cues) != len(sb.shots):
        print("  FAIL: cue count does not match shot count")
        ok = False

    clips = sorted((sb.dir / "clips").glob("*.mp4"))
    want = len(sb.shots) + (1 if outro_sec else 0)
    print(f"  clips         {len(clips)} rendered (expected {want})")
    if len(clips) != want:
        print("  FAIL: missing clips")
        ok = False

    if outro_sec:
        card = sb.dir / "clips" / "outro.mp4"
        card_len = probe_duration(card) if card.exists() else 0.0
        print(f"  end card      {card_len:.2f}s")
        if abs(card_len - outro_sec) > 0.1:
            print("  FAIL: end card is not the configured length")
            ok = False

    print("\n" + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

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
from pipeline.ffmpeg_util import duration as probe_duration, video_duration
from pipeline.schema import Motion, Shot, Storyboard, Style
from pipeline.step2_voice import voice
from pipeline.step4_assemble import assemble, render_clips

SLUG = "smoketest"

# Deliveries now hold their own sources, so a project directory is a folder in
# DELIVERY_ROOT beside the real videos. A throwaway test has no business
# sitting there, so it is pointed at scratch space for the duration.
config.DELIVERY_ROOT = Path(config.ROOT / ".smoketest")

# Two of these stop mid-sentence, on a comma, because cut-driven pacing breaks
# sentences across shots and each half then carries a different tail. That
# arithmetic is exactly what this test exists to check, so the fixture has to
# contain some.
SHOTS = [
    ("Roman concrete has survived two thousand years of waves.", "in"),
    ("Modern concrete cracks within decades of being poured.", "left"),
    ("The difference turned out to be seawater,", "out"),
    ("of all things.", "right"),
    ("It reacts with volcanic ash", "in"),
    ("to grow new crystals inside the cracks.", "left"),
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


def build(cuts: bool) -> Storyboard:
    sb = Storyboard(
        slug=SLUG,
        title="Smoke Test",
        style=Style(base_prompt="test pattern", model="none"),
        # Follows the active profile, so `CF_PROFILE=shorts python -m
        # tests.smoke` exercises the vertical geometry end to end -- fabricated
        # 1080x1920 frames, real ffmpeg, the same drift check. It needs no
        # models, which makes it the cheapest way to catch a profile that
        # produces a file of the wrong shape or a timeline that no longer
        # locks.
        aspect=config.ASPECT,
        shots=[
            Shot(id=i + 1, vo=vo, image_prompt=f"test frame {i + 1}",
                 motion=Motion(pan="none", zoom=1.0) if cuts
                 else Motion(pan=pan, zoom=1.15),
                 transition="cut" if cuts else "crossfade")
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


def run_once(cuts: bool) -> bool:
    kind = "hard cuts + concat" if cuts else "crossfades + xfade chain"
    print(f"\n########  {kind}  ########")
    print("=== validating storyboard ===")
    sb = build(cuts)
    sb.require_valid()
    print(f"  {len(sb.shots)} shots, all valid")

    print("=== step 2: narration (fake backend) ===")
    voice(sb, fake=True)

    print("=== step 4: assembly ===")
    # The frames here are fabricated colour cards; there is nothing to look
    # at, and this test exists to check timing arithmetic rather than
    # pictures. It is the one place the review gate is legitimately skipped.
    out = assemble(sb, force=True, skip_review=True)

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
    # At least one cue per shot, not exactly one. A line longer than
    # SUB_MAX_CHARS pages into several cues by design, and the equality held
    # here only because the test lines happen to fit inside 54 characters --
    # under the vertical profile the same lines wrap at 26 and the check
    # failed on correct output. What would be a real fault is a shot whose
    # narration produced no card at all.
    if len(cues) < len(sb.shots):
        print("  FAIL: fewer subtitle cues than shots; a line lost its card")
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

    print("  " + ("ok" if ok else "FAILED"))
    return ok


def run_truncated_clip() -> bool:
    """A killed render leaves a stub clip. Does anything notice?

    Nothing did. Stopping a background job mid-write left a 48-byte file in
    clips/, render_clips treated it as finished because it existed, and the
    concat demuxer stopped dead there -- the published video had 43 seconds
    of picture under ten minutes of narration, and the drift check read 0 ms
    because a container reports its longest stream.
    """
    print("\n########  a clip left behind by a killed render  ########")
    sb = build(cuts=True)
    sb.require_valid()
    voice(sb, fake=True)
    render_clips(sb)

    victim = sb.dir / "clips" / f"{sb.shots[2].stem}.mp4"
    whole = victim.stat().st_size
    victim.write_bytes(b"\x00" * 48)
    print(f"  truncated {victim.name}: {whole} bytes -> 48")

    out = assemble(sb, skip_review=True)
    ok = True

    rebuilt = victim.stat().st_size
    print(f"  after assembly {victim.name} is {rebuilt} bytes")
    if rebuilt < 1024:
        print("  FAIL: the stub was accepted as a finished clip")
        ok = False

    expected = sb.total_sec + (config.OUTRO_SECONDS if (
        config.OUTRO_ENABLED and config.OUTRO_SECONDS > 0) else 0.0)
    picture = video_duration(out)
    print(f"  picture {picture:.2f}s vs {expected:.2f}s of timeline")
    if abs(picture - expected) > 0.15:
        print("  FAIL: the picture is not as long as the narration")
        ok = False

    print("  " + ("ok" if ok else "FAILED"))
    return ok


def main() -> int:
    # Both assembly paths, because they do different timeline arithmetic. The
    # crossfade chain pads every clip and lets the transition eat the surplus;
    # concat pads nothing. Drift between picture and voice is the exact
    # failure this test exists to catch, so covering one path while shipping
    # the other would defeat the point of having it.
    results = [run_once(cuts=False), run_once(cuts=True),
               run_truncated_clip()]
    ok = all(results)
    print("\n" + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

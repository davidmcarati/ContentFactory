"""Run the whole pipeline for one project.

    python -m pipeline.run roman-concrete

Each step is skippable and resumable, so this is safe to re-run: it will not
redo narration or frames that already exist unless told to.
"""
from __future__ import annotations

import argparse
import time

from .comfy_client import ComfyClient
from .publish import publish
from .review import review
from .schema import Storyboard
from .step2_voice import voice
from .step3_frames import frames
from .step4_assemble import assemble

STEPS = ("voice", "frames", "review", "assemble", "publish")


def _fmt(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    return f"{m}m{s:02d}s" if m else f"{s}s"


def run(slug: str, *, steps: tuple[str, ...] = STEPS, force: bool = False,
        fake_voice: bool = False) -> None:
    sb = Storyboard.load(slug)
    sb.require_valid()

    todo = [s for s in sb.shots if s.image_prompt.startswith("TODO")]
    if todo and "frames" in steps:
        raise SystemExit(
            f"{len(todo)} shots still have placeholder image prompts "
            f"(first: shot {todo[0].id}). Write them before rendering."
        )

    started = time.monotonic()

    if "voice" in steps:
        print("== step 2: narration ==")
        voice(sb, force=force, fake=fake_voice)

    if "frames" in steps:
        print("\n== step 3: frames ==")
        # Reload: step 2 rewrote the storyboard with measured durations.
        sb = Storyboard.load(slug)
        frames(sb, force=force)
        # The image model is holding most of the card. Nothing later needs it,
        # and leaving it resident makes the ffmpeg stage share a hot GPU for
        # no reason.
        ComfyClient().free()

    if "review" in steps:
        print("\n== step 3b: review the frames ==")
        sb = Storyboard.load(slug)
        if review(sb) != 0:
            # Deliberately not a crash: everything up to here is done and
            # kept. The run stops because the next thing it would do is build
            # a video out of pictures nobody has seen.
            print(f"\nstopping here. Look at the sheets, fix what is badly "
                  f"wrong, then:\n"
                  f"  python -m pipeline.review {slug} --accept\n"
                  f"  python -m pipeline.run {slug} --from assemble")
            return

    if "assemble" in steps:
        print("\n== step 4: assembly ==")
        sb = Storyboard.load(slug)
        assemble(sb, force=force)

    if "publish" in steps:
        print("\n== step 5: publish ==")
        # The thumbnail needs ComfyUI, so this runs before anything else claims
        # the card back.
        sb = Storyboard.load(slug)
        publish(sb, force=force)

    print(f"\ntotal {_fmt(time.monotonic() - started)}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Run the content pipeline.")
    ap.add_argument("slug")
    ap.add_argument("--only", choices=STEPS, nargs="+",
                    help="run just these steps")
    ap.add_argument("--from", dest="start", choices=STEPS,
                    help="start at this step and run the rest")
    ap.add_argument("--force", action="store_true",
                    help="redo work that already exists on disk")
    ap.add_argument("--fake-voice", action="store_true",
                    help="silent placeholder narration, for testing timing")
    a = ap.parse_args()

    if a.only:
        steps = tuple(a.only)
    elif a.start:
        steps = STEPS[STEPS.index(a.start):]
    else:
        steps = STEPS

    run(a.slug, steps=steps, force=a.force, fake_voice=a.fake_voice)


if __name__ == "__main__":
    main()

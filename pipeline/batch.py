"""Run several projects back to back, unattended.

A week of videos is not ten independent runs. The card holds one 16 GB
checkpoint; the cheapest possible ordering is to render every project's frames
in one sitting rather than dropping and reloading the model between them, then
look at everything, then let ffmpeg work through the cuts while nobody is
waiting.

`run()` already stops at the review gate and returns instead of raising, so a
batch needs no special stopping logic: it walks the list, each project gets as
far as it legitimately can, and the ones that are waiting on a human say so.

    python -m pipeline.batch gold clothes dark            # voice + frames
    python -m pipeline.batch gold clothes dark --from assemble

A project that fails outright does not take the rest of the night with it --
the traceback is printed, the failure is recorded, and the next slug starts.
That matters most at step 3, where a single unreachable image host used to end
a nine-minute render.
"""
from __future__ import annotations

import argparse
import time
import traceback

from .run import STEPS, run


def _fmt(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}h{m:02d}m" if h else f"{m}m{s:02d}s"


def batch(slugs: list[str], *, steps: tuple[str, ...] = STEPS,
          force: bool = False, fake_voice: bool = False) -> int:
    results: list[tuple[str, str, float]] = []
    started = time.monotonic()

    for i, slug in enumerate(slugs, 1):
        head = f"  [{i}/{len(slugs)}] {slug}  "
        print(f"\n{'=' * 70}\n{head}\n{'=' * 70}", flush=True)
        mark = time.monotonic()
        try:
            run(slug, steps=steps, force=force, fake_voice=fake_voice)
            outcome = "ok"
        except SystemExit as exc:
            # The review gate and the validation failures speak in SystemExit.
            print(f"\n{slug}: {exc}", flush=True)
            outcome = "blocked"
        except Exception:
            traceback.print_exc()
            outcome = "FAILED"
        results.append((slug, outcome, time.monotonic() - mark))

    print(f"\n{'=' * 70}\nbatch of {len(slugs)} in {_fmt(time.monotonic() - started)}")
    for slug, outcome, spent in results:
        print(f"  {slug:22s} {outcome:8s} {_fmt(spent)}")

    failed = sum(1 for _, o, _ in results if o == "FAILED")
    return 1 if failed else 0


def main() -> None:
    ap = argparse.ArgumentParser(description="Run the pipeline over many projects.")
    ap.add_argument("slugs", nargs="+")
    ap.add_argument("--only", choices=STEPS, nargs="+")
    ap.add_argument("--from", dest="start", choices=STEPS)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--fake-voice", action="store_true")
    a = ap.parse_args()

    if a.only:
        steps = tuple(a.only)
    elif a.start:
        steps = STEPS[STEPS.index(a.start):]
    else:
        # Default is deliberately not the whole pipeline: everything up to the
        # point where a human is genuinely needed.
        steps = ("voice", "frames", "review")

    raise SystemExit(batch(a.slugs, steps=steps, force=a.force,
                           fake_voice=a.fake_voice))


if __name__ == "__main__":
    main()

"""Step 3b: look at the frames before anything is built out of them.

Every visual defect this project has ever shipped or nearly shipped was
invisible to the test suite and obvious in a picture: oversharpened upscales,
82 px subtitles, fake body copy, invented signatures, a whole video rendered
above the model's resolution ceiling, a 446 px manuscript that reported itself
as 2560, malformed hands, and a decorator in a baseball cap painting a
medieval genealogy.

None of that can be asserted automatically, so this step does not pretend to.
What it does is make the looking unskippable:

  * runs the checks that *can* be mechanised -- the documented failure classes
    from STYLE.md, plus frames that came back nearly empty
  * builds contact sheets and tells you where they are
  * records that a human looked, and refuses to let step 4 proceed until one
    has

The record is tied to the frames themselves. Regenerate a frame and the
review is stale again, which is the case that matters: it is exactly how
unreviewed frames would otherwise reach a finished video.

    python -m pipeline.review <slug>            # check and report
    python -m pipeline.review <slug> --accept   # record that you looked
"""
from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageFilter, ImageStat

from . import assets, config
from .schema import Storyboard

REVIEW_FILE = "review.json"


# ---------------------------------------------------------------------------
# Prompt lint -- the rules in STYLE.md, as regexes
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Rule:
    key: str
    pattern: re.Pattern
    why: str


RULES = (
    Rule("count",
         re.compile(r"\b(two|three|four|five|six|seven|eight|nine|ten|"
                    r"eleven|twelve|dozen|a pair of)\b", re.I),
         "the model cannot count; describe the arrangement instead"),
    # "hand press", "hand printed", "handwriting" are not hands in frame.
    Rule("hands",
         re.compile(r"\b(hands?|fingers|fist|gripping|clutching)\b"
                    r"(?!\s+(press|printed|painted|drawn|written|writing|made))",
                    re.I),
         "close-range hands come back malformed; show the object, not the actor"),
    Rule("writing",
         re.compile(r"\b(chart|poster|diagram|infographic|calendar|newspaper|"
                    r"magazine|manual|nameplate|signage|billboard|label)\b", re.I),
         "objects that carry writing get filled with invented text"),
    Rule("picture-of-a-picture",
         re.compile(r"\b(painting|panel|illustration|portrait|mural|drawing)\s+"
                    r"(of|showing|depicting)\b", re.I),
         "the model collapses the frame within the frame"),
    Rule("no-period",
         re.compile(r"\b(scribe|monk|writer|painter|clergyman|scholar|speaker|"
                    r"orator|prince|figure|figures|man|woman|men|women|people|"
                    r"couple|academics?|apprentices|workers?|editor)\b", re.I),
         "a person with no era named gets dressed in the present"),
)

_PERIOD = re.compile(
    r"\b(ancient|classical|greek|roman|byzantine|medieval|renaissance|"
    r"georgian|victorian|edwardian|eighteenth|nineteenth|twentieth|"
    r"fifteenth|sixteenth|seventeenth|twelfth|eighth|"
    r"nineteen \w+|modern|contemporary|present day)\b", re.I)


def lint_prompts(sb: Storyboard) -> list[tuple[int, str, str]]:
    """Flags, not errors. A rule can be knowingly broken -- see --accept."""
    out = []
    for shot in sb.shots:
        if shot.kind == "asset":
            continue
        text = shot.image_prompt
        for rule in RULES:
            if not rule.pattern.search(text):
                continue
            if rule.key == "no-period" and _PERIOD.search(text):
                continue
            out.append((shot.id, rule.key, rule.why))
    return out


# ---------------------------------------------------------------------------
# Frame lint -- what a picture can be measured for
# ---------------------------------------------------------------------------
def frame_energy(path: Path) -> float:
    """How much is actually in this frame?

    Mean edge magnitude on a downscaled copy. A frame that came back as a
    near-blank sheet of paper or an empty pale sky scores close to zero, and
    those are the ones that read as unfinished when held for eight seconds.
    Not a quality score -- a busy frame can still be wrong.
    """
    img = Image.open(path).convert("L").resize((256, 144), Image.LANCZOS)
    return ImageStat.Stat(img.filter(ImageFilter.FIND_EDGES)).mean[0]


EMPTY_FLOOR = 4.0          # measured: good frames sit well above this


def lint_frames(sb: Storyboard) -> list[tuple[int, str]]:
    out = []
    for shot in sb.shots:
        path = sb.dir / "frames" / f"{shot.stem}.png"
        if not path.exists():
            out.append((shot.id, "no frame rendered"))
            continue
        energy = frame_energy(path)
        if energy < EMPTY_FLOOR:
            out.append((shot.id, f"nearly empty (edge energy {energy:.1f})"))
        if shot.kind == "asset" and shot.asset and not assets.big_enough(shot.asset):
            out.append((shot.id, f"asset only {shot.asset.width}x"
                                 f"{shot.asset.height}, will be soft"))
    return out


# ---------------------------------------------------------------------------
# The record
# ---------------------------------------------------------------------------
def fingerprint(sb: Storyboard) -> dict[str, str]:
    """Identify the frames, so regenerating one invalidates the review."""
    marks = {}
    for shot in sb.shots:
        path = sb.dir / "frames" / f"{shot.stem}.png"
        if path.exists():
            stat = path.stat()
            marks[shot.stem] = f"{stat.st_size}:{int(stat.st_mtime)}"
    return marks


def stale(sb: Storyboard) -> list[str]:
    """Which shots have changed since the review, or were never reviewed."""
    record = sb.dir / REVIEW_FILE
    if not record.exists():
        return ["never"]
    saved = json.loads(record.read_text(encoding="utf-8")).get("frames", {})
    now = fingerprint(sb)
    return sorted(set(now) ^ set(saved)
                  | {k for k in now if saved.get(k) != now[k]})


def accept(sb: Storyboard, note: str = "") -> Path:
    record = sb.dir / REVIEW_FILE
    record.write_text(json.dumps({
        "note": note,
        "shots": len(sb.shots),
        "frames": fingerprint(sb),
    }, indent=2), encoding="utf-8")
    return record


def require_reviewed(sb: Storyboard) -> None:
    """Called by step 4. The gate, not a suggestion."""
    changed = stale(sb)
    if not changed:
        return
    if changed == ["never"]:
        raise SystemExit(
            f"{sb.slug}: frames have not been reviewed.\n"
            f"  python -m pipeline.review {sb.slug}\n"
            f"Look at the contact sheets it prints, fix what is badly wrong, "
            f"then rerun with --accept."
        )
    raise SystemExit(
        f"{sb.slug}: {len(changed)} frames changed since the review "
        f"({', '.join(changed[:8])}{'...' if len(changed) > 8 else ''}).\n"
        f"  python -m pipeline.review {sb.slug}"
    )


# ---------------------------------------------------------------------------
def review(sb: Storyboard, *, accept_it: bool = False, note: str = "") -> int:
    from tests.contact_sheet import build

    prompt_flags = lint_prompts(sb)
    frame_flags = lint_frames(sb)

    print(f"{sb.slug}: {len(sb.shots)} shots "
          f"({sum(1 for s in sb.shots if s.kind == 'asset')} real images)")

    if frame_flags:
        print(f"\nframes worth a second look ({len(frame_flags)}):")
        for shot_id, why in frame_flags:
            print(f"  shot {shot_id:03d}  {why}")

    if prompt_flags:
        print(f"\nprompts breaking a rule in STYLE.md ({len(prompt_flags)}):")
        for shot_id, key, why in prompt_flags:
            print(f"  shot {shot_id:03d}  [{key}] {why}")
        print("  (flags, not errors -- a rule can be broken deliberately)")

    if not frame_flags and not prompt_flags:
        print("\nno mechanical flags.")

    print("\ncontact sheets -- open these and look at every frame:")
    for sheet in build(sb.slug):
        print(f"  {sheet}")

    if accept_it:
        path = accept(sb, note)
        print(f"\nreviewed; recorded in {path}")
        return 0

    changed = stale(sb)
    if changed:
        print(f"\nNOT yet accepted. Step 4 will refuse to run.")
        print(f"When the frames look right: "
              f"python -m pipeline.review {sb.slug} --accept")
        return 1
    print("\nalready accepted, and nothing has changed since.")
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(description="Review frames before assembly.")
    ap.add_argument("slug")
    ap.add_argument("--accept", action="store_true",
                    help="record that you looked at the frames")
    ap.add_argument("--note", default="",
                    help="what you decided, for the record")
    a = ap.parse_args()

    sb = Storyboard.load(a.slug)
    raise SystemExit(review(sb, accept_it=a.accept, note=a.note))


if __name__ == "__main__":
    main()

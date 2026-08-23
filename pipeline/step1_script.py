"""Step 1: script and narration into a validated storyboard.

The writing itself happens in conversation -- an LLM is better at it than any
heuristic here. What this module does is the unglamorous half: chunk narration
into shot-sized pieces, pair each piece with an image prompt, vary the camera
move so a 120-shot video does not feel mechanical, and refuse to emit a
storyboard that later steps would choke on.

Two ways in:

  from a pair of text files, one narration paragraph or line per shot --
      step1_script.py new my-slug --title "..." --style "..." \
          --narration script.txt --prompts prompts.txt

  from a storyboard JSON written by hand or by a model --
      step1_script.py ingest my-slug --json draft.json
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from . import config, styles
from .schema import Motion, Shot, Storyboard, Style, Voice

# Target words per shot. Under ~12 the cuts feel twitchy against a still
# image; over ~40 the viewer has been staring at one picture too long.
TARGET_WORDS = 22
MAX_WORDS = 30

# Cycled rather than random so a rerun produces the same video, and ordered so
# the same move never lands twice in a row.
MOTION_CYCLE = [
    ("in", 1.12), ("left", 1.14), ("out", 1.10), ("right", 1.14),
    ("in", 1.16), ("up", 1.12), ("out", 1.13), ("down", 1.12),
]

_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"'(])")
_PARAGRAPH = re.compile(r"\n\s*\n")


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE.split(text.strip()) if s.strip()]


def _chunk_paragraph(paragraph: str) -> list[str]:
    chunks: list[str] = []
    current: list[str] = []

    for sentence in split_sentences(paragraph):
        words = len(sentence.split())
        running = sum(len(s.split()) for s in current)

        if current and running + words > MAX_WORDS:
            chunks.append(" ".join(current))
            current = [sentence]
        else:
            current.append(sentence)
            if running + words >= TARGET_WORDS:
                chunks.append(" ".join(current))
                current = []

    if current:
        chunks.append(" ".join(current))
    return chunks


def chunk_narration(text: str) -> list[str]:
    """Group sentences into shot-sized runs, one paragraph at a time.

    Sentences are never split mid-way: a cut inside a clause reads as a
    mistake, and the TTS prosody breaks too.

    Paragraph breaks are hard boundaries. A chunk that straddles one gets a
    single image for two unrelated ideas, which is exactly the shot a viewer
    notices as wrong -- the picture is still showing the previous point while
    the voice has moved on to the next.
    """
    chunks: list[str] = []
    for paragraph in _PARAGRAPH.split(text.strip()):
        if paragraph.strip():
            chunks.extend(_chunk_paragraph(paragraph))
    return chunks


def motion_for(index: int) -> Motion:
    pan, zoom = MOTION_CYCLE[index % len(MOTION_CYCLE)]
    return Motion(pan=pan, zoom=zoom)


def build(
    slug: str,
    title: str,
    base_prompt: str,
    narration: str,
    image_prompts: list[str] | None = None,
    *,
    model: str = "flux-schnell",
    voice_id: str = config.TTS_VOICE,
) -> Storyboard:
    lines = chunk_narration(narration)
    if not lines:
        raise ValueError("narration is empty after chunking")

    if image_prompts is None:
        # A placeholder per shot, so the storyboard is structurally valid and
        # a human can fill the prompts in afterwards.
        image_prompts = [f"TODO describe the visual for: {ln[:60]}" for ln in lines]
    elif len(image_prompts) != len(lines):
        raise ValueError(
            f"{len(lines)} narration chunks but {len(image_prompts)} image "
            f"prompts. Chunking is automatic, so write one prompt per "
            f"narration line and keep the files in step, or pass --prompts "
            f"only after checking the chunk count with --dry-run."
        )

    return Storyboard(
        slug=slug,
        title=title,
        style=Style(base_prompt=styles.resolve(base_prompt), model=model),
        voice=Voice(voice_id=voice_id),
        shots=[
            Shot(id=i + 1, vo=vo, image_prompt=ip, motion=motion_for(i))
            for i, (vo, ip) in enumerate(zip(lines, image_prompts))
        ],
    )


def report(sb: Storyboard) -> None:
    words = sum(len(s.vo.split()) for s in sb.shots)
    est_min = words / 155           # typical narration pace
    print(f"{sb.slug}: {len(sb.shots)} shots, {words} words")
    print(f"  estimated runtime {est_min:.1f} min (measured after step 2)")
    named = next((p for p in styles.PRESETS.values()
                  if p.prompt == sb.style.base_prompt), None)
    print(f"  style: {named.label if named else 'custom prompt'}")
    todo = sum(1 for s in sb.shots if s.image_prompt.startswith("TODO"))
    if todo:
        print(f"  {todo} image prompts still need writing")
    longest = max(sb.shots, key=lambda s: len(s.vo.split()))
    print(f"  longest shot: {len(longest.vo.split())} words (#{longest.id})")


def _read(path: str | None) -> str | None:
    return Path(path).read_text(encoding="utf-8") if path else None


def main() -> None:
    ap = argparse.ArgumentParser(description="Build a storyboard.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    new = sub.add_parser("new", help="build from narration + prompt text files")
    new.add_argument("slug")
    new.add_argument("--title", required=True)
    new.add_argument("--style", required=True,
                     help="one of: " + ", ".join(sorted(styles.PRESETS)) +
                          " (or the sheet number 2/4/6, or a full prompt)")
    new.add_argument("--narration", required=True, help="path to narration text")
    new.add_argument("--prompts", help="one image prompt per line")
    new.add_argument("--model", default="flux-schnell")
    new.add_argument("--voice", default=config.TTS_VOICE)
    new.add_argument("--dry-run", action="store_true",
                     help="print the chunking without writing anything")

    ing = sub.add_parser("ingest", help="validate and install a storyboard JSON")
    ing.add_argument("slug")
    ing.add_argument("--json", required=True)

    a = ap.parse_args()

    if a.cmd == "ingest":
        sb = Storyboard.from_dict(json.loads(Path(a.json).read_text(encoding="utf-8")))
        sb.slug = a.slug
    else:
        prompts = _read(a.prompts)
        sb = build(
            a.slug, a.title, a.style, _read(a.narration),
            [p.strip() for p in prompts.splitlines() if p.strip()] if prompts else None,
            model=a.model, voice_id=a.voice,
        )
        if a.dry_run:
            for shot in sb.shots:
                print(f"{shot.stem} [{len(shot.vo.split()):2d}w] {shot.vo}")
            report(sb)
            return

    sb.require_valid()
    path = sb.save()
    report(sb)
    print(f"\nwrote {path}")


if __name__ == "__main__":
    main()

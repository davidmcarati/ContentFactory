"""Step 5: assemble everything needed to actually upload the video.

Produces one self-contained folder per video under DELIVERY_ROOT:

    D:/TheArtOfChaosVideos/<slug>/
        <slug>.mp4          the film
        thumbnail.png       1280x720, generated separately from the film
        description.txt     title, description, chapters, tags, credits
        subtitles.srt       for upload; the burned-in ones are already baked
        credits.md          only when real images were used
        storyboard.json     provenance: exactly what produced this file

The thumbnail is generated from its own prompt rather than cropped out of a
frame. A shot is composed to be panned across at 1920 px wide; a thumbnail has
to read as a 200 px tile in a sidebar, and those are different pictures.
"""
from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from . import config, workflows
from .comfy_client import ComfyClient
from .outro import load_font
from .schema import Storyboard


# ---------------------------------------------------------------------------
# Thumbnail
# ---------------------------------------------------------------------------
def _fit_font(draw: ImageDraw.ImageDraw, text: str, width: int, start: int,
              max_height: int) -> tuple[ImageFont.FreeTypeFont, list[str]]:
    """Largest size at which the text fits the width and the height budget.

    Height matters as much as width: at 150 px the first attempt at "Not
    sadness. Not serotonin." fit happily across three lines and swallowed the
    entire thumbnail, leaving no picture behind it.
    """
    for size in range(start, 40, -6):
        font = _load(size)
        lines = _balanced_wrap(draw, text, font, width, max_lines=3)
        if not lines:
            continue
        block = len(lines) * size + int(size * 0.22) * (len(lines) - 1)
        if block <= max_height:
            return font, lines
    font = _load(44)
    return font, [text]


def _balanced_wrap(draw: ImageDraw.ImageDraw, text: str,
                   font: ImageFont.FreeTypeFont, width: int,
                   max_lines: int) -> list[str] | None:
    """Wrap into as few lines as possible, splitting as evenly as possible.

    A greedy wrap fills each line to the brim and strands the remainder, which
    is how "NOT SADNESS. / NOT / SEROTONIN." happened. Choosing the split that
    minimises the widest line keeps the block looking deliberate.
    """
    words = text.split()
    if not words:
        return None

    def fits(chunks: list[list[str]]) -> bool:
        return all(draw.textlength(" ".join(c), font=font) <= width
                   for c in chunks)

    def widest(chunks: list[list[str]]) -> float:
        return max(draw.textlength(" ".join(c), font=font) for c in chunks)

    for lines in range(1, max_lines + 1):
        if lines > len(words):
            break
        best = None
        # Enumerate every way to cut the word list into `lines` runs. Thumbnail
        # text is a handful of words, so this stays trivial.
        for cuts in _combinations(range(1, len(words)), lines - 1):
            bounds = [0, *cuts, len(words)]
            chunks = [words[bounds[i]:bounds[i + 1]]
                      for i in range(len(bounds) - 1)]
            if not fits(chunks):
                continue
            if best is None or widest(chunks) < widest(best):
                best = chunks
        if best:
            return [" ".join(c) for c in best]
    return None


def _combinations(seq, r):
    from itertools import combinations
    return combinations(seq, r)


def _load(size: int) -> ImageFont.FreeTypeFont:
    original = config.OUTRO_FONT
    config.OUTRO_FONT = config.THUMB_FONT
    try:
        return load_font(size)
    finally:
        config.OUTRO_FONT = original


def _wrap_to(draw: ImageDraw.ImageDraw, text: str,
             font: ImageFont.FreeTypeFont, width: int,
             max_lines: int) -> list[str] | None:
    words, lines, cur = text.split(), [], ""
    for word in words:
        trial = f"{cur} {word}".strip()
        if cur and draw.textlength(trial, font=font) > width:
            lines.append(cur)
            cur = word
            if len(lines) >= max_lines:
                return None
        else:
            cur = trial
    if cur:
        lines.append(cur)
    return lines if len(lines) <= max_lines else None


def build_thumbnail(sb: Storyboard, *, force: bool = False) -> Path:
    dest = sb.dir / "thumbnail.png"
    if dest.exists() and not force:
        return dest

    prompt = sb.publish.thumbnail_prompt
    base = sb.dir / "thumbnail_bg.png"

    if prompt:
        client = ComfyClient()
        client.require_up()
        graph = workflows.build(
            sb.style.model,
            prompt=f"{sb.style.base_prompt}, {prompt}",
            negative=sb.style.negative,
            seed=sb.style.seed_base + 9001,
            # One step above the tile, and profile-driven: a Short's tile is
            # vertical, so a 16:9 thumbnail is the wrong picture rather than
            # merely the wrong size.
            width=config.THUMB_COMPOSE_W, height=config.THUMB_COMPOSE_H,
            filename_prefix=f"{sb.slug}_thumb",
        )
        client.render(graph, base)
        # One picture is not worth leaving a 12 GB model resident afterwards,
        # and this is the last thing in the pipeline that needs the card.
        client.free()
    else:
        # No prompt written: fall back to the opening frame rather than fail.
        source = sb.shots[0].frame_path
        if not source:
            raise ValueError("no thumbnail_prompt and no frames to fall back on")
        base = Path(source)

    img = Image.open(base).convert("RGB")
    scale = max(config.THUMB_W / img.width, config.THUMB_H / img.height)
    img = img.resize((round(img.width * scale), round(img.height * scale)),
                     Image.LANCZOS)
    left, top = (img.width - config.THUMB_W) // 2, (img.height - config.THUMB_H) // 2
    img = img.crop((left, top, left + config.THUMB_W, top + config.THUMB_H))

    text = sb.publish.thumbnail_text.strip()
    if text:
        draw = ImageDraw.Draw(img)
        margin = 64
        # Leave the top half of the tile to the picture; text lives below it.
        font, lines = _fit_font(draw, text.upper(), config.THUMB_W - margin * 2,
                                config.THUMB_MAX_TEXT_SIZE,
                                max_height=int(config.THUMB_H * 0.46))

        heights = [draw.textbbox((0, 0), ln, font=font)[3] -
                   draw.textbbox((0, 0), ln, font=font)[1] for ln in lines]
        gap = int(font.size * 0.22)
        block = sum(heights) + gap * (len(lines) - 1)
        y = config.THUMB_H - margin - block

        # Darken behind the text. A thumbnail is judged at 200 px wide, where a
        # thin outline disappears and white text turns to mush against a bright
        # sky. The scrim is a gradient, not a band: a flat band leaves a hard
        # horizontal seam straight across the picture.
        band_top = max(y - int(font.size * 0.9), 0)
        mask = Image.new("L", img.size, 0)
        mask_draw = ImageDraw.Draw(mask)
        span = max(config.THUMB_H - band_top, 1)
        for line_y in range(band_top, config.THUMB_H):
            progress = (line_y - band_top) / span
            mask_draw.line([(0, line_y), (config.THUMB_W, line_y)],
                           fill=int(165 * progress ** 0.7))
        img = Image.composite(Image.new("RGB", img.size, (0, 0, 0)), img, mask)

        draw = ImageDraw.Draw(img)
        for line, height in zip(lines, heights):
            box = draw.textbbox((0, 0), line, font=font)
            x = (config.THUMB_W - (box[2] - box[0])) // 2 - box[0]
            draw.text((x, y - box[1]), line, font=font,
                      fill=(255, 255, 255),
                      stroke_width=max(font.size // 14, 4),
                      stroke_fill=(0, 0, 0))
            y += height + gap

    img.save(dest)
    return dest


# ---------------------------------------------------------------------------
# Description
# ---------------------------------------------------------------------------
def _timestamp(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def build_description(sb: Storyboard) -> str:
    parts = [sb.title, ""]

    if sb.publish.description:
        parts += [sb.publish.description.strip(), ""]

    if sb.publish.chapters:
        parts.append("Chapters")
        # YouTube only accepts a chapter list if the first one starts at 0:00.
        first = sb.publish.chapters[0]
        if sb.start_of(first.shot_id) > 0.5:
            parts.append("0:00 Intro")
        for chapter in sb.publish.chapters:
            parts.append(f"{_timestamp(sb.start_of(chapter.shot_id))} "
                         f"{chapter.title}")
        parts.append("")

    used = [s.asset for s in sb.shots if s.asset]
    if used:
        parts.append("Image credits")
        for asset in used:
            parts.append(f"- {asset.credit_line()}")
        parts.append("")

    # Platforms increasingly require this to be declared, and declaring it is
    # cheaper than having the channel decide for you what it thinks you did.
    parts.append("This video uses AI-generated imagery and a synthetic "
                 "narration voice.")
    if used:
        parts.append("Historical artworks and photographs are reproduced from "
                     "public-domain and openly licensed collections; see the "
                     "credits above.")
    parts.append("")

    if sb.publish.tags:
        parts += ["Tags", ", ".join(sb.publish.tags), ""]

    return "\n".join(parts).rstrip() + "\n"


# ---------------------------------------------------------------------------
def require_current(sb: Storyboard, video: Path) -> None:
    """Refuse to ship a cut that is older than the frames it was made from.

    The review gate guards the way into assembly: it will not let step 4 run
    on frames nobody has looked at. Nothing guarded the other direction, and
    the gap is not theoretical. The chronicles video shipped with shot 9 as
    it was before it was fixed -- the frame was re-rendered eight minutes
    after the cut was built, the review was then accepted, and the video was
    never rebuilt. Everything looked green.

    Comparing mtimes is enough, costs nothing, and catches the whole class.
    """
    cut = video.stat().st_mtime
    newer = sorted(
        s.id for s in sb.shots
        if (p := sb.dir / "frames" / f"{s.stem}.png").exists()
        and p.stat().st_mtime > cut
    )
    if not newer:
        return
    shown = ", ".join(f"{i:03d}" for i in newer[:8])
    raise SystemExit(
        f"{sb.slug}: {len(newer)} frame(s) are newer than {video.name} "
        f"({shown}{'...' if len(newer) > 8 else ''}).\n"
        f"The cut does not contain them. Rebuild before publishing:\n"
        f"  python -m pipeline.run {sb.slug} --from assemble --force"
    )


def publish(sb: Storyboard, *, force: bool = False,
            root: Path | None = None) -> Path:
    video = sb.dir / f"{sb.slug}.mp4"
    if not video.exists():
        raise FileNotFoundError(
            f"{video} does not exist; run step 4 before publishing"
        )
    require_current(sb, video)

    # Not DELIVERY_ROOT / slug: the folder may already exist under a numbered
    # name, and the sources live inside it.
    out = (root / sb.slug) if root else config.video_dir(sb.slug)
    out.mkdir(parents=True, exist_ok=True)

    thumbnail = build_thumbnail(sb, force=force)
    (out / "description.txt").write_text(build_description(sb), encoding="utf-8")

    copied = ["description.txt"]
    for src, name in (
        (video, f"{sb.slug}.mp4"),
        (thumbnail, "thumbnail.png"),
        (sb.dir / "subtitles.srt", "subtitles.srt"),
        (sb.dir / "credits.md", "credits.md"),
        (sb.dir / "storyboard.json", "storyboard.json"),
    ):
        if src.exists():
            shutil.copy2(src, out / name)
            copied.append(name)

    missing = [w for w in ("thumbnail.png", "subtitles.srt") if w not in copied]

    print(f"\npublished to {out}")
    for name in copied:
        size = (out / name).stat().st_size
        print(f"  {name:20s} {size / 1024**2:8.2f} MB")
    if missing:
        print(f"  note: {', '.join(missing)} not produced")
    if not sb.publish.description:
        print("  note: description is empty; write storyboard.publish.description")
    if not sb.publish.thumbnail_text:
        print("  note: thumbnail has no text overlay")
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Package a video for upload.")
    ap.add_argument("slug")
    ap.add_argument("--force", action="store_true",
                    help="regenerate the thumbnail even if one exists")
    ap.add_argument("--root", help="override the delivery root")
    a = ap.parse_args()

    sb = Storyboard.load(a.slug)
    sb.require_valid()
    publish(sb, force=a.force, root=Path(a.root) if a.root else None)


if __name__ == "__main__":
    main()

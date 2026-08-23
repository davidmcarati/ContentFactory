"""The end card.

A video that stops the moment the narration does feels like the file was
truncated. The outro gives the viewer somewhere to land and somewhere to click.

It is appended *after* the last shot rather than being a shot of its own,
because it has no narration. Making it a shot would put a silent entry into the
timeline that every duration calculation in the pipeline would then have to
special-case.

The background is the final frame of the video, blurred and pushed well down
toward black, so the card grows out of the film instead of cutting to an
unrelated panel. Text is drawn with Pillow: ffmpeg's `drawtext` segfaults in
this build for want of a fontconfig default.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from . import config, kenburns
from .schema import Motion, Storyboard

FONT_DIRS = (
    config.ASSETS / "fonts",
    Path("C:/Windows/Fonts"),
)


def load_font(size: int) -> ImageFont.FreeTypeFont:
    """First font that exists, biggest hammer last.

    Falling back to Pillow's bitmap default would silently produce a 10 px
    caption on a 1080p card, which is worse than failing.
    """
    for name in (config.OUTRO_FONT, config.OUTRO_FONT_FALLBACK):
        for directory in FONT_DIRS:
            candidate = directory / name
            if candidate.exists():
                return ImageFont.truetype(str(candidate), size)
    raise FileNotFoundError(
        f"no outro font found. Looked for {config.OUTRO_FONT} and "
        f"{config.OUTRO_FONT_FALLBACK} in {[str(d) for d in FONT_DIRS]}. "
        f"Drop a .ttf into assets/fonts and set OUTRO_FONT."
    )


def build_background(source: Path, dest: Path,
                     width: int = config.OUT_W,
                     height: int = config.OUT_H) -> Path:
    """Blur and darken the last frame into a backdrop for the card."""
    img = Image.open(source).convert("RGB")

    scale = max(width / img.width, height / img.height)
    img = img.resize((max(round(img.width * scale), width),
                      max(round(img.height * scale), height)), Image.LANCZOS)
    left, top = (img.width - width) // 2, (img.height - height) // 2
    img = img.crop((left, top, left + width, top + height))

    img = img.filter(ImageFilter.GaussianBlur(radius=width // 45))
    img = Image.blend(img, Image.new("RGB", img.size, (0, 0, 0)),
                      config.OUTRO_SCRIM)

    dest.parent.mkdir(parents=True, exist_ok=True)
    img.save(dest)
    return dest


def build_overlay(text: str | None = None, subtext: str | None = None,
                  width: int = config.OUT_W,
                  height: int = config.OUT_H) -> Image.Image:
    """The text layer: headline, a hairline rule, and an optional second line."""
    text = config.OUTRO_TEXT if text is None else text
    subtext = config.OUTRO_SUBTEXT if subtext is None else subtext

    layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)

    head_font = load_font(config.OUTRO_TEXT_SIZE)
    head_box = draw.textbbox((0, 0), text, font=head_font)
    head_w, head_h = head_box[2] - head_box[0], head_box[3] - head_box[1]

    sub_font = sub_w = sub_h = None
    if subtext:
        sub_font = load_font(config.OUTRO_SUBTEXT_SIZE)
        sub_box = draw.textbbox((0, 0), subtext, font=sub_font)
        sub_w, sub_h = sub_box[2] - sub_box[0], sub_box[3] - sub_box[1]

    rule_gap = config.OUTRO_TEXT_SIZE // 2
    block_h = head_h + (rule_gap + sub_h if subtext else 0)
    top = (height - block_h) // 2

    head_x = (width - head_w) // 2 - head_box[0]
    head_y = top - head_box[1]
    # A soft dark halo, so the headline holds up even if the backdrop is pale.
    for dx, dy in ((-2, 0), (2, 0), (0, -2), (0, 2)):
        draw.text((head_x + dx, head_y + dy), text, font=head_font,
                  fill=(0, 0, 0, 120))
    draw.text((head_x, head_y), text, font=head_font, fill=(255, 255, 255, 255))

    if subtext:
        rule_y = top + head_h + rule_gap // 2
        rule_half = min(head_w, width // 3) // 2
        draw.line([(width // 2 - rule_half, rule_y),
                   (width // 2 + rule_half, rule_y)],
                  fill=(255, 255, 255, 90), width=2)
        sub_x = (width - sub_w) // 2 - sub_box[0]
        sub_y = top + head_h + rule_gap - sub_box[1]
        draw.text((sub_x, sub_y), subtext, font=sub_font,
                  fill=(235, 235, 235, 220))

    return layer


def render(sb: Storyboard, *, force: bool = False) -> Path | None:
    """Render the end card clip. Returns None when outros are switched off."""
    if not config.OUTRO_ENABLED or config.OUTRO_SECONDS <= 0:
        return None

    last_frame = sb.shots[-1].frame_path
    if not last_frame:
        raise ValueError("cannot build an outro before the frames exist")

    # Always re-rendered, never cached. It costs a few seconds against a
    # half-hour pipeline, and caching it means editing OUTRO_TEXT silently does
    # nothing until someone thinks to pass --force.
    clip = sb.dir / "clips" / "outro.mp4"
    background = build_background(Path(last_frame), sb.dir / "frames" / "outro.png")
    frames = max(round(config.OUTRO_SECONDS * config.FPS), 1)
    kenburns.render(
        background, clip,
        Motion(pan="in", zoom=1.06),   # barely moving; enough to feel alive
        frames,
        overlay=build_overlay(),
    )
    return clip

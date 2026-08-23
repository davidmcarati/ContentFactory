"""Subtitle generation, in two formats for two different jobs.

`.srt` is the upload artifact -- YouTube and every other platform wants it, and
it carries no styling.

`.ass` is what actually gets burned into the picture. It exists because libass
assumes a 288-line canvas when it reads an SRT and scales the font up by
1080/288, so a nominal size 22 lands on screen at 82 px. An ASS file declaring
PlayResX/PlayResY means the sizes and margins here are the pixels you get.
"""
from __future__ import annotations

import re
from pathlib import Path

from . import config
from .schema import Storyboard

_SENTENCE = re.compile(r"(?<=[.!?])\s+")
_CLAUSE = re.compile(r"(?<=[,;:])\s+")


def _srt_time(t: float) -> str:
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _ass_time(t: float) -> str:
    cs = int(round(t * 100))
    h, cs = divmod(cs, 360_000)
    m, cs = divmod(cs, 6_000)
    s, cs = divmod(cs, 100)
    return f"{h:d}:{m:02d}:{s:02d}.{cs:02d}"


def wrap(text: str, width: int = config.SUB_MAX_CHARS) -> list[str]:
    """Break a line into at most two roughly equal lines.

    Balancing matters more than it sounds: a greedy wrap leaves a long line
    over a two-word orphan, which draws the eye far more than the text itself.
    """
    words = text.split()
    if not words:
        return []
    if len(text) <= width:
        return [text]

    # Try every split point, keep the one with the most even halves that still
    # fits, so neither line runs past the margin.
    best, best_cost = None, None
    for i in range(1, len(words)):
        a, b = " ".join(words[:i]), " ".join(words[i:])
        if len(a) > width or len(b) > width:
            continue
        cost = abs(len(a) - len(b))
        if best_cost is None or cost < best_cost:
            best, best_cost = (a, b), cost
    if best:
        return list(best)

    # Does not fit in two lines; fall back to greedy and accept the overflow.
    lines, cur = [], ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > width:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    return lines


def _pack(parts: list[str], limit: int) -> list[str]:
    """Greedily join parts, starting a new group before exceeding the limit."""
    out: list[str] = []
    cur = ""
    for part in parts:
        trial = f"{cur} {part}".strip()
        if cur and len(trial) > limit:
            out.append(cur)
            cur = part
        else:
            cur = trial
    if cur:
        out.append(cur)
    return out


def split_cues(text: str, width: int = config.SUB_MAX_CHARS) -> list[str]:
    """Break a narration line into pieces that each fit two subtitle lines.

    Breaks are chosen at the strongest boundary available -- sentence first,
    then clause, and only as a last resort between arbitrary words. Counting
    characters alone produced cards ending "...for two thousand" with "years."
    stranded on the next one, which reads as a fault even when the timing is
    perfect.
    """
    limit = 2 * width
    pieces: list[str] = []

    for sentence in _SENTENCE.split(text.strip()):
        sentence = sentence.strip()
        if not sentence:
            continue
        if len(sentence) <= limit:
            pieces.append(sentence)
            continue
        for clause in _pack(_CLAUSE.split(sentence), limit):
            if len(clause) <= limit:
                pieces.append(clause)
            else:
                pieces.extend(_pack(clause.split(), limit))

    # Rejoin neighbours that still fit together, so two short sentences share a
    # card instead of flashing past one at a time.
    return _pack(pieces, limit) or [text]


def cues(sb: Storyboard) -> list[tuple[float, float, str]]:
    """Timed cues, at most two lines each, keyed to the narration.

    A cue ends when its share of the voice ends, not when the picture changes,
    so the tail pause between shots stays clean. Time is split between the
    pieces of a long line in proportion to their length -- an approximation,
    but one that drifts by well under the length of a cue. Word-accurate
    timing needs forced alignment.
    """
    out: list[tuple[float, float, str]] = []
    t = 0.0
    for shot in sb.shots:
        pieces = split_cues(shot.vo)
        total = sum(len(p) for p in pieces) or 1
        start = t
        for piece in pieces:
            span = shot.audio_sec * len(piece) / total
            out.append((start, start + span, piece))
            start += span
        t += shot.duration
    return out


def build_srt(sb: Storyboard) -> Path:
    path = sb.dir / "subtitles.srt"
    blocks = [
        f"{i}\n{_srt_time(a)} --> {_srt_time(b)}\n" + "\n".join(wrap(text)) + "\n"
        for i, (a, b, text) in enumerate(cues(sb), start=1)
    ]
    path.write_text("\n".join(blocks), encoding="utf-8")
    return path


def build_ass(sb: Storyboard) -> Path:
    path = sb.dir / "subtitles.ass"
    # ASS colours are &HAABBGGRR -- alpha first, then blue, green, red.
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {config.OUT_W}
PlayResY: {config.OUT_H}
WrapStyle: 0
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{config.SUB_FONT},{config.SUB_FONT_SIZE},&H00FFFFFF,&H000000FF,&H00000000,&HA0000000,0,0,0,0,100,100,0,0,1,{config.SUB_OUTLINE},{config.SUB_SHADOW},2,{config.SUB_MARGIN_H},{config.SUB_MARGIN_H},{config.SUB_MARGIN_V},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = [
        f"Dialogue: 0,{_ass_time(a)},{_ass_time(b)},Default,,0,0,0,,"
        + r"\N".join(wrap(text))
        for a, b, text in cues(sb)
    ]
    path.write_text(header + "\n".join(lines) + "\n", encoding="utf-8")
    return path

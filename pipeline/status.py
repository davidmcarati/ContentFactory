"""Where is everything, right now.

    python -m pipeline.status              every project, one line each
    python -m pipeline.status gold         one project, in detail
    python -m pipeline.status --watch      redraw until interrupted

Deliberately stores nothing. A status file would be one more thing that can
disagree with reality, and this pipeline has already been bitten once by
trusting a recorded path over the disk. Every number here is counted from
what is actually on disk at the moment you ask, so it cannot go stale and
cannot lie about a run that died.

A render in progress shows up on its own: the frame count climbs and the
newest frame is a few seconds old, which is what the LIVE marker means.
"""
from __future__ import annotations

import argparse
import time
from dataclasses import dataclass
from pathlib import Path

from . import config
from .schema import Storyboard

# A frame written in the last two minutes means something is still working.
LIVE_WINDOW = 120.0
BAR_W = 12


@dataclass
class State:
    slug: str
    folder: str
    title: str
    shots: int
    model: str
    todo: int              # prompts still saying TODO
    voiced: int
    framed: int
    newest_frame: float    # epoch, 0 if none
    review: str
    redone: int | None     # frames changed since the review was accepted
    runtime: float         # seconds of finished video, 0 if none
    published: list[str]

    @property
    def live(self) -> bool:
        return bool(self.newest_frame
                    and time.time() - self.newest_frame < LIVE_WINDOW)

    @property
    def stage(self) -> str:
        if self.todo:
            return "script"
        if self.voiced < self.shots:
            return "voice"
        if self.framed < self.shots:
            return "frames"
        if self.review != "ok":
            return "review"
        if not self.runtime:
            return "assemble"
        if len(self.published) < 4:
            return "publish"
        return "done"


def bar(done: int, total: int, width: int = BAR_W) -> str:
    if not total:
        return " " * width
    # Clamped: the frames directory can legitimately hold more files than
    # there are shots, and an overflowing bar breaks the column alignment for
    # every row under it.
    filled = max(0, min(width, round(width * done / total)))
    return "#" * filled + "." * (width - filled)


def read(slug: str) -> State | None:
    project = config.project_dir(slug)
    if not (project / "storyboard.json").exists():
        return None
    sb = Storyboard.load(slug)

    # Only files that are actually a shot's frame. `outro.png`, the end card,
    # lives in the same directory and was making every project report one
    # frame more than it has shots.
    wanted = {f"{s.stem}.png" for s in sb.shots}
    frames = [p for p in (project / "frames").glob("*.png") if p.name in wanted]
    newest = max((f.stat().st_mtime for f in frames), default=0.0)

    from .review import stale
    changed = stale(sb)
    if not changed:
        review, redone = "ok", 0
    elif changed == ["never"]:
        review, redone = "not looked at", None
    else:
        review, redone = f"{len(changed)} changed", len(changed)

    video = project / f"{sb.slug}.mp4"
    runtime = 0.0
    if video.exists():
        from .ffmpeg_util import duration
        try:
            runtime = duration(video)
        except Exception:
            runtime = 0.0

    delivery = config.video_dir(slug)
    published = [n for n in (f"{sb.slug}.mp4", "thumbnail.png",
                             "description.txt", "subtitles.srt",
                             "storyboard.json")
                 if (delivery / n).exists()]

    return State(
        slug=sb.slug,
        folder=delivery.name,
        title=sb.title,
        shots=len(sb.shots),
        model=sb.style.model,
        todo=sum(1 for s in sb.shots if s.image_prompt.startswith("TODO")),
        voiced=sum(1 for s in sb.shots if s.audio_sec),
        framed=len(frames),
        newest_frame=newest,
        review=review,
        redone=redone,
        runtime=runtime,
        published=published,
    )


def known_slugs() -> list[str]:
    """Every project under the delivery root, in release order.

    Folders carry a numeric prefix once a video is scheduled; ones that do
    not sort after, which is the order they were made in anyway.
    """
    root = config.DELIVERY_ROOT
    if not root.is_dir():
        return []
    found = []
    for d in root.iterdir():
        if not (d / config.SOURCES_NAME / "storyboard.json").exists():
            continue
        head, _, rest = d.name.partition("_")
        order = int(head) if head.isdigit() else 999
        found.append((order, d.name, rest or d.name))
    return [slug for _, _, slug in sorted(found)]


def render_table(states: list[State]) -> str:
    lines = [
        f"{'folder':16s} {'shots':>5s}  {'voice':>7s}  "
        f"{'frames':{BAR_W}s} {'':>7s}  {'model':12s} {'review':13s} "
        f"{'cut':>6s}  {'stage':8s}"
    ]
    lines.append("-" * len(lines[0]))
    for s in states:
        cut = f"{s.runtime / 60:5.1f}m" if s.runtime else "     -"
        mark = "  LIVE" if s.live else ""
        lines.append(
            f"{s.folder:16s} {s.shots:5d}  {s.voiced:3d}/{s.shots:<3d}  "
            f"{bar(s.framed, s.shots)} {s.framed:3d}/{s.shots:<3d}  "
            f"{s.model:12s} {s.review:13s} {cut}  {s.stage:8s}{mark}"
        )
    return "\n".join(lines)


def live_line(s: State) -> str:
    """What a render in flight is actually doing.

    Counting files does not work for a re-render: step 3 overwrites frames in
    place, so all 91 exist from the first second and the count never moves.
    What does move is the number of frames that differ from the accepted
    review, which is exactly how many have been redone so far.
    """
    if s.redone is not None and s.framed == s.shots:
        return (f"{s.folder}: {bar(s.redone, s.shots, 24)} "
                f"{s.redone}/{s.shots} re-rendered, {s.shots - s.redone} to go")
    return (f"{s.folder}: {bar(s.framed, s.shots, 24)} "
            f"{s.framed}/{s.shots} frames, {s.shots - s.framed} to go")


def render_detail(s: State) -> str:
    out = [f"{s.folder}   {s.title}", ""]
    steps = [
        ("1 script", s.shots - s.todo, s.shots),
        ("2 voice", s.voiced, s.shots),
        ("3 frames", s.framed, s.shots),
    ]
    for label, done, total in steps:
        out.append(f"  {label:10s} {bar(done, total, 28)} {done:3d}/{total}")
    out.append(f"  3b review  {s.review}")
    out.append(f"  4 cut      {f'{s.runtime / 60:.1f} min' if s.runtime else '-'}")
    out.append(f"  5 publish  {', '.join(s.published) or '-'}")
    out.append("")
    out.append(f"  model {s.model}, stage {s.stage}"
               + ("  -- rendering now" if s.live else ""))
    if s.live:
        age = time.time() - s.newest_frame
        out.append(f"  {live_line(s)}, newest {age:.0f}s ago")
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser(description="Pipeline state, counted from disk.")
    ap.add_argument("slug", nargs="?", help="one project, in detail")
    ap.add_argument("--watch", action="store_true", help="redraw every 10s")
    ap.add_argument("--every", type=float, default=10.0)
    a = ap.parse_args()

    while True:
        if a.slug:
            s = read(a.slug)
            body = render_detail(s) if s else f"no project {a.slug!r}"
        else:
            states = [s for s in (read(x) for x in known_slugs()) if s]
            body = render_table(states)
            live = [s for s in states if s.live]
            if live:
                body += "\n\n" + "\n".join(live_line(s) for s in live)
        if a.watch:
            print("\033[2J\033[H" + time.strftime("%H:%M:%S") + "\n")
        print(body, flush=True)
        if not a.watch:
            return
        time.sleep(a.every)


if __name__ == "__main__":
    main()

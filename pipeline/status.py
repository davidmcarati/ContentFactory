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
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from . import config
from .schema import Storyboard

# A frame written in the last two minutes means something is still working.
LIVE_WINDOW = 120.0
# How long one computed page is reused across overlapping requests.
CACHE_SEC = 3.0
# A gap longer than this between two frames means they belong to different
# runs. Qwen takes about 95 s a frame and schnell about 9, so ten minutes is
# far outside either while still splitting yesterday's batch from today's.
RUN_GAP = 600.0
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
    cut_stale: bool        # the mp4 is older than the frames under it
    published: list[str]
    run_count: int         # frames written in the current unbroken run
    run_span: float        # seconds from the first of that run to the last
    voiced_files: int      # audio/NNN.wav actually on disk
    narration: bool        # the single mixed narration track
    clipped: int           # clips/NNN.mp4, the Ken Burns pass
    subs: bool             # subtitles.srt and .ass both written

    @property
    def steps(self) -> list[tuple[str, int, int]]:
        """Every stage that actually leaves something on disk.

        Upscaling is deliberately absent. It is not a step: the frame
        graph composes at COMPOSE_W and resamples to GEN_W inside the
        same ComfyUI job, so there is no separate pass and nothing to
        count. A bar for it would be decoration pretending to be a
        measurement.
        """
        return [
            ("script", self.shots - self.todo, self.shots),
            ("voice", self.voiced_files, self.shots),
            ("mix", int(self.narration), 1),
            ("frames", self.framed, self.shots),
            ("review", int(self.review == "ok"), 1),
            ("clips", self.clipped, self.shots),
            ("subs", int(self.subs), 1),
            ("cut", int(bool(self.runtime and not self.cut_stale)), 1),
            ("publish", len(self.published), 5),
        ]

    @property
    def live(self) -> bool:
        """Is something still writing frames here?

        The window has to scale with the model, not sit at a constant. A
        fixed two minutes was calibrated on schnell at 9 s a frame; Qwen
        takes 88 to 141, so a project mid-render looked dead for most of the
        gap between one frame and the next. Three times the measured rate
        covers an ordinary slow frame without keeping a finished run marked
        live for long.
        """
        if not self.newest_frame:
            return False
        window = max(LIVE_WINDOW, 3 * self.rate)
        return time.time() - self.newest_frame < window

    @property
    def rate(self) -> float:
        """Measured seconds per frame for the run in progress.

        Taken from the frames themselves rather than from a counter, because
        a counter would have to be stored and could then disagree with the
        disk. Needs two frames to have a gap to measure.
        """
        if self.run_count < 2 or self.run_span <= 0:
            return 0.0
        return self.run_span / (self.run_count - 1)

    @property
    def left(self) -> int:
        """Frames still to do.

        For a re-render this is not "files missing" -- step 3 overwrites in
        place, so every file exists from the first second. What is missing is
        the frames that still match the accepted review, i.e. the ones not
        yet replaced.
        """
        if self.redone is not None and self.framed == self.shots:
            return max(self.shots - self.redone, 0)
        return max(self.shots - self.framed, 0)

    @property
    def eta(self) -> float:
        rate = self.rate
        return self.left * rate if rate and self.live else 0.0

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
        # An mp4 on disk is not the same claim as "this video is finished".
        # Re-rendering gold left last night's cut sitting beside frames that
        # had all been replaced, and the page called it done.
        if not self.runtime or self.cut_stale:
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


_DURATIONS: dict[tuple[str, int, int], float] = {}


def cached_duration(video: Path) -> float:
    """ffprobe once per finished file, not once per page load.

    Fourteen probes on every request took long enough that the browser gave
    up mid-response. A finished video's length only changes when the file
    does, so the key is its size and mtime.
    """
    if not video.exists():
        return 0.0
    stat = video.stat()
    key = (str(video), stat.st_size, int(stat.st_mtime))
    if key not in _DURATIONS:
        from .ffmpeg_util import duration
        try:
            _DURATIONS[key] = duration(video)
        except Exception:
            _DURATIONS[key] = 0.0
    return _DURATIONS[key]


def current_run(stamps: list[float]) -> tuple[int, float]:
    """How many frames the run in progress has written, and over how long.

    Walks back from the newest frame while consecutive frames are close
    together, and stops at the first real gap. That separates this render
    from whatever wrote the same directory yesterday without needing either
    of them to have recorded anything.
    """
    if len(stamps) < 2:
        return len(stamps), 0.0
    start = len(stamps) - 1
    while start > 0 and stamps[start] - stamps[start - 1] <= RUN_GAP:
        start -= 1
    return len(stamps) - start, stamps[-1] - stamps[start]


def human(seconds: float) -> str:
    if seconds <= 0:
        return ""
    m, s = divmod(int(seconds + 0.5), 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}h {m:02d}m"
    return f"{m}m {s:02d}s" if m else f"{s}s"


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
    stamps = sorted(f.stat().st_mtime for f in frames)
    newest = stamps[-1] if stamps else 0.0
    run_count, run_span = current_run(stamps)

    wavs = len([p for p in (project / "audio").glob("*.wav")
                if p.stem.isdigit()])
    narration = (project / "audio" / "narration.wav").exists()
    clipped = len([p for p in (project / "clips").glob("*.mp4")
                   if p.stem.isdigit()])
    subs = ((project / "subtitles.srt").exists()
            and (project / "subtitles.ass").exists())

    from .review import stale
    changed = stale(sb)
    if not changed:
        review, redone = "ok", 0
    elif changed == ["never"]:
        review, redone = "not looked at", None
    else:
        review, redone = f"{len(changed)} changed", len(changed)

    video = project / f"{sb.slug}.mp4"
    runtime = cached_duration(video)
    cut_stale = bool(runtime and newest
                     and video.stat().st_mtime < newest)

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
        cut_stale=cut_stale,
        published=published,
        run_count=run_count,
        run_span=run_span,
        voiced_files=wavs,
        narration=narration,
        clipped=clipped,
        subs=subs,
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
        f"{'frames':{BAR_W}s} {'':>7s}  {'left':>9s}  {'model':12s} "
        f"{'review':13s} {'cut':>6s}  {'stage':8s}"
    ]
    lines.append("-" * len(lines[0]))
    for s in states:
        cut = f"{s.runtime / 60:5.1f}m" if s.runtime else "     -"
        if s.cut_stale:
            cut = "  stale"
        mark = "  LIVE" if s.live else ""
        lines.append(
            f"{s.folder:16s} {s.shots:5d}  {s.voiced:3d}/{s.shots:<3d}  "
            f"{bar(s.framed, s.shots)} {s.framed:3d}/{s.shots:<3d}  "
            f"{human(s.eta) or '-':>9s}  "
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
    done = s.redone if (s.redone is not None and s.framed == s.shots) else s.framed
    word = "re-rendered" if done is s.redone else "frames"
    tail = ""
    if s.rate:
        tail = f", {s.rate:.0f}s each"
        if s.eta:
            tail += f", about {human(s.eta)} left"
    return (f"{s.folder}: {bar(done, s.shots, 24)} "
            f"{done}/{s.shots} {word}, {s.left} to go{tail}")


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
    cut = "-" if not s.runtime else (
        f"{s.runtime / 60:.1f} min"
        + ("  (older than the frames -- needs reassembly)" if s.cut_stale else ""))
    out.append(f"  4 cut      {cut}")
    out.append(f"  5 publish  {', '.join(s.published) or '-'}")
    out.append("")
    out.append(f"  model {s.model}, stage {s.stage}"
               + ("  -- rendering now" if s.live else ""))
    if s.live:
        age = time.time() - s.newest_frame
        out.append(f"  {live_line(s)}, newest {age:.0f}s ago")
    return "\n".join(out)


PAGE = """<!doctype html><html><head><meta charset="utf-8">
<meta http-equiv="refresh" content="{every}">
<title>Content Factory</title><style>
:root {{ color-scheme: dark; }}
body {{ background:#101014; color:#e8e8ee; margin:0; padding:28px 32px;
  font:15px/1.5 "Segoe UI",system-ui,sans-serif; }}
h1 {{ font-size:17px; font-weight:600; margin:0 0 4px; letter-spacing:.02em; }}
.sub {{ color:#7a7a88; font-size:13px; margin-bottom:22px; }}
table {{ border-collapse:collapse; width:100%; max-width:1180px; }}
th {{ text-align:left; font-weight:500; color:#7a7a88; font-size:12px;
  text-transform:uppercase; letter-spacing:.06em; padding:0 14px 8px 0; }}
td {{ padding:9px 14px 9px 0; border-top:1px solid #22222c; vertical-align:middle; }}
.folder {{ font-weight:600; }}
.title {{ color:#8a8a99; font-size:12.5px; }}
.track {{ background:#22222c; border-radius:3px; height:9px; width:190px;
  overflow:hidden; display:inline-block; vertical-align:middle; }}
.fill {{ background:#5b8def; height:100%; display:block; border-radius:3px; }}
.fill.live {{ background:#e0a33e; }}
.fill.done {{ background:#3f9d63; }}
.num {{ color:#9a9aa8; font-size:12.5px; margin-left:10px;
  font-variant-numeric:tabular-nums; }}
.tag {{ font-size:11.5px; padding:2px 8px; border-radius:11px;
  background:#22222c; color:#9a9aa8; }}
.tag.live {{ background:#4a3512; color:#f0b955; }}
.tag.done {{ background:#16301f; color:#5cbc82; }}
.tag.wait {{ background:#2e2418; color:#d09a4e; }}
.model {{ font-size:12px; color:#8a8a99; }}
.eta {{ padding-left:18px; font-size:12.5px; color:#e8e8ee; font-variant-numeric:tabular-nums;
  white-space:nowrap; }}
.steps {{ display:flex; gap:7px; }}
.step {{ flex:1 1 0; min-width:0; overflow:hidden; }}
.step .t {{ background:#22222c; border-radius:2px; height:7px; overflow:hidden; }}
.step .f {{ height:100%; display:block; background:#3f9d63; }}
.step .f.part {{ background:#5b8def; }}
.step .f.none {{ background:transparent; }}
.step .f.busy {{ background:#e0a33e; }}
.step .l {{ font-size:9.5px; color:#63636f; margin-top:5px; line-height:1.35;
  letter-spacing:.05em; text-transform:uppercase; white-space:nowrap;
  overflow:hidden; text-overflow:ellipsis; }}
.step .n {{ font-size:10.5px; color:#9a9aa8; line-height:1.25;
  font-variant-numeric:tabular-nums; white-space:nowrap; }}
.eta small {{ display:block; color:#6e6e7c; font-size:11px; }}
</style></head><body>
<h1>Content Factory</h1>
<div class="sub">{when} &middot; refreshes every {every}s &middot; counted from disk</div>
<table><tr><th>video</th><th style="width:54%">pipeline</th>
<th>remaining</th><th>model</th><th>cut</th><th>stage</th></tr>
{rows}
</table></body></html>"""

ROW = """<tr>
<td><div class="folder">{folder}</div><div class="title">{title}</div></td>
<td><div class="steps">{steps}</div></td>
<td class="eta">{eta}</td>
<td class="model">{model}</td>
<td class="model">{cut}</td>
<td><span class="tag {cls}">{stage}</span></td></tr>"""


def step_strip(s: State) -> str:
    """One small bar per stage, in the order the pipeline runs them."""
    out = []
    for name, done, total in s.steps:
        pct = 100 * min(done, total) / max(total, 1)
        if done >= total:
            cls = "f"
        elif done:
            cls = "f part" if not (s.live and name == "frames") else "f busy"
        else:
            cls = "f none"
        # The count goes on its own line. Inline, the labels of nine
        # narrow columns ran into each other -- "SCRIPT 77/77VOICE 77/77MIX".
        count = ("&mdash;" if done >= total else "&nbsp;") if total == 1             else f"{min(done, total)}/{total}"
        out.append(f'<div class="step"><div class="t">'
                   f'<span class="{cls}" style="width:{pct:.0f}%"></span></div>'
                   f'<div class="l">{name}</div><div class="n">{count}</div></div>')
    return "".join(out)


def eta_cell(s: State) -> str:
    if not s.live:
        return "&mdash;"
    if not s.rate:
        return "measuring&hellip;"
    return f"{human(s.eta)}<small>{s.rate:.0f}s per frame</small>"


def render_html(states: list[State], every: int) -> str:
    rows = []
    for s in states:
        if s.live and s.redone is not None and s.framed == s.shots:
            done, cls = s.redone, "live"
        else:
            done = s.framed
            cls = "live" if s.live else ("done" if s.stage == "done" else "")
        rows.append(ROW.format(
            folder=s.folder, title=s.title, model=s.model,
            cut=("stale" if s.cut_stale else
                 (f"{s.runtime / 60:.1f} min" if s.runtime else "&mdash;")),
            stage=s.stage, cls=cls, steps=step_strip(s),
            eta=eta_cell(s),
            pct=100 * min(done, s.shots) / max(s.shots, 1)))
    return PAGE.format(rows="\n".join(rows), every=every,
                       when=time.strftime("%H:%M:%S"))


def serve(port: int, every: int) -> None:
    """A page anyone can leave open, instead of a command they have to run."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    cache: dict[str, object] = {"at": 0.0, "body": b""}
    lock = threading.Lock()

    def body_now() -> bytes:
        """One computation shared by every request inside a short window.

        The page reloads itself and the disk is usually busy with a render,
        so several requests overlap. Recomputing per request made them queue
        behind each other until a browser gave up waiting.
        """
        with lock:
            if time.time() - float(cache["at"]) < CACHE_SEC and cache["body"]:
                return cache["body"]  # type: ignore[return-value]
            states = [s for s in (read(x) for x in known_slugs()) if s]
            cache["body"] = render_html(states, every).encode("utf-8")
            cache["at"] = time.time()
            return cache["body"]  # type: ignore[return-value]

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = body_now()
            try:
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
                # A browser that navigated away mid-response is normal on a
                # page that reloads itself, and is not worth a traceback.
                pass

        def log_message(self, *args):
            pass          # one line per refresh is noise, not information

    # Warm the cache first. Twelve cold ffprobe calls took longer than a
    # browser waits, so the very first page load never arrived.
    for slug in known_slugs():
        read(slug)
    print(f"status page on http://127.0.0.1:{port}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()


def main() -> None:
    ap = argparse.ArgumentParser(description="Pipeline state, counted from disk.")
    ap.add_argument("slug", nargs="?", help="one project, in detail")
    ap.add_argument("--watch", action="store_true", help="redraw every 10s")
    ap.add_argument("--serve", nargs="?", type=int, const=8199,
                    help="serve it as a web page instead (default port 8199)")
    ap.add_argument("--every", type=float, default=10.0)
    a = ap.parse_args()

    if a.serve:
        serve(a.serve, int(a.every))
        return

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

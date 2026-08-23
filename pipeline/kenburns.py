"""Camera motion over a still frame, rendered subpixel-accurate.

ffmpeg's `zoompan` rounds its crop window to whole source pixels. A slow move
needs the window to advance by a fraction of a pixel per frame, so it advances
by zero on some frames and one on others, and the irregular rhythm reads as the
camera shaking -- worse the deeper the zoom, because the fraction drifts.

Measured on a real frame, 180 frames, zoom 1.12 (judder = coefficient of
variation of frame-to-frame difference; lower is smoother):

    zoompan, 2x oversampled source    0.206
    zoompan, 4x oversampled source    0.266   <- more pixels did not help
    this module                       0.039

Oversampling was the obvious fix and it does not work, because the rounding is
in the window position rather than the sampling. Pillow's `resize(box=...)`
takes a float rectangle and resamples from exactly there, which removes the
rounding entirely.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from PIL import Image

from . import config
from .schema import Motion


def ease(p: float) -> float:
    """Smoothstep, so the move starts and stops instead of snapping into
    motion at full speed. Set MOTION_EASING False for a linear move."""
    if not config.MOTION_EASING:
        return p
    return p * p * (3.0 - 2.0 * p)


def crop_rect(motion: Motion, progress: float, src_w: int, src_h: int,
              out_w: int, out_h: int) -> tuple[float, float, float, float]:
    """The source rectangle visible at this point in the shot.

    Returned as floats on purpose -- rounding here is the whole bug.
    """
    # Largest region of the source with the delivery aspect ratio, so nothing
    # is stretched if the generated frame is not exactly 16:9.
    target = out_w / out_h
    if src_w / src_h > target:
        base_w, base_h = src_h * target, float(src_h)
    else:
        base_w, base_h = float(src_w), src_w / target

    p = ease(progress)
    z = motion.zoom

    if motion.pan == "none":
        zoom = 1.0
    elif motion.pan == "in":
        zoom = 1.0 + (z - 1.0) * p
    elif motion.pan == "out":
        zoom = z - (z - 1.0) * p
    else:
        zoom = z                        # translations hold the zoom steady

    cw, ch = base_w / zoom, base_h / zoom
    slack_x, slack_y = src_w - cw, src_h - ch

    if motion.pan == "left":            # camera pans left: window moves right
        x, y = slack_x * (1.0 - p), slack_y / 2
    elif motion.pan == "right":
        x, y = slack_x * p, slack_y / 2
    elif motion.pan == "up":
        x, y = slack_x / 2, slack_y * (1.0 - p)
    elif motion.pan == "down":
        x, y = slack_x / 2, slack_y * p
    else:
        x, y = slack_x / 2, slack_y / 2

    return x, y, x + cw, y + ch


def render(source: Path, dest: Path, motion: Motion, frames: int,
           *, fps: int = config.FPS, out_w: int = config.OUT_W,
           out_h: int = config.OUT_H, crf: int = 18,
           overlay: Image.Image | None = None) -> Path:
    """Render one shot's clip: a still, moved, encoded.

    `overlay` is an RGBA image the size of the output, composited on every
    frame *after* the move. It stays put while the picture behind it drifts,
    which is what an end card or a lower-third needs -- text that scales with
    the zoom looks like a mistake.
    """
    img = Image.open(source).convert("RGB")
    src_w, src_h = img.size
    last = max(frames - 1, 1)

    if overlay is not None:
        if overlay.size != (out_w, out_h):
            overlay = overlay.resize((out_w, out_h), Image.LANCZOS)
        if overlay.mode != "RGBA":
            overlay = overlay.convert("RGBA")

    dest.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{out_w}x{out_h}",
         "-r", str(fps), "-i", "-",
         "-c:v", "libx264", "-preset", "medium", "-crf", str(crf),
         "-pix_fmt", "yuv420p", "-an", str(dest)],
        stdin=subprocess.PIPE,
    )
    try:
        for i in range(frames):
            box = crop_rect(motion, i / last, src_w, src_h, out_w, out_h)
            frame = img.resize((out_w, out_h), Image.LANCZOS, box=box)
            if overlay is not None:
                frame.paste(overlay, (0, 0), overlay)
            proc.stdin.write(frame.tobytes())
        proc.stdin.close()
    except BrokenPipeError as exc:
        proc.kill()
        raise RuntimeError(
            f"ffmpeg closed the pipe while rendering {dest.name}; "
            f"check that the source image opened correctly"
        ) from exc

    if proc.wait() != 0:
        raise RuntimeError(f"ffmpeg exited {proc.returncode} rendering {dest}")
    return dest


def _job(args: tuple) -> str:
    source, dest, motion, frames = args
    render(Path(source), Path(dest), motion, frames)
    return dest


def render_many(jobs: list[tuple], workers: int | None = None) -> None:
    """Render several clips at once.

    Each job is CPU-bound in Pillow and x264, so processes rather than threads.
    Workers are capped well below the core count because every one of them also
    starts a multithreaded encoder.
    """
    import os
    from concurrent.futures import ProcessPoolExecutor

    if workers is None:
        workers = max(1, min(config.CLIP_WORKERS, (os.cpu_count() or 4) // 2))
    if workers == 1 or len(jobs) == 1:
        for job in jobs:
            _job(job)
        return

    with ProcessPoolExecutor(max_workers=workers) as pool:
        for _ in pool.map(_job, jobs):
            pass

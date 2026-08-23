"""Measure Ken Burns judder objectively, and compare ways of fixing it.

The complaint is that the camera "starts shaking" partway through a shot. That
is zoompan's integer crop arithmetic: the crop window needs to advance by a
fractional number of source pixels each frame, so it advances by 0 px on some
frames and 1 px on others. The eye reads the irregular rhythm as shake, and it
gets worse as the zoom deepens because the fraction drifts.

Metric: mean absolute difference between consecutive frames. Under genuinely
smooth motion the picture changes by the same amount every frame, so the MAD
series is nearly flat. Under judder it alternates between "did not move" and
"jumped a pixel", so the series oscillates. The coefficient of variation
(std/mean) of that series is the judder score -- lower is smoother.

    .venv-pipeline/Scripts/python.exe -m tests.kenburns_probe
"""
from __future__ import annotations

import subprocess
import time
from pathlib import Path

import numpy as np
from PIL import Image

from pipeline import config
from pipeline.schema import Motion

TMP = Path("C:/Users/David/AppData/Local/Temp/kb_probe")
SOURCE = Path("E:/Git_NOTYET/Content_Factory/projects/roman-concrete/frames/001.png")
FRAMES = 180                    # 6 seconds at 30 fps, a typical shot
MOTION = Motion(pan="in", zoom=1.12)


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------
def render_zoompan(dest: Path, oversample: int) -> float:
    """Current approach: ffmpeg zoompan over an upscaled source."""
    src_w, src_h = config.OUT_W * oversample, config.OUT_H * oversample
    z, last = MOTION.zoom, FRAMES - 1
    graph = (
        f"scale={src_w}:{src_h}:force_original_aspect_ratio=increase,"
        f"crop={src_w}:{src_h},"
        f"zoompan=z='1+{z - 1:.4f}*on/{last}'"
        f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
        f":d={FRAMES}:s={config.OUT_W}x{config.OUT_H}:fps={config.FPS},"
        f"format=yuv420p"
    )
    t0 = time.monotonic()
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-loop", "1",
         "-i", str(SOURCE), "-filter_complex", graph, "-frames:v", str(FRAMES),
         "-r", str(config.FPS), "-c:v", "libx264", "-crf", "16",
         "-pix_fmt", "yuv420p", "-an", str(dest)],
        check=True,
    )
    return time.monotonic() - t0


def render_pil(dest: Path) -> float:
    """Crop and resample in Pillow, which takes a float box.

    Because the crop rectangle is never rounded, each frame samples exactly
    where it should. This is the reference for what smooth looks like.
    """
    img = Image.open(SOURCE).convert("RGB")
    W, H = img.size
    z, last = MOTION.zoom, FRAMES - 1

    t0 = time.monotonic()
    proc = subprocess.Popen(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         "-f", "rawvideo", "-pix_fmt", "rgb24",
         "-s", f"{config.OUT_W}x{config.OUT_H}", "-r", str(config.FPS),
         "-i", "-", "-c:v", "libx264", "-crf", "16", "-pix_fmt", "yuv420p",
         "-an", str(dest)],
        stdin=subprocess.PIPE,
    )
    for i in range(FRAMES):
        zoom = 1 + (z - 1) * (i / last)
        cw, ch = W / zoom, H / zoom
        left, top = (W - cw) / 2, (H - ch) / 2
        frame = img.resize((config.OUT_W, config.OUT_H), Image.LANCZOS,
                           box=(left, top, left + cw, top + ch))
        proc.stdin.write(frame.tobytes())
    proc.stdin.close()
    proc.wait()
    return time.monotonic() - t0


# ---------------------------------------------------------------------------
# Measurement
# ---------------------------------------------------------------------------
def _smooth(x: np.ndarray, k: int = 9) -> np.ndarray:
    pad = np.pad(x, (k // 2, k // 2), mode="edge")
    return np.convolve(pad, np.ones(k) / k, mode="valid")


def judder_score(clip: Path) -> tuple[float, float, np.ndarray]:
    """Shake, isolated from intended acceleration.

    Taking std/mean of the raw difference series does NOT work once easing is
    switched on: eased motion is deliberately slow-fast-slow, so the series has
    a large smooth ramp in it and the score triples on footage that actually
    looks fine. Subtracting a moving average leaves only the frame-to-frame
    irregularity, which is what the eye reads as shake.
    """
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(clip), "-f", "rawvideo",
         "-pix_fmt", "gray", "-s", "480x270", "-"],
        capture_output=True, check=True,
    ).stdout
    frames = np.frombuffer(raw, np.uint8).reshape(-1, 270, 480).astype(np.float32)
    mad = np.abs(np.diff(frames, axis=0)).mean(axis=(1, 2))
    # Trim the ends: motion starts and stops from a standstill there, and a low
    # difference is correct rather than a sign of anything.
    mad = mad[5:-5]
    residual = mad - _smooth(mad)
    return float(residual.std() / mad.mean()), float(mad.mean()), mad


def spark(series: np.ndarray, width: int = 60) -> str:
    bins = "_.-~=*#"
    step = max(len(series) // width, 1)
    sampled = series[::step][:width]
    lo, hi = sampled.min(), sampled.max()
    if hi - lo < 1e-9:
        return bins[0] * len(sampled)
    idx = ((sampled - lo) / (hi - lo) * (len(bins) - 1)).round().astype(int)
    return "".join(bins[i] for i in idx)


def main() -> None:
    TMP.mkdir(parents=True, exist_ok=True)
    print(f"source {SOURCE.name}, {FRAMES} frames, pan={MOTION.pan} "
          f"zoom={MOTION.zoom}\n")

    runs = [
        ("zoompan 2x (current)", lambda p: render_zoompan(p, 2)),
        ("zoompan 4x", lambda p: render_zoompan(p, 4)),
        ("pillow subpixel", render_pil),
    ]

    print(f"{'method':22s} {'render':>8s} {'judder':>8s} {'motion':>8s}   shape")
    print("-" * 78)
    for name, fn in runs:
        clip = TMP / (name.split()[0] + name.split()[1] + ".mp4")
        took = fn(clip)
        cv, mean, series = judder_score(clip)
        print(f"{name:22s} {took:7.1f}s {cv:8.3f} {mean:8.2f}   {spark(series)}")

    print("\njudder = std/mean of frame-to-frame difference; lower is smoother")


if __name__ == "__main__":
    main()

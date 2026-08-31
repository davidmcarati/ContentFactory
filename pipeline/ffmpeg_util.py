"""Thin wrapper around the ffmpeg CLI.

We shell out rather than use a Python binding: the filter graphs below are
already the hard part, and an extra abstraction layer only makes them harder
to debug. Every command is logged so a failing render can be reproduced by
pasting one line into a terminal.
"""
from __future__ import annotations

import json
import shlex
import subprocess
from pathlib import Path

from . import config


class FFmpegError(RuntimeError):
    pass


def run(args: list[str], *, quiet: bool = True) -> None:
    cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-y", *args]
    if quiet:
        cmd[3:3] = ["-loglevel", "error"]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        log = config.LOGS / "ffmpeg_last_failure.txt"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(
            shlex.join(cmd) + "\n\n" + proc.stderr, encoding="utf-8"
        )
        raise FFmpegError(
            f"ffmpeg failed (exit {proc.returncode}); full command and stderr "
            f"written to {log}\n{proc.stderr.strip()[-2000:]}"
        )


def duration(path: Path) -> float:
    """Container duration in seconds, via ffprobe."""
    proc = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "json", str(path),
        ],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise FFmpegError(f"ffprobe failed on {path}: {proc.stderr.strip()}")
    return float(json.loads(proc.stdout)["format"]["duration"])


def video_duration(path: Path) -> float:
    """Duration of the *picture*, which is not the same as the file's.

    A container reports the longest stream it holds. When a concat listing
    hits an unreadable clip, ffmpeg stops taking video there and carries on
    muxing the full narration, so the file still reports ten minutes and the
    picture stops after forty seconds. That is how a truncated cut got past
    every check in this repo and was published: the drift test compared the
    container against the timeline and found them identical, because both
    were measuring the audio.

    Counts decoded frames rather than trusting the stream's duration field,
    which mp4 fills in from the same container metadata. It costs about half
    a second on a ten-minute file.
    """
    proc = subprocess.run(
        [
            "ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0",
            "-show_entries", "stream=nb_read_frames,r_frame_rate",
            "-of", "json", str(path),
        ],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise FFmpegError(f"ffprobe failed on {path}: {proc.stderr.strip()}")
    streams = json.loads(proc.stdout).get("streams") or []
    if not streams:
        raise FFmpegError(f"{path} has no video stream")
    frames = int(streams[0]["nb_read_frames"])
    num, den = (int(x) for x in streams[0]["r_frame_rate"].split("/"))
    if den == 0:
        raise FFmpegError(f"{path} reports a zero frame rate")
    return frames * den / num


def escape_filter_path(path: Path) -> str:
    r"""Windows paths inside a filter graph need doubled escaping.

    `subtitles=C\:/foo/bar.srt` is what the filter parser expects; a raw
    `C:\foo\bar.srt` is read as a filter option separator and blows up.
    """
    s = str(path).replace("\\", "/")
    return s.replace(":", "\\:")

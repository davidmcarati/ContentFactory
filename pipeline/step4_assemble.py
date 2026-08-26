"""Step 4: turn frames + narration into the finished video.

Each shot is first rendered to its own short clip. That costs a little disk but
buys two things worth far more on a 90-shot video: a bad shot can be
regenerated on its own, and the expensive part is resumable after a crash.
Clips are independent, so they render in parallel processes.

The crossfade arithmetic is the fiddly bit. `xfade` overlaps its two inputs, so
a naive chain makes the video shorter than the narration and the two drift
apart -- by shot 90 the voice is describing a picture that left the screen ten
seconds ago. The fix is to render every clip CROSSFADE_SEC longer than its
narration and let the transition eat exactly that surplus, so shot i always
occupies precisely its own duration on the final timeline.

Camera motion lives in `kenburns.py`; see its docstring for why it does not use
ffmpeg's zoompan.
"""
from __future__ import annotations

import argparse
import time
from pathlib import Path

from . import config, kenburns, outro, subtitles
from .ffmpeg_util import run, duration as probe_duration, escape_filter_path
from .schema import Shot, Storyboard


def has_outro() -> bool:
    return config.OUTRO_ENABLED and config.OUTRO_SECONDS > 0


def all_cuts(sb: Storyboard) -> bool:
    """Is this storyboard cut together rather than crossfaded?

    Whole-storyboard rather than per-boundary on purpose. Mixing the two would
    mean tracking which individual clips carry a surplus and which do not,
    for a result nobody has asked for; a video either dissolves or it cuts.
    """
    return bool(sb.shots) and all(s.transition == "cut" for s in sb.shots)


def clip_length(sb: Storyboard, shot: Shot) -> float:
    """How long this shot's clip file must be.

    Under crossfades every clip but the very last carries a CROSSFADE_SEC
    surplus that the following transition consumes. With an end card appended,
    the last shot is no longer the last clip, so it needs the surplus too --
    forget that and the final crossfade eats half a second of the closing line.

    Cuts consume nothing, so each clip is exactly its own narration.
    """
    if all_cuts(sb):
        return shot.duration
    is_final = shot.id == len(sb.shots) and not has_outro()
    surplus = 0.0 if is_final else config.CROSSFADE_SEC
    return shot.duration + surplus


def render_clips(sb: Storyboard, *, force: bool = False) -> list[Path]:
    jobs, paths = [], []
    for shot in sb.shots:
        out = sb.dir / "clips" / f"{shot.stem}.mp4"
        paths.append(out)
        if out.exists() and not force:
            continue
        if not shot.frame_path:
            raise ValueError(f"shot {shot.id} has no frame; run step 3 first")
        frames = max(round(clip_length(sb, shot) * config.FPS), 1)
        jobs.append((str(Path(shot.frame_path)), str(out), shot.motion, frames))

    if jobs:
        print(f"rendering {len(jobs)} clips "
              f"({len(paths) - len(jobs)} already done)...")
        t0 = time.monotonic()
        kenburns.render_many(jobs)
        print(f"  {len(jobs)} clips in {time.monotonic() - t0:.0f}s")
    else:
        print(f"all {len(paths)} clips already rendered")
    return paths


def _xfade_chain(durations: list[float]) -> tuple[str, str]:
    """Chain every clip together, offsetting each transition by the running
    timeline position so picture and voice stay locked."""
    parts, prev, elapsed = [], "0:v", 0.0
    for i, length in enumerate(durations[:-1]):
        elapsed += length
        label = f"v{i}"
        parts.append(
            f"[{prev}][{i + 1}:v]xfade=transition=fade"
            f":duration={config.CROSSFADE_SEC}:offset={elapsed:.3f}[{label}]"
        )
        prev = label
    return ";".join(parts), prev


def assemble(sb: Storyboard, *, force: bool = False,
             skip_review: bool = False) -> Path:
    # Frames must have been looked at. Every visual defect this project has
    # shipped was invisible to the tests and obvious in a picture, so the
    # looking is a gate rather than a good intention. See pipeline/review.py.
    if not skip_review:
        from .review import require_reviewed
        require_reviewed(sb)

    sb.ensure_dirs()
    narration = sb.dir / "audio" / "narration.wav"
    if not narration.exists():
        raise FileNotFoundError(
            f"{narration} missing; run step 2 (voice) before assembling"
        )

    clips = render_clips(sb, force=force)
    durations = [s.duration for s in sb.shots]

    end_card = outro.render(sb, force=force)
    if end_card:
        clips.append(end_card)
        durations.append(config.OUTRO_SECONDS)
        print(f"  end card  {config.OUTRO_SECONDS:.1f}s  "
              f"\"{config.OUTRO_TEXT}\"")

    total = sum(durations)
    out = sb.dir / f"{sb.slug}.mp4"
    args: list[str] = []

    if all_cuts(sb):
        # One concat input instead of one input per clip. The crossfade path
        # opens every clip at once and chains a filter per boundary, which is
        # already uncomfortable at 90 shots; cut-driven pacing runs to two or
        # three hundred, where it stops being a good idea entirely. Concat is
        # linear, opens one file at a time, and needs no arithmetic because a
        # cut consumes nothing.
        listing = sb.dir / "clips" / "concat.txt"
        listing.write_text(
            "".join(f"file '{c.as_posix()}'\n" for c in clips),
            encoding="utf-8",
        )
        args += ["-f", "concat", "-safe", "0", "-i", str(listing)]
        args += ["-i", str(narration)]
        audio_idx = 1
        graph, vlabel = "[0:v]null[vout]", "vout"
        print(f"concatenating {len(clips)} clips (hard cuts)")
    else:
        for c in clips:
            args += ["-i", str(c)]
        args += ["-i", str(narration)]
        audio_idx = len(clips)

        if len(clips) == 1:
            graph, vlabel = "[0:v]null[vout]", "vout"
        else:
            chain, last = _xfade_chain(durations)
            graph, vlabel = f"{chain};[{last}]null[vout]", "vout"

    subtitles.build_srt(sb)                      # for upload
    if config.BURN_SUBTITLES:
        ass = subtitles.build_ass(sb)            # for burning
        graph += f";[{vlabel}]subtitles='{escape_filter_path(ass)}'[vsub]"
        vlabel = "vsub"

    # Fade to black at the very end. Without it the file simply stops on a lit
    # frame, which reads as a truncated download rather than an ending.
    if config.OUTRO_FADE_OUT > 0:
        fade_at = max(total - config.OUTRO_FADE_OUT, 0.0)
        graph += (f";[{vlabel}]fade=t=out:st={fade_at:.3f}"
                  f":d={config.OUTRO_FADE_OUT}[vfade]")
        vlabel = "vfade"

    # The narration stops before the end card does. `-shortest` would then trim
    # the video down to the audio and throw the card away, so the audio is
    # padded with silence to the full length instead and the output is cut to
    # an explicit duration.
    graph += f";[{audio_idx}:a]apad[aout]"

    args += [
        "-filter_complex", graph,
        "-map", f"[{vlabel}]",
        "-map", "[aout]",
        "-t", f"{total:.3f}",
        "-c:v", "libx264", "-preset", "medium", "-crf", "19",
        "-pix_fmt", "yuv420p", "-r", str(config.FPS),
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        str(out),
    ]
    print("muxing final video...")
    run(args)

    got = probe_duration(out)
    print(f"\n{out}")
    print(f"  duration {got / 60:.1f} min "
          f"({sb.total_sec / 60:.1f} min narration"
          + (f" + {config.OUTRO_SECONDS:.0f}s end card)" if end_card else ")"))
    if abs(got - total) > 1.0:
        print("  WARNING: drift over 1s between audio and video timelines")
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Assemble the final video.")
    ap.add_argument("slug", help="project slug under projects/")
    ap.add_argument("--force", action="store_true",
                    help="re-render clips that already exist")
    ap.add_argument("--skip-review", action="store_true",
                    help="assemble frames nobody has looked at (smoke tests "
                         "and timing experiments; not for anything shipped)")
    a = ap.parse_args()
    sb = Storyboard.load(a.slug)
    sb.require_valid()
    assemble(sb, force=a.force, skip_review=a.skip_review)


if __name__ == "__main__":
    main()

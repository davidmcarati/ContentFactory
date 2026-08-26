"""Step 2: narration, and the timeline that everything else hangs off.

This runs *before* image generation on purpose. The length of a shot is the
length of its narration -- not an estimate, the measured sample count -- so
until the voice exists there is no timeline to cut pictures to.

Two artifacts come out of here:
  audio/NNN.wav     one file per shot, kept so a single line can be redone
  audio/narration.wav  the continuous track, shot audio plus the tail pauses

`narration.wav` is built by concatenating the per-shot files with exactly the
padding the schema promises, which is what keeps step 4's crossfade offsets
honest. If you regenerate one shot, rebuild the narration too.
"""
from __future__ import annotations

import argparse
import numpy as np
import soundfile as sf

from pathlib import Path

from . import config
from .schema import Storyboard

_PIPELINE = None


def _kokoro():
    """Load Kokoro-82M once. Import is lazy so the fake backend and the
    narration builder work on a machine with no model downloaded."""
    global _PIPELINE
    if _PIPELINE is None:
        from kokoro import KPipeline
        print("loading Kokoro-82M...")
        _PIPELINE = KPipeline(lang_code="a")     # 'a' = American English
    return _PIPELINE


def _as_numpy(audio) -> np.ndarray:
    """Kokoro hands back a torch tensor; soundfile wants an array."""
    if hasattr(audio, "detach"):
        audio = audio.detach().cpu().numpy()
    return np.asarray(audio, dtype=np.float32).reshape(-1)


def synth(text: str, voice: str, speed: float) -> np.ndarray:
    """Speak one narration line as a single contiguous waveform.

    Kokoro splits long input into chunks and yields them one at a time; we
    glue them back together so a shot is always exactly one audio file.
    """
    chunks = [_as_numpy(a) for _, _, a in _kokoro()(text, voice=voice, speed=speed)]
    if not chunks:
        raise RuntimeError(f"Kokoro produced no audio for: {text[:60]!r}")
    return np.concatenate(chunks)


def synth_fake(text: str, voice: str, speed: float) -> np.ndarray:
    """A stand-in that produces silence of a plausible length.

    Lets the whole pipeline -- timeline maths, crossfades, subtitles, muxing --
    be exercised end to end before any model has been downloaded.
    """
    words = max(len(text.split()), 1)
    seconds = words / (2.6 * speed)          # ~155 wpm, typical narration pace
    return np.zeros(int(seconds * config.TTS_SAMPLE_RATE), dtype=np.float32)


def normalize(audio: np.ndarray, peak_dbfs: float = -1.0) -> np.ndarray:
    peak = float(np.max(np.abs(audio))) if audio.size else 0.0
    if peak < 1e-6:
        return audio
    return audio * (10 ** (peak_dbfs / 20) / peak)


def trim(audio: np.ndarray) -> np.ndarray:
    """Cut the silence Kokoro leaves around every utterance.

    Measured in tests/prosody_probe.py: ~0.40 s before the first word and
    ~0.59 s after the last, on every line regardless of length. That silence
    was being measured as narration, so it set the pace of the video and no
    amount of tuning SHOT_TAIL_SEC could reach it.

    Run after normalize(), so the peak is at a known level and the floor below
    means the same thing on every line.
    """
    if not config.TRIM_SILENCE or audio.size == 0:
        return audio
    floor = 10 ** (config.TRIM_FLOOR_DBFS / 20)
    loud = np.flatnonzero(np.abs(audio) > floor)
    if loud.size == 0:
        return audio
    margin = int(config.TRIM_MARGIN_SEC * config.TTS_SAMPLE_RATE)
    start = max(int(loud[0]) - margin, 0)
    end = min(int(loud[-1]) + 1 + margin, len(audio))
    return audio[start:end]


# ---------------------------------------------------------------------------
def build_narration(sb: Storyboard) -> Path:
    """Stitch the per-shot files into one track, tails included.

    Every shot contributes exactly `audio_sec + shot.tail` of timeline, which
    is the same arithmetic step 4 uses to place its cuts. The tail is per shot
    rather than global because a line broken off mid-sentence needs a
    different gap after it than one that ends on a full stop; see Shot.tail.
    """
    sr = config.TTS_SAMPLE_RATE

    parts = []
    for shot in sb.shots:
        if not shot.audio_path:
            raise ValueError(f"shot {shot.id} has no audio; synthesize it first")
        audio, file_sr = sf.read(shot.audio_path, dtype="float32", always_2d=False)
        if file_sr != sr:
            raise ValueError(
                f"shot {shot.id}: {file_sr} Hz, expected {sr} Hz -- delete the "
                f"wav and regenerate rather than resampling silently"
            )
        tail = np.zeros(int(round(shot.tail * sr)), dtype=np.float32)
        parts += [np.asarray(audio, dtype=np.float32).reshape(-1), tail]

    out = sb.dir / "audio" / "narration.wav"
    sf.write(out, np.concatenate(parts), sr)
    return out


def voice(sb: Storyboard, *, force: bool = False, fake: bool = False) -> Path:
    sb.ensure_dirs()
    backend = synth_fake if fake else synth
    sr = config.TTS_SAMPLE_RATE

    for shot in sb.shots:
        path = sb.dir / "audio" / f"{shot.stem}.wav"
        if not path.exists() or force:
            audio = trim(normalize(backend(shot.vo, sb.voice.voice_id,
                                           sb.voice.speed)))
            sf.write(path, audio, sr)
        info = sf.info(path)

        # The measured length is the source of truth for the whole timeline.
        shot.audio_path = str(path)
        shot.audio_sec = info.frames / info.samplerate

        flag = ""
        if shot.duration > config.MAX_SHOT_SEC:
            flag = "  <-- long, consider splitting this shot"
        elif shot.duration < config.MIN_SHOT_SEC:
            flag = "  <-- very short, consider merging"
        print(f"  shot {shot.stem}  {shot.audio_sec:5.2f}s{flag}")

    narration = build_narration(sb)
    sb.save()
    print(f"\nnarration: {narration}")
    print(f"total: {sb.total_sec / 60:.2f} min across {len(sb.shots)} shots")
    return narration


def main() -> None:
    ap = argparse.ArgumentParser(description="Generate the narration track.")
    ap.add_argument("slug", help="project slug under projects/")
    ap.add_argument("--force", action="store_true", help="re-synthesize existing wavs")
    ap.add_argument("--fake", action="store_true",
                    help="silent placeholder audio, for testing without models")
    a = ap.parse_args()
    sb = Storyboard.load(a.slug)
    sb.require_valid()
    voice(sb, force=a.force, fake=a.fake)


if __name__ == "__main__":
    main()

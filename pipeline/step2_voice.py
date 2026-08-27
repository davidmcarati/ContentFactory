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
from .schema import Shot, Storyboard, _SENTENCE_END

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


def synth_timed(text: str, voice: str,
                speed: float) -> tuple[np.ndarray, list[tuple[str, float]]]:
    """Speak a run of narration and report where each word ended.

    Returns the waveform and a list of (word, end_seconds). Kokoro emits a
    token per word *and* per punctuation mark, and may split long input into
    several chunks whose timestamps each restart at zero, so the offsets are
    accumulated here.

    The word ends are what let a run be spoken as one take and still cut into
    shots on measured boundaries rather than guessed ones -- the invariant
    this whole pipeline is built around.
    """
    audio: list[np.ndarray] = []
    words: list[tuple[str, float]] = []
    offset = 0.0

    for result in _kokoro()(text, voice=voice, speed=speed):
        chunk = _as_numpy(result.output.audio if hasattr(result.output, "audio")
                          else result.output)
        for token in (result.tokens or []):
            end = getattr(token, "end_ts", None)
            body = (token.text or "").strip()
            if end is None or not any(c.isalnum() for c in body):
                continue
            words.append((body, offset + float(end)))
        audio.append(chunk)
        offset += len(chunk) / config.TTS_SAMPLE_RATE

    if not audio:
        raise RuntimeError(f"Kokoro produced no audio for: {text[:60]!r}")
    return np.concatenate(audio), words


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


def leading_silence(audio: np.ndarray) -> float:
    """Seconds of near-nothing before the first word."""
    if not config.TRIM_SILENCE or audio.size == 0:
        return 0.0
    floor = 10 ** (config.TRIM_FLOOR_DBFS / 20)
    loud = np.flatnonzero(np.abs(audio) > floor)
    if loud.size == 0:
        return 0.0
    margin = int(config.TRIM_MARGIN_SEC * config.TTS_SAMPLE_RATE)
    return max(int(loud[0]) - margin, 0) / config.TTS_SAMPLE_RATE


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
def segments(sb: Storyboard) -> list[list[Shot]]:
    """Group shots into runs that will each be spoken as one utterance.

    A run only ever ends where a sentence ends. Breaking mid-sentence would
    reintroduce exactly the seam this is here to remove, so the word target is
    a target and the sentence boundary is a rule.
    """
    runs: list[list[Shot]] = []
    current: list[Shot] = []
    words = 0

    for shot in sb.shots:
        current.append(shot)
        words += len(shot.vo.split())
        ends_sentence = _SENTENCE_END.search(shot.vo) is not None
        if ends_sentence and words >= config.SEGMENT_WORDS:
            runs.append(current)
            current, words = [], 0
        elif words >= config.SEGMENT_MAX_WORDS and ends_sentence:
            runs.append(current)
            current, words = [], 0

    if current:
        runs.append(current)
    return runs


def split_points(run: list[Shot], words: list[tuple[str, float]],
                 total_sec: float) -> list[float]:
    """Where each shot in a run ends, in seconds from the start of the run.

    Uses Kokoro's word ends. Punctuation tokens are already filtered out, so
    the nth spoken word of the run is words[n]; a shot boundary is the end of
    its own last word.

    Falls back to splitting in proportion to word count if the timestamps do
    not line up -- which is a guess, and says so, because a silent guess here
    would put the cuts slightly off the voice for a whole video.
    """
    counts = [len(shot.vo.split()) for shot in run]
    if sum(counts) != len(words):
        spoken = sum(counts) or 1
        print(f"    note: {len(words)} timed words for {spoken} written; "
              f"splitting this run proportionally")
        out, acc = [], 0
        for n in counts:
            acc += n
            out.append(total_sec * acc / spoken)
        return out

    out, acc = [], 0
    for n in counts:
        acc += n
        out.append(words[acc - 1][1])
    out[-1] = total_sec              # the last shot owns the rest of the take
    return out


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
        lead = np.zeros(int(round(shot.lead_sec * sr)), dtype=np.float32)
        tail = np.zeros(int(round(shot.tail * sr)), dtype=np.float32)
        parts += [lead, np.asarray(audio, dtype=np.float32).reshape(-1), tail]

    out = sb.dir / "audio" / "narration.wav"
    sf.write(out, np.concatenate(parts), sr)
    return out


def speak_run(run: list[Shot], sb: Storyboard, *, fake: bool) -> None:
    """Speak one run as a single take and slice it into its shots.

    The slices are contiguous, so concatenating them reproduces the take
    exactly: within a run the viewer hears one continuous performance while
    the picture cuts underneath it.
    """
    sr = config.TTS_SAMPLE_RATE
    text = " ".join(shot.vo.strip() for shot in run)

    if fake:
        audio = synth_fake(text, sb.voice.voice_id, sb.voice.speed)
        words = []
    else:
        audio, words = synth_timed(text, sb.voice.voice_id, sb.voice.speed)
    audio = normalize(audio)

    # Trim the run, not its pieces: silence inside a take is the speaker
    # breathing, and cutting it out is what made the old version choppy.
    head = leading_silence(audio)
    audio = trim(audio)
    total = len(audio) / sr
    words = [(w, max(t - head, 0.0)) for w, t in words]

    ends = split_points(run, words, total) if len(run) > 1 else [total]

    start = 0.0
    for shot, end in zip(run, ends):
        end = min(max(end, start), total)
        piece = audio[int(round(start * sr)): int(round(end * sr))]
        path = sb.dir / "audio" / f"{shot.stem}.wav"
        sf.write(path, piece, sr)
        shot.audio_path = str(path)
        shot.audio_sec = len(piece) / sr
        # Mid-run shots are followed by the next slice of the same take.
        shot.tail_sec = 0.0
        start = end
    run[-1].tail_sec = config.SENTENCE_TAIL_SEC


def voice(sb: Storyboard, *, force: bool = False, fake: bool = False) -> Path:
    sb.ensure_dirs()

    runs = segments(sb)
    spoken = sum(len(s.vo.split()) for s in sb.shots)
    print(f"{len(sb.shots)} shots spoken as {len(runs)} runs "
          f"(~{spoken / max(len(runs), 1):.0f} words each)")

    done = all((sb.dir / "audio" / f"{s.stem}.wav").exists() for s in sb.shots)
    if done and not force:
        # Re-measure what is already on disk rather than re-speaking it, but
        # the run structure still has to be reapplied: the tails live in the
        # storyboard, not in the wavs.
        for run in runs:
            for shot in run:
                path = sb.dir / "audio" / f"{shot.stem}.wav"
                info = sf.info(path)
                shot.audio_path = str(path)
                shot.audio_sec = info.frames / info.samplerate
                shot.tail_sec = 0.0
            run[-1].tail_sec = config.SENTENCE_TAIL_SEC
    else:
        for i, run in enumerate(runs, start=1):
            speak_run(run, sb, fake=fake)
            span = sum(s.audio_sec for s in run)
            print(f"  run {i:3d}/{len(runs)}  {len(run):2d} shots  {span:5.1f}s")

    # Nothing to look at yet when the voice starts on frame one.
    sb.shots[0].lead_sec = config.LEAD_IN_SEC

    long = [s.id for s in sb.shots if s.duration > config.MAX_SHOT_SEC]
    short = [s.id for s in sb.shots if s.duration < config.MIN_SHOT_SEC]
    if long:
        print(f"  {len(long)} shots over {config.MAX_SHOT_SEC}s: {long[:8]}")
    if short:
        print(f"  {len(short)} shots under {config.MIN_SHOT_SEC}s: {short[:8]}")

    narration = build_narration(sb)
    sb.save()
    print(f"\nnarration: {narration}")
    print(f"total: {sb.total_sec / 60:.2f} min across {len(sb.shots)} shots, "
          f"{len(runs)} unbroken takes")
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

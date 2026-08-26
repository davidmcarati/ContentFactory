"""What does a mid-sentence cut cost the voice?

Cut-driven pacing wants a new picture every two to four seconds. But the
script line is also the TTS unit -- step 2 speaks each shot's `vo` on its own
and measures the result -- so short shots mean handing Kokoro sentence
fragments, and a fragment gets spoken like a sentence: pitch falls to a full
stop, and silence is left after it.

Eighty-five shots could absorb that. Two hundred and thirty cannot, so this
measures it before the script gets re-cut by hand.

Three real sentences from 3_clothes, each spoken three ways:

  whole     one utterance, the way it ships today
  clauses   split where a comma or a conjunction already sits
  hard      split every ~6 words, ignoring where the grammar is

For each: how much longer the pieces run than the whole, how much dead air
sits at the end of each piece, and which way the pitch moves over the last
third of it. A falling tail is the sound of a full stop that the writing did
not ask for.

Writes joined wavs to the scratch dir so the seams can also just be heard.

Run:  .venv-pipeline/Scripts/python.exe -m tests.prosody_probe
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import soundfile as sf

from pipeline import config
from pipeline.step2_voice import normalize, synth

OUT = Path(__file__).resolve().parent.parent / ".probe" / "prosody"

SR = config.TTS_SAMPLE_RATE

CASES = [
    {
        "name": "louse",
        "whole": "There are two kinds of louse that live on people, and one "
                 "lives in the hair on your head.",
        "clauses": ["There are two kinds of louse that live on people,",
                    "and one lives in the hair on your head."],
        "hard": ["There are two kinds of louse",
                 "that live on people, and one",
                 "lives in the hair on your head."],
    },
    {
        "name": "purple",
        "whole": "Tyrian purple came from a gland in a sea snail, and it took "
                 "an enormous number of snails, rotted in vats, to dye a "
                 "single garment.",
        "clauses": ["Tyrian purple came from a gland in a sea snail,",
                    "and it took an enormous number of snails,",
                    "rotted in vats, to dye a single garment."],
        "hard": ["Tyrian purple came from a gland",
                 "in a sea snail, and it took",
                 "an enormous number of snails, rotted",
                 "in vats, to dye a single garment."],
    },
    {
        "name": "uniform",
        "whole": "Put anyone in a particular set of clothes and their posture, "
                 "vocabulary and willingness to give orders all shift slightly.",
        "clauses": ["Put anyone in a particular set of clothes",
                    "and their posture, vocabulary and willingness",
                    "to give orders all shift slightly."],
        "hard": ["Put anyone in a particular",
                 "set of clothes and their posture,",
                 "vocabulary and willingness to give",
                 "orders all shift slightly."],
    },
]

# Anything under this counts as silence. Kokoro's noise floor sits well below
# it, and normalize() puts the peak at -1 dBFS, so the ratio is stable.
SILENCE = 0.01


def trailing_silence(audio: np.ndarray) -> float:
    """Seconds of near-nothing at the end of the clip."""
    loud = np.flatnonzero(np.abs(audio) > SILENCE)
    if loud.size == 0:
        return len(audio) / SR
    return (len(audio) - loud[-1] - 1) / SR


def leading_silence(audio: np.ndarray) -> float:
    loud = np.flatnonzero(np.abs(audio) > SILENCE)
    return len(audio) / SR if loud.size == 0 else loud[0] / SR


def trimmed(audio: np.ndarray) -> np.ndarray:
    loud = np.flatnonzero(np.abs(audio) > SILENCE)
    return audio if loud.size == 0 else audio[loud[0]: loud[-1] + 1]


def f0(window: np.ndarray) -> float:
    """Crude pitch by autocorrelation. Good enough to see a fall.

    Only the direction matters here, so there is no point reaching for a
    proper estimator: 70-300 Hz covers a male narrator, and a window that is
    unvoiced is reported as zero and skipped by the caller.
    """
    window = window - window.mean()
    if np.max(np.abs(window)) < SILENCE:
        return 0.0
    corr = np.correlate(window, window, mode="full")[len(window) - 1:]
    lo, hi = int(SR / 300), int(SR / 70)
    if hi >= len(corr):
        return 0.0
    peak = lo + int(np.argmax(corr[lo:hi]))
    # A voiced window correlates strongly with itself one period later; an
    # unvoiced one does not, and would otherwise report a confident garbage
    # pitch from whatever noise happened to line up.
    if corr[peak] < 0.3 * corr[0]:
        return 0.0
    return SR / peak


def pitch_move(audio: np.ndarray) -> float:
    """Semitones between the middle and the end of the last voiced stretch.

    Negative means the voice fell -- the sound of a full stop.
    """
    loud = np.flatnonzero(np.abs(audio) > SILENCE)
    if loud.size == 0:
        return 0.0
    speech = audio[: loud[-1] + 1]
    win = int(0.12 * SR)
    if len(speech) < 3 * win:
        return 0.0
    tail = speech[-3 * win:]
    a, b = f0(tail[:win]), f0(tail[-win:])
    if a <= 0 or b <= 0:
        return 0.0
    return 12 * np.log2(b / a)


def speak(text: str) -> np.ndarray:
    return normalize(synth(text, config.TTS_VOICE, config.TTS_SPEED))


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    tail = np.zeros(int(round(config.SHOT_TAIL_SEC * SR)), dtype=np.float32)

    print(f"tail padding between shots: {config.SHOT_TAIL_SEC:.2f}s\n")
    rows = []

    for case in CASES:
        whole = speak(case["whole"])
        whole_sec = len(whole) / SR
        sf.write(OUT / f"{case['name']}_whole.wav", whole, SR)

        words = len(case["whole"].split())
        whole_trim = len(trimmed(whole)) / SR + config.SHOT_TAIL_SEC
        print(f"--- {case['name']}  ({words} words, {whole_sec:.2f}s whole) ---")
        print(f"  whole    {whole_sec:6.2f}s  "
              f"head {leading_silence(whole):.2f}s  "
              f"gap {trailing_silence(whole):.2f}s  "
              f"pitch {pitch_move(whole):+5.1f} st")
        print(f"           trimmed to {whole_trim:.2f}s")

        for mode in ("clauses", "hard"):
            pieces = [speak(p) for p in case[mode]]
            joined = np.concatenate(
                [x for p in pieces for x in (p, tail)])
            sf.write(OUT / f"{case['name']}_{mode}.wav", joined, SR)

            spoken = sum(len(p) for p in pieces) / SR
            total = len(joined) / SR
            gaps = [trailing_silence(p) for p in pieces[:-1]]
            moves = [pitch_move(p) for p in pieces[:-1]]
            moves = [m for m in moves if m != 0.0]

            secs = [len(p) / SR for p in pieces]
            print(f"  {mode:8s} {total:6.2f}s  "
                  f"(+{total - whole_sec:.2f}s, {len(pieces)} shots of "
                  f"{min(secs):.1f}-{max(secs):.1f}s)")
            print(f"           mid-sentence gap "
                  f"{np.mean(gaps) + config.SHOT_TAIL_SEC:.2f}s avg "
                  f"(silence {np.mean(gaps):.2f} + tail {config.SHOT_TAIL_SEC:.2f})")
            print(f"           pitch at the seam "
                  f"{np.mean(moves) if moves else 0:+5.1f} st avg")

            # What it would cost if step 2 trimmed each shot to its speech and
            # then applied the tail it promises, instead of shipping whatever
            # silence the model happened to leave behind.
            cut = sum(len(trimmed(p)) / SR + config.SHOT_TAIL_SEC for p in pieces)
            tsecs = [len(trimmed(p)) / SR + config.SHOT_TAIL_SEC for p in pieces]
            print(f"           trimmed to {cut:.2f}s  "
                  f"({cut - whole_trim:+.2f}s vs whole, shots of "
                  f"{min(tsecs):.1f}-{max(tsecs):.1f}s)")
            rows.append((case["name"], mode, total - whole_sec, cut - whole_trim,
                         np.mean(gaps) + config.SHOT_TAIL_SEC,
                         np.mean(moves) if moves else 0.0))
        print()

    print("=== summary ===")
    for mode in ("clauses", "hard"):
        m = [r for r in rows if r[1] == mode]
        print(f"  {mode:8s} as-is +{np.mean([r[2] for r in m]):.2f}s  "
              f"trimmed {np.mean([r[3] for r in m]):+.2f}s  "
              f"seam {np.mean([r[4] for r in m]):.2f}s  "
              f"pitch {np.mean([r[5] for r in m]):+.1f} st")
    print(f"\nwavs in {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

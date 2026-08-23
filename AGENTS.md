# Working in this repository

Local pipeline that turns a written script into a narrated long-form video.
Everything runs on one machine: no cloud APIs, no per-minute billing.

Read this before changing anything. Most of it is the result of a measurement
that contradicted the obvious answer, and the obvious answer is what you will
reach for first.

---

## 1. The shape of the thing

Five steps. Each is independently runnable and resumable. They communicate
only through one file per project: `projects/<slug>/storyboard.json`.

```
step 1  step1_script.py    narration text   ->  storyboard.json
step 2  step2_voice.py     Kokoro-82M       ->  audio/*.wav, narration.wav
step 3  step3_frames.py    FLUX / open collections  ->  frames/*.png
step 4  step4_assemble.py  ffmpeg           ->  <slug>.mp4
step 5  publish.py         Pillow + ComfyUI ->  D:/TheArtOfChaosVideos/<slug>/
```

`pipeline/run.py` chains all five. `pipeline/schema.py` owns every data type
and is the file to read first.

Supporting modules: `kenburns.py` (camera motion), `subtitles.py`,
`outro.py` (end card), `assets.py` (real images + licensing),
`comfy_client.py`, `workflows.py` (ComfyUI graphs), `ffmpeg_util.py`,
`config.py` (all the knobs).

**Editorial decisions live in [STYLE.md](STYLE.md)** — tone, humour, structure,
when to fetch a real image instead of generating one, how the packaging should
read. Read it before writing a script; it wins over your own taste.
[DEPENDENCY.md](DEPENDENCY.md) covers installing everything from nothing.

Step 5 delivers a self-contained folder per video — film, thumbnail,
description with chapters and tags, subtitles, credits and the storyboard that
produced it — outside the repo, on `D:/TheArtOfChaosVideos`.

---

## 2. Invariants — do not break these

**Voice is measured, never estimated.** A shot lasts exactly as long as its
narration takes to speak, read from the rendered wav's sample count. Step 2
therefore runs *before* step 3. If you find yourself estimating a duration
from a word count anywhere outside `synth_fake`, you have introduced drift.

**The narration track and the video timeline are built from the same
arithmetic.** `shot.duration == audio_sec + SHOT_TAIL_SEC`. `narration.wav`
concatenates shot audio plus exactly that tail; step 4 places every crossfade
at the running sum of the same value. Change one and you must change both, and
`tests/smoke.py` is what will tell you that you did not.

**Crossfades consume a deliberate surplus.** `xfade` overlaps its inputs, so
every clip except the last is rendered `CROSSFADE_SEC` longer than its
narration. Remove that surplus and the video gets shorter than the audio, a
little more on every cut, until the voice is describing a picture that left the
screen ten seconds ago.

**Every step writes results back immediately.** Step 3 saves the storyboard
after each frame. A 90-shot render killed at shot 60 must cost one shot on
resume, not sixty.

**Seeds are derived, never random.** `Storyboard.seed_for()` is
`seed_base + shot.id`. Regenerating one frame two days later must reproduce the
same picture, or it will no longer match its neighbours.

**The end card sits outside the narration timeline.** It is appended after the
last shot, not added as a shot, because it has no narration and a silent shot
would need special-casing in every duration calculation. Two consequences that
are easy to miss: the last shot now needs a crossfade surplus like every other
clip, and the audio must be padded with silence — `-shortest` would otherwise
trim the video back to the narration and throw the card away.

**Chapters are anchored to shot ids, never to timestamps.** Re-recording the
narration then moves them correctly instead of leaving them pointing at the
wrong moment.

**Licence tiers are an allowlist, and unknown means no.** See section 5.

---

## 3. Measurements that overturned the obvious choice

Do not "optimise" these back without re-running the probe that settled them.

### Ken Burns does not use ffmpeg's zoompan

`zoompan` rounds its crop window to whole source pixels. A slow move needs a
fractional advance per frame, so it advances 0 px on some frames and 1 px on
others, and the irregular rhythm reads as camera shake.

Oversampling the source is the intuitive fix. It does not work:

| method | shake (high-frequency component) |
|---|---|
| zoompan, 2x oversampled | 0.186 |
| zoompan, 4x oversampled | 0.264 |
| `kenburns.py` (Pillow, float crop box) | 0.035 |

The rounding is in the *window position*, not the sampling, so more pixels do
not help. `Image.resize(box=...)` takes a float rectangle. Probe:
`tests/kenburns_probe.py`.

**The judder metric has a trap.** Plain std/mean of the frame-difference
series triples once easing is on, because eased motion is deliberately
slow-fast-slow. Subtract a moving average first and measure only the residual.
Getting this wrong once already produced a false regression report.

### Frames are generated large, not upscaled

| configuration | time/frame |
|---|---|
| generate 1344x768 | 5.1 s |
| generate 1920x1088 | 9.1 s |
| **generate 2304x1296 (current)** | **13.1 s** |
| generate 1344x768 + 4x ESRGAN | 30.0 s |

The ESRGAN route costs twice as much and comes back visibly oversharpened.
`GEN_W` is set so that the deepest zoom (~1.16) never outruns real pixels.
`esrgan=True` survives in `workflows.py` for material that genuinely needs
reconstruction.

### Subtitles are burned from .ass, never .srt

libass assumes a 288-line canvas for SRT input and scales the font by
1080/288. A nominal "size 22" arrived on screen 82 px tall. The generated .ass
declares `PlayResX/PlayResY`, so `SUB_FONT_SIZE` is real pixels.

Cues break at sentence boundaries, then clause boundaries, and only then
between arbitrary words — a card ending "...for two thousand" with "years."
stranded on the next one reads as a fault even when the timing is perfect.

### The negative prompt does nothing on FLUX.1-schnell

schnell is guidance-distilled and runs at cfg 1.0, which means the negative
conditioning is never actually applied. `style.negative` is wired up and
carried through the graph, and it has no effect whatsoever.

This is easy to forget and expensive when you do. An entire batch was prompted
with `text, letters, typography, gibberish text` in the negative and came back
covered in fake body copy and invented labels.

**The only lever is the positive prompt.** To keep text out of a frame, do not
ask for objects that carry text: `chart`, `poster`, `diagram`, `infographic`,
`calendar`, `newspaper`, `magazine`, `manual`, `label`, `plate`. Describe the
same idea as physical objects instead — "thick stacked paper strips of
different heights" rather than "a bar chart" — and the text mostly disappears.
"editorial illustration" in the base style has the same problem: it biases
toward magazine layouts, which the model dutifully fills with fake paragraphs.

### Asset shots are fetched before generated ones

Step 3 sorts fetches first. Interleaved, ComfyUI dropped the 16 GB checkpoint
during the network-bound stretches and reloaded it afterwards, costing more
than the fetches did.

---

## 4. Environment traps

**The Bash tool collapses `\\` to `\`, even inside a quoted heredoc.** Writing
Python containing a regex or an escape sequence through a heredoc silently
corrupts it. Use the Write/Edit tools for any file containing backslashes, or
build them with `chr(92)`.

**Windows console is cp1252** and dies on the first accented artist name.
`pipeline/__init__.py` forces stdout and stderr to UTF-8 on import; do not
remove it.

**`drawtext` segfaults in this ffmpeg build** — no default fontconfig. Burned
subtitles go through the `subtitles` filter and libass, which works. Do not
reach for `drawtext` for titles or lower-thirds; render them with Pillow.

**torch must be a cu128 build.** The 5080 is Blackwell, compute capability
12.0. Check with `torch.cuda.get_arch_list()` and look for `sm_120`.

**Two virtualenvs, deliberately.** `.venv-comfy` for ComfyUI, `.venv-pipeline`
for everything else. They pin conflicting transitive versions; merging them
means one breaks on every upgrade. Run pipeline modules with
`.venv-pipeline/Scripts/python.exe`.

**ComfyUI flags drift between versions.** `--normalvram` does not exist in
0.33; normal is the default. `--reserve-vram 1.5` matters, because the desktop
session already holds ~2.5 GB of a 16 GB card.

**`/object_info` has two encodings for dropdown options** — legacy
`[[a, b], {...}]` and current `["COMBO", {"options": [...]}]`. A server uses
both at once. `comfy_client.options_for` handles either; reading only one
makes a present model look missing.

---

## 5. Real images and licensing

`assets.py` fetches from Wikimedia Commons and the Met, both keyless. Use it
for anything a viewer could recognise: named artworks, historical photographs,
real places. A generated Mona Lisa is a lie with a melted face.

Results are sorted into tiers by `classify()`:

| tier | covers | usable |
|---|---|---|
| `safe` | public domain, CC0 | yes, no obligations |
| `attribution` | CC-BY | yes, credit required |
| `share_alike` | CC-BY-SA | excluded by default — the share-alike term can arguably reach the finished video |
| `rejected` | NC, ND, fair use, **unknown** | never |

Unknown is rejected, never assumed permissive. Being wrong that way costs a
generated image; being wrong the other way costs a takedown on a monetised
video.

`credits.md` is written automatically whenever any asset is used. Do not make
it optional.

One caveat the metadata cannot settle: a photograph *of* a public-domain 2D
painting is itself PD in the US (Bridgeman v. Corel) and Commons tags it PD-Art
on that basis; a few jurisdictions disagree. For flat old artwork this is
normal practice. For 3D objects and modern photographs the photographer's own
rights apply, and the tier is what governs.

**Everything shipped must stay commercially clean.** FLUX.1-schnell and
Kokoro-82M are Apache-2.0. FLUX.1-**dev** is better and is forbidden here — its
licence bars commercial use. Do not install it because it produces nicer
pictures.

---

## 6. Testing

```bash
.venv-pipeline/Scripts/python.exe -m tests.smoke            # required before shipping
.venv-pipeline/Scripts/python.exe -m tests.kenburns_probe   # after touching motion
.venv-pipeline/Scripts/python.exe -m tests.style_probe      # style comparison sheet
.venv-pipeline/Scripts/python.exe -m tests.contact_sheet <slug>   # review every frame
```

`tests/smoke.py` fabricates frames and silent narration, drives steps 2 and 4
for real, and asserts the finished file's duration matches the storyboard's
prediction. It needs no models and runs in about 30 seconds. Current baseline
is **9 ms drift** across 5 shots; if that number grows, the timeline
arithmetic broke.

There is no test for image quality, and there cannot be. `tests.contact_sheet`
tiles every frame of a project with its shot number so a 77-shot video can be
reviewed in three glances; a bad frame is then regenerated on its own with
`step3_frames <slug> --only 47 --force`. Look at the sheet. Every visual defect
found so far — ESRGAN oversharpening, oversized subtitles, mismatched asset
backdrops, mid-phrase subtitle breaks, fake body copy in generated frames —
was invisible to the test suite and obvious in a picture.

Show the sheet to the user rather than deciding alone what looks good.

---

## 7. Working habits that pay off here

**Measure before believing.** Two of the three biggest decisions in this
codebase went the opposite way from the standard advice, and both times a
fifteen-minute probe settled it. Build the probe.

**Check the metric before trusting the number.** The one time a measurement
looked alarming, the measurement was broken, not the code.

**Show pictures.** Rendering a contact sheet and looking at it caught the
ESRGAN artifacts, the oversized subtitles, the mismatched asset backdrops and
the mid-phrase cue breaks. None of those show up in a passing test.

**Prefer a small real run over a large careful plan.** The 8-shot
`roman-concrete` project exists to be re-run end to end after any change; it
takes about four minutes and exercises everything.

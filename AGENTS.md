# Working in this repository

Local pipeline that turns a written script into a narrated long-form video.
Everything runs on one machine: no cloud APIs, no per-minute billing.

Read this before changing anything. Most of it is the result of a measurement
that contradicted the obvious answer, and the obvious answer is what you will
reach for first.

---

## 1. The shape of the thing

Five steps. Each is independently runnable and resumable. They communicate
only through one file per project: `<video>/sources/storyboard.json`.

**Nothing a project produces lives in this repository.** Script, storyboard,
narration, frames and clips all sit in the video's own folder on the delivery
drive, under `sources`. `config.video_dir()` resolves a slug to that folder
and tolerates the release-order prefix the folders carry on disk
(`0_depression`, `1_chronicles`), so do not assume `DELIVERY_ROOT / slug`.

```
step 1   step1_script.py    narration text   ->  storyboard.json
step 2   step2_voice.py     Kokoro-82M       ->  audio/*.wav, narration.wav
step 3   step3_frames.py    FLUX / open collections  ->  frames/*.png
step 3b  review.py          a person, looking ->  review.json
step 4   step4_assemble.py  ffmpeg           ->  <slug>.mp4
step 5   publish.py         Pillow + ComfyUI ->  D:/TheArtOfChaosVideos/<slug>/
```

`pipeline/run.py` chains all five. `pipeline/schema.py` owns every data type
and is the file to read first.

Supporting modules: `kenburns.py` (camera motion), `subtitles.py`,
`outro.py` (end card), `assets.py` (real images + licensing),
`styles.py` (the three approved looks), `comfy_client.py`,
`workflows.py` (ComfyUI graphs), `ffmpeg_util.py`, `config.py` (all the knobs).

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

**`storyboard.json` is the only source of truth about a project.** The
`script.txt` and `prompts.txt` a project was built from are inputs, not
records: every prompt rewritten afterwards lives in the storyboard and nowhere
else. On the depression project they had drifted to 47 of 77 lines matching,
and reading the text file to work out why a frame looked wrong produced a page
of confident nonsense about frames that were in fact correct. Read the
storyboard, or `python -m tests.contact_sheet <slug>`, and never the input
files.

**Never edit the storyboard while a step is running.** That same
save-after-every-frame makes `storyboard.json` a last-writer-wins file with no
locking. A long render holds a copy loaded at start-up and writes all of it
back 77 times; anything edited in the meantime is silently gone. This ate a
full set of publish metadata — description, tags, chapters, thumbnail text —
and the loss only surfaced at the packaging step, which reported an empty
description for a video that had one written an hour earlier. Wait for the step
to finish, then edit, then re-run what needs re-running.

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

**Nothing is assembled out of frames nobody has looked at.** Step 4 refuses to
run until `review.json` exists and matches the frames on disk. Regenerate one
frame and the review goes stale again, naming the shot. This is a gate rather
than a good intention because every visual defect this project has shipped or
nearly shipped was invisible to the tests and obvious in a picture — see
section 6. `--skip-review` exists for `tests/smoke.py`, whose frames are
fabricated colour cards, and for nothing that gets published.

**Media paths in a storyboard are a cache, not the truth.** Steps record
absolute paths to audio, frames and clips, and a project folder can move — it
did, when deliveries started carrying their own sources. `Storyboard.load()`
therefore calls `rebase_paths()`, which repoints anything whose recorded path
has gone missing at `<project>/<sub>/<stem>` and drops it if there is nothing
there either. Without that, step 4 happily rebuilds a video out of a directory
that no longer exists.

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

### Frames are composed small and enlarged — asking for 3 MP breaks the picture

This one was wrong for a whole video, so read it before "optimising" it back.

FLUX is trained around a megapixel. Asked for more, it does not paint the same
scene with more pixels; it loses track of the scene and starts repeating and
fusing local structure. Same prompts, same seeds, five sizes:

| composed at | result |
|---|---|
| 1344x768 — 1.03 MP | correct |
| **1536x864 — 1.33 MP (current)** | **correct** |
| 1600x896 — 1.43 MP | correct |
| 1792x1024 — 1.84 MP | drifting: objects merge, spurious borders |
| 2048x1152 — 2.36 MP | wrong: stray duplicates, invented frames |
| 2304x1296 — 2.99 MP | broken |

At 2.99 MP a dozen keys around a keyhole came back as one melted mass, a valve
wheel as unreadable pulp, a seahorse fused into the calipers holding it. Every
one of them was correct at 1.03. This is not the model being weak at hard
prompts — it is the model being asked for a canvas it cannot hold.

So the model composes at `COMPOSE_W x COMPOSE_H` and the frame is resampled up
to `GEN_W x GEN_H` afterwards. 1536x864 is exactly 16:9 on a multiple of 16,
so the enlargement is a clean 1.5x with no reframing.

**Plain Lanczos, not ESRGAN.** At 1.5x the two are indistinguishable in a 1:1
crop, and the upscaler costs four times the wall clock because a second model
has to share a 16 GB card with a 16 GB checkpoint. (The old note here claimed
ESRGAN was "visibly oversharpened" at 30 s/frame. Re-measured: 15-30 s, wildly
variable from checkpoint churn, and not oversharpened on flat 2D art. It was
measured once on a different style and generalised.) `esrgan=True` remains for
material that needs real reconstruction rather than resampling.

| route | time/frame |
|---|---|
| **compose 1536x864 -> Lanczos -> 2304x1296 (current)** | **7.1 s** |
| direct 2304x1296 (old default, produced the broken frames) | 13.1 s |
| compose 1536x864 -> 4x ESRGAN -> 2304x1296 | 24-31 s |

Faster *and* correct, which is why the old measurement went unquestioned for
so long: it was a real measurement of the wrong quantity. It timed the routes
and never asked whether the picture was still right.

### The model cannot count, and naming a thing summons it

Two prompt-level failures that look like hallucination and are not.

**Counting.** Every numeric prompt in the depression batch failed: "six open
books" produced a bookshelf, "three brass vessels" two lamps, "four
overlapping circles" three, "two people" one. Drop the number and describe the
arrangement — "a row of identical cups, one of them filled with something
black" works where "three cups" does not.

**Naming.** A dozen frames came back signed — "Nzainful", "S0/20IG 1918" —
and some with a deckled paper border. The cause was in the style prompt:
`screen print texture`, `hand printed`, `subtle paper grain` describe a
*printed artefact*, and a print has a signature and an edition number, so the
model drew them. Removing those tokens removed the signatures.

Adding `unsigned` put them back. There is no way to ask for the absence of
something: the negative prompt is inert on schnell, and naming it in the
positive prompt is a request. Describe what should be there instead.

### A scene lands; a diagram does not

Measured across a batch of ten videos, 857 frames, all reviewed by eye. The
review rate was not evenly spread:

| video | subject | frames re-prompted |
|---|---|---|
| money, time, conspiracy | ideas with no physical form | 22-29% |
| gold, dragons, cities, clothes | things and places | 12-17% |
| salt, dark, fire | things and places | 10-11% |

Almost every rejected frame in the first group had the same shape of prompt:
"a flat graphic band where a marker steps further along on each cycle", "a
stylised composition of exchange, accounting and storage". What came back was
a pleasant abstract landscape with no relation to the idea.

The style prompt already supplies the flatness, the palette and the graphic
treatment. When the shot prompt *also* describes an abstraction, nothing in
the whole prompt names a thing that exists, and the model falls back on
landscape — which is what a diffusion model does with an under-determined
prompt. "An ancient Greek astronomer's desk with a marked bronze ring and
brass dividers" carries exactly the same idea and lands every time.

Write the scene. Let the narration carry the abstraction.

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
.venv-pipeline/Scripts/python.exe -m pipeline.review <slug> # required before assembly
.venv-pipeline/Scripts/python.exe -m tests.kenburns_probe   # after touching motion
.venv-pipeline/Scripts/python.exe -m tests.style_probe      # style comparison sheet
.venv-pipeline/Scripts/python.exe -m tests.contact_sheet <slug>   # sheets on their own
```

`tests/smoke.py` fabricates frames and silent narration, drives steps 2 and 4
for real, and asserts the finished file's duration matches the storyboard's
prediction. It needs no models and runs in about 30 seconds. Current baseline
is **24 ms drift** across 5 shots and an end card; if that number grows, the
timeline arithmetic broke.

**Step 3b is where the looking happens.** `python -m pipeline.review <slug>`
runs the checks that can be mechanised — the STYLE.md prompt rules as regexes,
frames whose edge energy says they came back nearly empty, assets too small to
hold the screen — then builds the contact sheets and stops. `--accept` records
that a person looked, against a fingerprint of the frames. The mechanical
flags are flags, not errors: a rule can be broken deliberately, and the
`--note` is where you say why.

What it cannot do is judge a picture, which is the point of the stage.

There is no test for image quality, and there cannot be. `tests.contact_sheet`
tiles every frame of a project with its shot number so a 77-shot video can be
reviewed in three glances; a bad frame is then regenerated on its own with
`step3_frames <slug> --only 47 --force`. Look at the sheet. Every visual defect
found so far — oversized subtitles, mismatched asset backdrops, mid-phrase
subtitle breaks, fake body copy, invented signatures, and a whole video's
worth of frames broken by generating above the resolution ceiling — was
invisible to the test suite and obvious in a picture.

The resolution one is the cautionary tale: it survived a full render, an
assembly, a delivery and a review, because the frames were individually
plausible and only wrong against the prompt that asked for them. Read the
prompt beside the picture, not just the picture.

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

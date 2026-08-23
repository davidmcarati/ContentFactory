# Content Factory

Local pipeline for long-form narrated video. Script in, finished MP4 out, no
cloud services and no per-minute billing.

Built and measured on: RTX 5080 (16 GB), i9-14900K, 64 GB RAM, Windows 11.

## The idea

Four steps, each resumable, all talking to one another through a single file:
`projects/<slug>/storyboard.json`.

```
step 1  script      narration text        ->  storyboard.json
step 2  voice       Kokoro-82M            ->  audio/*.wav + narration.wav
step 3  frames      FLUX.1-schnell, or a  ->  frames/*.png + credits.md
                    real image from an
                    open collection
step 4  assemble    ffmpeg                ->  <slug>.mp4
```

Two design decisions are worth knowing before changing anything:

**Voice runs before pictures.** A shot lasts exactly as long as its narration
takes to speak. That length is measured from the rendered audio, never
estimated from word count, so the pictures are cut to the voice rather than the
other way round.

**Every step writes its results back to the storyboard immediately.** Killing
step 3 at shot 60 of 90 and restarting costs one shot, not sixty.

## Setup

Already done in this working copy. To rebuild from scratch:

```bash
py -3.12 -m venv .venv-comfy && py -3.12 -m venv .venv-pipeline
.venv-comfy/Scripts/python.exe -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128
.venv-comfy/Scripts/python.exe -m pip install -r comfy/requirements.txt
.venv-pipeline/Scripts/python.exe -m pip install torch --index-url https://download.pytorch.org/whl/cu128
.venv-pipeline/Scripts/python.exe -m pip install kokoro soundfile numpy requests pillow faster-whisper
```

Two virtualenvs on purpose: ComfyUI wants the newest torch, the TTS and
alignment stack pins older transitive versions, and sharing one environment
means one of them breaks on every upgrade. Disk is cheaper than that.

The cu128 wheels matter. The 5080 is Blackwell, compute capability 12.0, and
only torch builds carrying `sm_120` will drive it. Verify with:

```bash
.venv-comfy/Scripts/python.exe -c "import torch; print(torch.cuda.get_arch_list())"
```

Models live on `D:/ai-models` via `comfy/extra_model_paths.yaml`, so a fresh
ComfyUI checkout never costs a redownload.

| file | size | licence |
|---|---|---|
| `checkpoints/flux1-schnell-fp8.safetensors` | 16 GB | Apache-2.0 |
| `upscale_models/4x-UltraSharpV2.safetensors` | 134 MB | optional, off by default |

Kokoro-82M (Apache-2.0) downloads itself on first run.

## Running

Start the image backend and leave it up:

```bash
scripts/start_comfy.cmd
```

Then, per project:

```bash
.venv-pipeline/Scripts/python.exe -m pipeline.step1_script new my-slug --title "..." --style midcentury --narration script.txt --prompts prompts.txt
.venv-pipeline/Scripts/python.exe -m pipeline.run my-slug
```

Useful variants:

```bash
# the three approved styles, and when to use each
... -m pipeline.styles

# see how the narration chunks into shots before committing to prompts
... step1_script new my-slug ... --narration script.txt --dry-run

# time the whole cut with silent placeholder audio, no models needed
... -m pipeline.run my-slug --fake-voice

# redo one bad frame, keeping its seed reproducible
... -m pipeline.step3_frames my-slug --only 47 --force

# re-cut without regenerating anything
... -m pipeline.run my-slug --only assemble
```

## Measured performance

Frames are delivered at 2304x1296 but composed at 1536x864, steady state after
the checkpoint is resident:

| route | time/frame |
|---|---|
| **compose 1536x864 -> Lanczos -> 2304x1296 (current)** | **7.1 s** |
| generate 2304x1296 directly | 13.1 s |
| compose 1536x864 -> 4x ESRGAN -> 2304x1296 | 24-31 s |

Generating straight at 2304x1296 is not just slower, it is wrong: 2.99 MP is
three times FLUX's training resolution, and above about 1.5 MP the model stops
composing a scene and starts fusing and duplicating local structure. It shipped
a video that way before anyone measured it. AGENTS.md has the size ladder and
the side-by-side.

Camera motion does not use ffmpeg's `zoompan`, which rounds its crop window to
whole pixels and makes the camera appear to shake partway through a shot.
`kenburns.py` crops with a float box in Pillow instead:

| method | shake |
|---|---|
| zoompan, 2x oversampled source | 0.186 |
| zoompan, 4x oversampled source | 0.264 |
| **Pillow float crop (current)** | **0.035** |

Oversampling is the intuitive fix and it does not work -- the rounding is in
the window position, not the sampling. Probe: `tests/kenburns_probe.py`.

For a 12-minute video, roughly 90 shots:

| stage | time |
|---|---|
| narration (Kokoro, GPU) | under 1 min |
| frames (90 x 7.1 s) | ~11 min |
| clips + mux (ffmpeg, CPU) | ~8 min |
| **total** | **~20 min**, mostly unattended |

Narration pace measured at 146 words/minute, so 12 minutes is about 1750 words.

## Real images alongside generated ones

Some shots should not be invented. A generated Mona Lisa is a lie with a melted
face; the real one is public domain and free. Mark those shots `"kind":
"asset"` with a search query and step 3 fetches instead of generating.

```bash
# look at what is available and how it is licensed, before committing
.venv-pipeline/Scripts/python.exe -m pipeline.assets "Vitruvian Man Leonardo"
```

Sources are Wikimedia Commons and the Met, both keyless. Only public-domain,
CC0 and CC-BY results are used; NC, ND and anything with unrecognised licence
metadata are rejected outright, and CC-BY-SA is excluded by default because the
share-alike term can arguably reach the finished video. `credits.md` is written
automatically. Portrait images are fitted over a darkened blur of themselves
rather than cropped, so nobody's head gets cut off.

Fetching is also *faster* than generating -- about 1 second against 13 -- and
step 3 does all the fetches before any generation, so ComfyUI is not made to
drop and reload a 16 GB checkpoint in between.

## Known limits

**Character consistency is not solved.** Every frame is generated
independently. A shared style prompt and a deterministic seed keep the *look*
coherent, which is enough for illustrative documentary material. A recurring
human character will have a different face in every shot. Fixing that means a
style LoRA, IP-Adapter reference conditioning, or generating from one master
frame with an image-edit model.

**Subtitle timing within a shot is proportional, not aligned.** A long line
pages through several cues and each gets a share of the shot's audio in
proportion to its length. It drifts by well under the length of a cue, but
word-accurate karaoke timing needs forced alignment; `faster-whisper` is
installed for that and not yet wired in.

**The final mux builds one xfade chain across all clips.** It has been run at
8 shots. At 90 it opens 90 inputs at once, which ffmpeg handles but not
gracefully. If it becomes a problem, concatenate in chunks.

**`drawtext` does not work** in this ffmpeg build -- no fontconfig default
config, it segfaults. Burned-in subtitles go through the `subtitles` filter and
libass instead, which works fine.

**No background music bed.** `assets/music/` exists and nothing reads it yet.

## Licensing

Deliberately commercial-clean throughout: FLUX.1-schnell and Kokoro-82M are
both Apache-2.0. FLUX.1-**dev** is the more capable and better-supported model
but its licence forbids commercial use, so it is not installed here. Qwen-Image
(also Apache-2.0) is the quality upgrade path, at roughly 28 GB of weights and
heavy CPU offload on a 16 GB card.

## Tests

```bash
.venv-pipeline/Scripts/python.exe -m tests.smoke
```

Fabricates synthetic frames and silent narration and drives steps 2 and 4 for
real, then checks that the finished file's duration matches the storyboard's
prediction. It needs no models and catches the failure that matters most:
audio and video timelines drifting apart.

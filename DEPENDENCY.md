# Dependencies

Everything this pipeline needs, why that particular version, and how to install
it from nothing. Nothing here is vendored into the repository: the models alone
are about 17 GB and the ComfyUI checkout is a clone, not a copy.

Reference machine: RTX 5080 (16 GB), i9-14900K, 64 GB RAM, Windows 11.

---

## 1. System

| tool | version used | notes |
|---|---|---|
| Python | **3.12** | 3.13 is installed on this machine and is the default `python`; do not use it. Parts of the TTS stack still fail to build wheels on 3.13. |
| ffmpeg | 8.1.2 (Gyan full build) | `winget install Gyan.FFmpeg`. Must be on PATH. |
| git | any | |
| NVIDIA driver | 596.49 (CUDA 13.2) | Needs to be new enough for Blackwell. |
| GitHub CLI | 2.93 | Only for repository work, not for the pipeline. |

Verify Python 3.12 is present:

```bash
py -0p
```

## 2. Virtual environments

Two, deliberately. ComfyUI tracks the newest torch; the TTS and alignment stack
pins older transitive versions of `transformers`, `numpy` and `tokenizers`.
Sharing one environment means one of them breaks on every upgrade, and the
duplicated torch install is cheaper than that.

```bash
py -3.12 -m venv .venv-comfy
py -3.12 -m venv .venv-pipeline
```

### .venv-comfy — the image backend

```bash
.venv-comfy/Scripts/python.exe -m pip install --upgrade pip setuptools wheel
.venv-comfy/Scripts/python.exe -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128
.venv-comfy/Scripts/python.exe -m pip install -r comfy/requirements.txt
```

### .venv-pipeline — everything else

```bash
.venv-pipeline/Scripts/python.exe -m pip install --upgrade pip setuptools wheel
.venv-pipeline/Scripts/python.exe -m pip install torch --index-url https://download.pytorch.org/whl/cu128
.venv-pipeline/Scripts/python.exe -m pip install kokoro soundfile numpy requests pillow faster-whisper
```

| package | used for |
|---|---|
| `torch` (cu128) | Kokoro inference |
| `kokoro` | text to speech; pulls `misaki`, `espeakng-loader`, `spacy` |
| `soundfile`, `numpy` | building the narration track |
| `pillow` | Ken Burns motion, end card, thumbnails, fitting fetched images |
| `requests` | ComfyUI API, Wikimedia and Met APIs |
| `faster-whisper` | installed for forced alignment; not wired in yet |

**The cu128 index URL is not optional.** The 5080 is Blackwell, compute
capability 12.0. A default-index torch wheel will install and then refuse to
see the card. Confirm:

```bash
.venv-comfy/Scripts/python.exe -c "import torch; print(torch.cuda.get_arch_list())"
```

`sm_120` must appear in the list. Observed good result:
`torch 2.11.0+cu128`, arch list `['sm_75','sm_80','sm_86','sm_90','sm_100','sm_120']`.

## 3. ComfyUI

Cloned, not vendored, and ignored by git:

```bash
git clone --depth 1 https://github.com/comfyanonymous/ComfyUI.git comfy
```

Version in use: **0.33.0**. Flags drift between releases — `--normalvram` does
not exist in 0.33, and normal is the default anyway. Start it with
`scripts/start_comfy.cmd`, which passes `--reserve-vram 1.5` because the
desktop session already holds ~2.5 GB of the 16 GB card.

`comfy/extra_model_paths.yaml` is committed and points ComfyUI at `D:/ai-models`,
so re-cloning ComfyUI never costs a model redownload.

## 4. Models

All on `D:/ai-models`. Total about 17 GB.

| file | location | size | licence |
|---|---|---|---|
| `flux1-schnell-fp8.safetensors` | `checkpoints/` | 16.0 GB | Apache-2.0 |
| `4x-UltraSharpV2.safetensors` | `upscale_models/` | 134 MB | optional, off by default |
| Kokoro-82M | Hugging Face cache | ~350 MB | Apache-2.0 |
| `en_core_web_sm` | pip | 12.8 MB | MIT, pulled automatically by Kokoro |

```bash
.venv-pipeline/Scripts/python.exe -c "from huggingface_hub import hf_hub_download; hf_hub_download('Comfy-Org/flux1-schnell','flux1-schnell-fp8.safetensors',local_dir='D:/ai-models/checkpoints')"
.venv-pipeline/Scripts/python.exe -c "from huggingface_hub import hf_hub_download; hf_hub_download('Kim2091/UltraSharpV2','4x-UltraSharpV2.safetensors',local_dir='D:/ai-models/upscale_models')"
```

Kokoro-82M and the spaCy model download themselves on the first run of step 2.

### Why these models and not the better ones

**FLUX.1-schnell, not FLUX.1-dev.** dev is more capable, better supported and
has more LoRAs. Its licence forbids commercial use. This channel is monetised,
so dev is not installed, and should not be installed "just to compare".

**Qwen-Image is the upgrade path** and is also Apache-2.0, but it is a 20B
model: 19 GB for the transformer plus 8.7 GB for the text encoder, against
13.5 GB of usable VRAM. It runs with heavy CPU offload and is far slower than
7 s per frame. Worth revisiting for hero shots only.

**The upscaler is installed but off by default.** Frames are composed at
1536x864 and resampled to 2304x1296 with Lanczos (7.1 s/frame). A 4x ESRGAN in
that slot costs 24-31 s and is indistinguishable in a 1:1 crop at this
enlargement, because a second model has to share the card with a 16 GB
checkpoint. `esrgan=True` in `workflows.py` exists for material that needs real
reconstruction rather than resampling.

## 5. Fonts

The end card and thumbnails are drawn with Pillow — ffmpeg's `drawtext`
segfaults in this build for want of a fontconfig default. Fonts are looked up
in `assets/fonts/` first, then `C:/Windows/Fonts`.

Currently used: `segoeuib.ttf` (Segoe UI Bold), falling back to `arialbd.ttf`.
Both ship with Windows. To use a licensed brand font, drop the `.ttf` into
`assets/fonts/` and set `OUTRO_FONT` / `THUMB_FONT` in `config.py`.

Burned-in subtitles go through libass, which finds fonts by name; `SUB_FONT`
is a family name, not a filename.

## 6. External services

No API keys anywhere. Both image sources are keyless and rate-limited only by
politeness:

| service | used for | terms |
|---|---|---|
| Wikimedia Commons API | licensed image search | send a real User-Agent, which `assets.py` does |
| Met Museum Collection API | CC0 artwork | open, no key |
| Hugging Face Hub | model downloads | anonymous works; set `HF_TOKEN` for faster downloads and higher limits |

## 7. Verifying an installation

```bash
.venv-comfy/Scripts/python.exe -c "import torch; print(torch.cuda.get_device_name(0), torch.cuda.get_arch_list())"
scripts/start_comfy.cmd                                        # leave running
.venv-pipeline/Scripts/python.exe -m tests.smoke               # no models needed
.venv-pipeline/Scripts/python.exe -m pipeline.run roman-concrete --only frames
```

`tests/smoke.py` passing means the timeline arithmetic is intact. The
`roman-concrete` run means the model, ComfyUI and the API client all agree.

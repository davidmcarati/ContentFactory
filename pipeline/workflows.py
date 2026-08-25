"""ComfyUI workflow graphs, built in code rather than exported from the UI.

An exported workflow is a snapshot of one person's node layout; six months
later it fails validation because a node gained an input. Building the graph
from a function keeps the whole thing in one screen, makes the seed and prompt
obvious, and means `verify_backend` can check every assumption against a live
server before a 100-shot batch starts.

The graph format is ComfyUI's API format: a flat dict of node id -> class name
plus inputs, where a link is [source_node_id, output_index].
"""
from __future__ import annotations

from typing import Any

from . import config

Graph = dict[str, dict[str, Any]]

FLUX_SCHNELL_CKPT = "flux1-schnell-fp8.safetensors"
UPSCALER = "4x-UltraSharpV2.safetensors"

QWEN_UNET = "qwen-image-Q4_K_M.gguf"
QWEN_CLIP = "qwen_2.5_vl_7b_fp8_scaled.safetensors"
QWEN_VAE = "qwen_image_vae.safetensors"

CHROMA_UNET = "Chroma1-HD-Q8_0.gguf"
CHROMA_CLIP = "t5xxl_fp8_e4m3fn_scaled.safetensors"
FLUX_VAE = "ae.safetensors"

# Which input on each loader names a file on disk. Used by verify_backend to
# check a graph's files without knowing which model built it -- the previous
# version hard-coded the FLUX checkpoint and would have stayed silent about a
# missing Qwen quant.
LOADER_FIELDS = {
    "CheckpointLoaderSimple": "ckpt_name",
    "UnetLoaderGGUF": "unet_name",
    "CLIPLoader": "clip_name",
    "VAELoader": "vae_name",
    "UpscaleModelLoader": "model_name",
}


def _finish(g: Graph, source: str, *, width: int, height: int,
            target_w: int, target_h: int, esrgan: bool,
            filename_prefix: str) -> Graph:
    """Shared tail: optional enlargement, then save.

    Every model composes small and is resampled up, so this is the same three
    nodes each time. `source` is the id of the node producing the IMAGE.
    """
    tail = [source, 0]
    if esrgan:
        g["7"] = {"class_type": "UpscaleModelLoader",
                  "inputs": {"model_name": UPSCALER}}
        g["8"] = {"class_type": "ImageUpscaleWithModel",
                  "inputs": {"upscale_model": ["7", 0], "image": [source, 0]}}
        g["9"] = {"class_type": "ImageScale",
                  "inputs": {"image": ["8", 0], "width": target_w,
                             "height": target_h, "upscale_method": "lanczos",
                             "crop": "center"}}
        tail = ["9", 0]
    elif (width, height) != (target_w, target_h):
        g["11"] = {"class_type": "ImageScale",
                   "inputs": {"image": [source, 0], "width": target_w,
                              "height": target_h, "upscale_method": "lanczos",
                              "crop": "disabled"}}
        tail = ["11", 0]

    g["10"] = {"class_type": "SaveImage",
               "inputs": {"images": tail, "filename_prefix": filename_prefix}}
    return g


def flux_schnell(
    prompt: str,
    negative: str,
    seed: int,
    *,
    width: int = config.GEN_W,
    height: int = config.GEN_H,
    steps: int = 4,
    compose: bool = True,
    esrgan: bool = False,
    filename_prefix: str = "cf",
) -> Graph:
    """FLUX.1-schnell, Apache-2.0, four steps.

    schnell is guidance-distilled: cfg must stay at 1.0 and the negative prompt
    does essentially nothing. It is wired up anyway so the same graph shape
    works if the model is swapped for one that does use it.

    `width`/`height` are the size of the frame you get back. The model is not
    asked for them directly: it composes at COMPOSE_W x COMPOSE_H and the
    result is resampled up. Asking schnell for 2.99 MP in one go returns a
    broken picture, not a big one -- see AGENTS.md for the side-by-side.
    Composition is settled where the model is competent; the remaining 1.44x
    is arithmetic.

    Plain Lanczos, not ESRGAN. At 1.44x the two are indistinguishable in a 1:1
    crop and the upscaler costs 4x the time, because a second model has to
    share a 16 GB card with a 16 GB checkpoint. `esrgan=True` is still there
    for source material that needs real reconstruction.

    `compose=False` renders at `width` x `height` directly. That is what the
    probes use to demonstrate the problem; it is not a production setting.
    """
    target_w, target_h = width, height
    if compose:
        width, height = config.COMPOSE_W, config.COMPOSE_H

    g: Graph = {
        "1": {"class_type": "CheckpointLoaderSimple",
              "inputs": {"ckpt_name": FLUX_SCHNELL_CKPT}},
        "2": {"class_type": "CLIPTextEncode",
              "inputs": {"text": prompt, "clip": ["1", 1]}},
        "3": {"class_type": "CLIPTextEncode",
              "inputs": {"text": negative, "clip": ["1", 1]}},
        "4": {"class_type": "EmptySD3LatentImage",
              "inputs": {"width": width, "height": height, "batch_size": 1}},
        "5": {"class_type": "KSampler",
              "inputs": {
                  "seed": seed,
                  "steps": steps,
                  "cfg": 1.0,
                  "sampler_name": "euler",
                  "scheduler": "simple",
                  "denoise": 1.0,
                  "model": ["1", 0],
                  "positive": ["2", 0],
                  "negative": ["3", 0],
                  "latent_image": ["4", 0],
              }},
        "6": {"class_type": "VAEDecode",
              "inputs": {"samples": ["5", 0], "vae": ["1", 2]}},
    }

    # 4x UltraSharp overshoots (1600 -> 6400) when esrgan is on, so the tail
    # comes back down to the size actually asked for. Downsampling after
    # reconstruction is what keeps the linework clean instead of oversharpened.
    return _finish(g, "6", width=width, height=height, target_w=target_w,
                   target_h=target_h, esrgan=esrgan,
                   filename_prefix=filename_prefix)


def qwen_image(
    prompt: str,
    negative: str,
    seed: int,
    *,
    width: int = config.GEN_W,
    height: int = config.GEN_H,
    steps: int = 20,
    cfg: float = 2.5,
    shift: float = 3.1,
    compose: bool = True,
    esrgan: bool = False,
    filename_prefix: str = "cf",
) -> Graph:
    """Qwen-Image, Apache-2.0, ~20B MMDiT, run from a Q4_K_M quant.

    Two things it has that schnell does not.

    It is not guidance-distilled, so cfg is real and `negative` is finally a
    working lever rather than dead wiring.

    It renders legible text. Every failure of the form MOLNTUR, "Weelay /
    Dicky / Gunday" or COS'TOM HOUSE came from asking a model that cannot
    write for a picture with writing in it. If this model holds up, the
    STYLE.md rule against naming charts, calendars and signage is no longer a
    law of nature and should be re-measured rather than inherited.

    Q4_K_M is the largest quant that leaves headroom on a 16 GB card: 12.2 GB
    of weights against 14.5 GB usable after ComfyUI's reserve. The text
    encoder is a separate 8.7 GB file, but ComfyUI runs it and unloads it
    before the sampler starts, so peak VRAM is the larger of the two rather
    than the sum -- which is the whole reason this fits at all.
    """
    target_w, target_h = width, height
    if compose:
        width, height = config.COMPOSE_W, config.COMPOSE_H

    g: Graph = {
        "1": {"class_type": "UnetLoaderGGUF",
              "inputs": {"unet_name": QWEN_UNET}},
        "12": {"class_type": "CLIPLoader",
               "inputs": {"clip_name": QWEN_CLIP, "type": "qwen_image"}},
        "13": {"class_type": "VAELoader",
               "inputs": {"vae_name": QWEN_VAE}},
        "2": {"class_type": "CLIPTextEncode",
              "inputs": {"text": prompt, "clip": ["12", 0]}},
        "3": {"class_type": "CLIPTextEncode",
              "inputs": {"text": negative, "clip": ["12", 0]}},
        "4": {"class_type": "EmptySD3LatentImage",
              "inputs": {"width": width, "height": height, "batch_size": 1}},
        "14": {"class_type": "ModelSamplingAuraFlow",
               "inputs": {"model": ["1", 0], "shift": shift}},
        "5": {"class_type": "KSampler",
              "inputs": {
                  "seed": seed,
                  "steps": steps,
                  "cfg": cfg,
                  "sampler_name": "euler",
                  "scheduler": "simple",
                  "denoise": 1.0,
                  "model": ["14", 0],
                  "positive": ["2", 0],
                  "negative": ["3", 0],
                  "latent_image": ["4", 0],
              }},
        "6": {"class_type": "VAEDecode",
              "inputs": {"samples": ["5", 0], "vae": ["13", 0]}},
    }
    return _finish(g, "6", width=width, height=height, target_w=target_w,
                   target_h=target_h, esrgan=esrgan,
                   filename_prefix=filename_prefix)


def chroma(
    prompt: str,
    negative: str,
    seed: int,
    *,
    width: int = config.GEN_W,
    height: int = config.GEN_H,
    steps: int = 26,
    cfg: float = 4.0,
    compose: bool = True,
    esrgan: bool = False,
    filename_prefix: str = "cf",
) -> Graph:
    """Chroma1-HD, Apache-2.0, ~8.9B, de-distilled from FLUX.1-schnell.

    The interesting property is what the de-distillation gives back: real
    classifier-free guidance. schnell runs at cfg 1.0 by construction, which
    is why `style.negative` has never done anything in this project. Here it
    does.

    Smaller than Qwen, so a Q8_0 quant -- near-lossless -- still fits, where
    Qwen has to drop to Q4. Whether 8.9B at Q8 beats 20B at Q4 is exactly the
    question the bake-off exists to answer, and it is not obvious either way.

    FLUX architecture, so it wants T5-XXL alone (Chroma dropped CLIP-L) and
    the FLUX autoencoder. Both are separate files here rather than baked into
    a checkpoint the way flux1-schnell-fp8 is.
    """
    target_w, target_h = width, height
    if compose:
        width, height = config.COMPOSE_W, config.COMPOSE_H

    g: Graph = {
        "1": {"class_type": "UnetLoaderGGUF",
              "inputs": {"unet_name": CHROMA_UNET}},
        "12": {"class_type": "CLIPLoader",
               "inputs": {"clip_name": CHROMA_CLIP, "type": "chroma"}},
        "13": {"class_type": "VAELoader",
               "inputs": {"vae_name": FLUX_VAE}},
        "2": {"class_type": "CLIPTextEncode",
              "inputs": {"text": prompt, "clip": ["12", 0]}},
        "3": {"class_type": "CLIPTextEncode",
              "inputs": {"text": negative, "clip": ["12", 0]}},
        "4": {"class_type": "EmptySD3LatentImage",
              "inputs": {"width": width, "height": height, "batch_size": 1}},
        "5": {"class_type": "KSampler",
              "inputs": {
                  "seed": seed,
                  "steps": steps,
                  "cfg": cfg,
                  "sampler_name": "euler",
                  "scheduler": "simple",
                  "denoise": 1.0,
                  "model": ["1", 0],
                  "positive": ["2", 0],
                  "negative": ["3", 0],
                  "latent_image": ["4", 0],
              }},
        "6": {"class_type": "VAEDecode",
              "inputs": {"samples": ["5", 0], "vae": ["13", 0]}},
    }
    return _finish(g, "6", width=width, height=height, target_w=target_w,
                   target_h=target_h, esrgan=esrgan,
                   filename_prefix=filename_prefix)


MODELS = {
    "flux-schnell": flux_schnell,
    "qwen": qwen_image,
    "chroma": chroma,
}

# The 1.5 MP composition ceiling was measured on FLUX.1-schnell and applies to
# the FLUX architecture. Qwen is a different model trained at a different
# resolution; inheriting the number without measuring would repeat exactly the
# mistake that shipped a video composed above the ceiling.
FLUX_FAMILY = {"flux-schnell", "chroma"}


def build(model: str, **kwargs: Any) -> Graph:
    if model not in MODELS:
        raise KeyError(
            f"unknown model {model!r}; storyboard style.model must be one of "
            f"{sorted(MODELS)}"
        )
    return MODELS[model](**kwargs)


# ---------------------------------------------------------------------------
def verify_backend(client, model: str = "flux-schnell", **kwargs: Any) -> list[str]:
    """Check a live ComfyUI has the nodes and files this graph needs.

    Worth doing before a batch rather than after: a missing checkpoint surfaces
    as a validation error on shot 1, but a missing *upscaler* surfaces on shot
    1 too -- and both are far cheaper to find here than 40 minutes in.
    """
    problems: list[str] = []
    graph = build(model, prompt="probe", negative="", seed=1, **kwargs)

    # The expensive mistake this codebase has actually made. Composing above
    # the measured ceiling does not fail, it quietly returns wrong pictures,
    # and nobody notices until a 77-frame batch is on the contact sheet.
    megapixels = config.COMPOSE_W * config.COMPOSE_H / 1e6
    if model in FLUX_FAMILY and megapixels > 1.5:
        problems.append(
            f"COMPOSE_W x COMPOSE_H is {megapixels:.2f} MP. Above ~1.5 MP "
            f"FLUX stops composing a scene and starts fusing and duplicating "
            f"structure -- see AGENTS.md. Compose smaller and enlarge."
        )

    present = client.object_info()
    for node_id, node in graph.items():
        cls = node["class_type"]
        if cls not in present:
            problems.append(f"node {cls} (id {node_id}) is not installed")

    # Check the files this graph actually loads, whichever model built it.
    # Reading the filenames off the graph rather than listing them here means
    # a new model cannot be added with its files left unverified.
    for node_id, node in graph.items():
        cls = node["class_type"]
        field = LOADER_FIELDS.get(cls)
        if not field or cls not in present:
            continue
        wanted = node["inputs"].get(field)
        found = client.options_for(cls, field)
        if wanted and wanted not in found:
            problems.append(
                f"{cls} (id {node_id}) wants {wanted}, which ComfyUI cannot "
                f"see; it offers {found or 'nothing'}"
            )

    return problems

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

    tail = ["6", 0]
    if esrgan:
        g["7"] = {"class_type": "UpscaleModelLoader",
                  "inputs": {"model_name": UPSCALER}}
        g["8"] = {"class_type": "ImageUpscaleWithModel",
                  "inputs": {"upscale_model": ["7", 0], "image": ["6", 0]}}
        # 4x UltraSharp overshoots (1600 -> 6400), so come back down to the
        # size actually asked for. Downsampling after reconstruction is what
        # keeps the linework clean instead of oversharpened.
        g["9"] = {"class_type": "ImageScale",
                  "inputs": {
                      "image": ["8", 0],
                      "width": target_w,
                      "height": target_h,
                      "upscale_method": "lanczos",
                      "crop": "center",
                  }}
        tail = ["9", 0]
    elif (width, height) != (target_w, target_h):
        g["11"] = {"class_type": "ImageScale",
                   "inputs": {
                       "image": ["6", 0],
                       "width": target_w,
                       "height": target_h,
                       "upscale_method": "lanczos",
                       "crop": "disabled",
                   }}
        tail = ["11", 0]

    g["10"] = {"class_type": "SaveImage",
               "inputs": {"images": tail, "filename_prefix": filename_prefix}}
    return g


MODELS = {
    "flux-schnell": flux_schnell,
}


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

    present = client.object_info()
    used = {node["class_type"] for node in graph.values()}
    for node_id, node in graph.items():
        cls = node["class_type"]
        if cls not in present:
            problems.append(f"node {cls} (id {node_id}) is not installed")

    # Only check files the graph actually loads. The server always reports
    # UpscaleModelLoader as available, so checking it unconditionally would
    # fail a perfectly good setup that simply does not upscale.
    for node_cls, field, wanted, kind in (
        ("CheckpointLoaderSimple", "ckpt_name", FLUX_SCHNELL_CKPT, "checkpoint"),
        ("UpscaleModelLoader", "model_name", UPSCALER, "upscaler"),
    ):
        if node_cls not in used or node_cls not in present:
            continue
        found = client.options_for(node_cls, field)
        if wanted not in found:
            problems.append(
                f"{kind} {wanted} not visible to ComfyUI; it sees "
                f"{found or 'nothing'}"
            )

    return problems

"""Minimal ComfyUI HTTP client.

ComfyUI's API takes a workflow graph, queues it, and hands back a prompt id.
We poll `/history` rather than opening the websocket: for batch rendering there
is no interactive progress to stream, and polling has far fewer ways to fail
silently halfway through a 120-frame run.
"""
from __future__ import annotations

import json
import time
import urllib.parse
import uuid
from pathlib import Path
from typing import Any

import requests

from . import config


class ComfyError(RuntimeError):
    pass


class ComfyClient:
    def __init__(self, url: str = config.COMFY_URL, timeout: float = 10.0):
        self.url = url.rstrip("/")
        self.timeout = timeout
        self.client_id = str(uuid.uuid4())

    # -- health ---------------------------------------------------------
    def is_up(self) -> bool:
        try:
            requests.get(f"{self.url}/system_stats", timeout=2).raise_for_status()
            return True
        except requests.RequestException:
            return False

    def require_up(self) -> None:
        if not self.is_up():
            raise ComfyError(
                f"ComfyUI is not answering on {self.url}.\n"
                f"Start it with:  scripts/start_comfy.cmd"
            )

    def system_stats(self) -> dict[str, Any]:
        r = requests.get(f"{self.url}/system_stats", timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    # -- introspection --------------------------------------------------
    def object_info(self, node: str | None = None) -> dict[str, Any]:
        """What nodes this install actually has, and what inputs they take.

        Worth querying instead of trusting a workflow copied from a tutorial:
        node signatures drift between ComfyUI versions and a stale graph fails
        with an unhelpful validation error.
        """
        path = f"/object_info/{node}" if node else "/object_info"
        r = requests.get(f"{self.url}{path}", timeout=30)
        r.raise_for_status()
        return r.json()

    def has_nodes(self, *names: str) -> dict[str, bool]:
        info = self.object_info()
        return {n: n in info for n in names}

    def options_for(self, node: str, field: str) -> list[str]:
        """The dropdown choices a loader node offers -- i.e. which model files
        ComfyUI can actually see on disk.

        Two encodings are in the wild and a given server can use both at once:
        the legacy `[[opt, opt], {...}]` and the current
        `["COMBO", {"options": [opt, opt]}]`. Reading only one of them makes a
        present model look missing, so handle either.
        """
        info = self.object_info(node).get(node, {})
        spec = info.get("input", {}).get("required", {}).get(field)
        if not spec:
            return []
        if isinstance(spec[0], list):
            return list(spec[0])
        if len(spec) > 1 and isinstance(spec[1], dict):
            return list(spec[1].get("options", []))
        return []

    # -- queueing -------------------------------------------------------
    def queue(self, workflow: dict[str, Any]) -> str:
        payload = {"prompt": workflow, "client_id": self.client_id}
        r = requests.post(f"{self.url}/prompt", json=payload, timeout=self.timeout)
        if r.status_code != 200:
            raise ComfyError(_explain_rejection(r))
        return r.json()["prompt_id"]

    def wait(self, prompt_id: str, *, timeout: float = 900.0,
             poll: float = 1.0) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            r = requests.get(f"{self.url}/history/{prompt_id}", timeout=self.timeout)
            r.raise_for_status()
            hist = r.json().get(prompt_id)
            if hist:
                status = hist.get("status", {})
                if status.get("status_str") == "error":
                    raise ComfyError(
                        f"workflow failed:\n{json.dumps(status, indent=2)[:2000]}"
                    )
                if status.get("completed"):
                    return hist
            time.sleep(poll)
        raise ComfyError(f"prompt {prompt_id} did not finish within {timeout:.0f}s")

    # -- results --------------------------------------------------------
    def images_from(self, history: dict[str, Any]) -> list[dict[str, str]]:
        out = []
        for node_out in history.get("outputs", {}).values():
            out.extend(node_out.get("images", []))
        return out

    def download(self, image: dict[str, str], dest: Path) -> Path:
        params = urllib.parse.urlencode({
            "filename": image["filename"],
            "subfolder": image.get("subfolder", ""),
            "type": image.get("type", "output"),
        })
        r = requests.get(f"{self.url}/view?{params}", timeout=120)
        r.raise_for_status()
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(r.content)
        return dest

    # -- one-shot convenience -------------------------------------------
    def render(self, workflow: dict[str, Any], dest: Path,
               *, timeout: float = 900.0) -> Path:
        history = self.wait(self.queue(workflow), timeout=timeout)
        images = self.images_from(history)
        if not images:
            raise ComfyError("workflow completed but produced no image; is a "
                             "SaveImage node connected?")
        return self.download(images[0], dest)

    def vram_free(self) -> int:
        """Bytes free on the first CUDA device, as ComfyUI sees it."""
        try:
            devices = self.system_stats().get("devices") or []
        except requests.RequestException:
            return 0
        return int(devices[0].get("vram_free", 0)) if devices else 0

    def free(self, *, unload_models: bool = True, wait: float = 15.0) -> int:
        """Drop models from VRAM, and wait until they are actually gone.

        Necessary on a 16 GB card, where the image model and anything else
        competing for memory will not both fit.

        The wait is the point. POST /free returns as soon as the request is
        queued, not when the weights are released -- so a caller that frees
        and immediately checks nvidia-smi sees 9 GB still held and concludes
        the call did nothing. Polling until the number actually moves turns
        "asked politely" into "it happened", and returns the bytes recovered
        so the caller can say so.
        """
        before = self.vram_free()
        try:
            requests.post(
                f"{self.url}/free",
                json={"unload_models": unload_models, "free_memory": True},
                timeout=self.timeout,
            )
        except requests.RequestException:
            return 0

        deadline = time.monotonic() + wait
        best = before
        while time.monotonic() < deadline:
            time.sleep(0.4)
            now = self.vram_free()
            if now > best:
                best = now
            # Settled: nothing more came back on the last two polls.
            elif best > before:
                break
        return max(best - before, 0)


def _explain_rejection(resp: requests.Response) -> str:
    """ComfyUI's 400s carry the useful detail nested a few levels down."""
    try:
        body = resp.json()
    except ValueError:
        return f"ComfyUI rejected the workflow ({resp.status_code}): {resp.text[:500]}"

    lines = [f"ComfyUI rejected the workflow ({resp.status_code}): "
             f"{body.get('error', {}).get('message', 'unknown error')}"]
    for node_id, err in (body.get("node_errors") or {}).items():
        for detail in err.get("errors", []):
            lines.append(f"  node {node_id}: {detail.get('message')} "
                         f"{detail.get('details', '')}")
    return "\n".join(lines)

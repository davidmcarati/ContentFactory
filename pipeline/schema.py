"""The storyboard contract.

`storyboard.json` is the single artifact that every pipeline step reads or
writes. Step 1 creates it, step 2 attaches real audio durations, step 3
attaches frame paths, step 4 consumes the whole thing.

Deliberately *not* in the schema: shot durations. A shot lasts exactly as long
as its narration takes to speak, and that is only known after TTS has run.
Guessing it up front is the classic way to end up with drifting audio.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Literal

from . import config

PanDirection = Literal["in", "out", "left", "right", "up", "down", "none"]
Transition = Literal["cut", "crossfade", "fade_black"]
ShotKind = Literal["generate", "asset"]


@dataclass
class Asset:
    """A real image from an open collection, with its provenance.

    Lives here rather than in `assets.py` so the schema stays importable
    without pulling in the network and imaging stack. Every field except the
    local path is needed to credit the image later, and credits are not
    optional for anything above the public-domain tier.
    """
    source: str                  # "wikimedia" | "met" | "local"
    title: str
    author: str
    license: str
    tier: str                    # see assets.classify
    page_url: str
    file_url: str
    license_url: str = ""
    width: int = 0
    height: int = 0
    local_path: str | None = None

    def credit_line(self) -> str:
        bits = [self.title or "Untitled"]
        if self.author:
            bits.append(f"by {self.author}")
        bits.append(f"({self.license})")
        if self.page_url:
            bits.append(f"- {self.page_url}")
        return " ".join(bits)


@dataclass
class Motion:
    """Ken Burns parameters for a still frame."""
    pan: PanDirection = "in"
    zoom: float = 1.12          # end scale relative to start

    def validate(self, where: str) -> list[str]:
        errs = []
        if not 1.0 <= self.zoom <= 1.6:
            errs.append(f"{where}: zoom {self.zoom} outside 1.0-1.6")
        return errs


@dataclass
class Style:
    """Applied to every shot, so the whole video looks like one piece."""
    base_prompt: str
    negative: str = "text, watermark, signature, blurry, deformed, lowres, jpeg artifacts"
    seed_base: int = 20260823
    model: str = "qwen"          # key into pipeline.workflows.MODELS


@dataclass
class Voice:
    voice_id: str = config.TTS_VOICE
    speed: float = config.TTS_SPEED


@dataclass
class Chapter:
    """A YouTube chapter marker, anchored to a shot rather than a timestamp.

    Timestamps are derived from the measured narration at publish time, so
    re-recording the voice moves the chapters with it instead of leaving them
    pointing at the wrong moment.
    """
    title: str
    shot_id: int


@dataclass
class Publish:
    """Everything the video needs around it to actually be uploaded."""
    description: str = ""
    tags: list[str] = field(default_factory=list)
    chapters: list[Chapter] = field(default_factory=list)
    # The thumbnail is generated separately from the film. Reusing a frame
    # gives a picture composed for a moving shot, which is the wrong shape for
    # a 1280x720 tile that has to survive being 200 px wide in a sidebar.
    thumbnail_prompt: str = ""
    thumbnail_text: str = ""


@dataclass
class Shot:
    id: int
    vo: str                      # narration text, spoken verbatim
    image_prompt: str            # appended to Style.base_prompt
    motion: Motion = field(default_factory=Motion)
    transition: Transition = "crossfade"

    # Where the picture comes from. "generate" runs the diffusion model;
    # "asset" fetches a real image, which is the right choice for anything a
    # viewer could recognise -- a named painting, a historical photograph, a
    # real place. A generated Mona Lisa is simply wrong, and looks it.
    kind: ShotKind = "generate"
    query: str | None = None     # search terms, when kind == "asset"
    asset: Asset | None = None   # filled in once resolved, kept for credits

    # --- filled in by later steps, absent on a fresh storyboard ---
    audio_sec: float | None = None
    audio_path: str | None = None
    frame_path: str | None = None
    clip_path: str | None = None
    seed: int | None = None

    @property
    def stem(self) -> str:
        return f"{self.id:03d}"

    def validate(self) -> list[str]:
        where = f"shot {self.id}"
        errs = []
        if not self.vo.strip():
            errs.append(f"{where}: empty narration")
        if self.kind == "asset":
            if not (self.query or self.asset):
                errs.append(f"{where}: kind is 'asset' but no query or asset")
        elif not self.image_prompt.strip():
            errs.append(f"{where}: empty image_prompt")
        # Long lines mean the script was chunked badly and the shot will
        # linger far past what a viewer tolerates on a still image.
        words = len(self.vo.split())
        if words > 45:
            errs.append(f"{where}: {words} words of narration, split it")
        errs += self.motion.validate(where)
        return errs

    @property
    def duration(self) -> float:
        """Playback length including the tail pause."""
        if self.audio_sec is None:
            raise ValueError(f"shot {self.id} has no audio yet; run step 2 first")
        return self.audio_sec + config.SHOT_TAIL_SEC


@dataclass
class Storyboard:
    slug: str
    title: str
    style: Style
    shots: list[Shot]
    voice: Voice = field(default_factory=Voice)
    publish: Publish = field(default_factory=Publish)
    aspect: str = "16:9"

    # ------------------------------------------------------------------
    @property
    def dir(self) -> Path:
        return config.project_dir(self.slug)

    def start_of(self, shot_id: int) -> float:
        """Timeline position where a shot's narration begins."""
        t = 0.0
        for shot in self.shots:
            if shot.id == shot_id:
                return t
            t += shot.duration
        raise KeyError(f"no shot {shot_id} in storyboard {self.slug!r}")

    @property
    def total_sec(self) -> float:
        return sum(s.duration for s in self.shots)

    def prompt_for(self, shot: Shot) -> str:
        return f"{self.style.base_prompt}, {shot.image_prompt}"

    def seed_for(self, shot: Shot) -> int:
        """Deterministic per-shot seed, so a rerun reproduces the same frame."""
        return shot.seed if shot.seed is not None else self.style.seed_base + shot.id

    # ------------------------------------------------------------------
    def validate(self) -> list[str]:
        errs = []
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", self.slug):
            errs.append(f"slug {self.slug!r} must be lowercase-kebab-case")
        if not self.shots:
            errs.append("storyboard has no shots")
        ids = [s.id for s in self.shots]
        if ids != list(range(1, len(ids) + 1)):
            errs.append("shot ids must run 1..N with no gaps")
        for s in self.shots:
            errs += s.validate()
        return errs

    def require_valid(self) -> None:
        errs = self.validate()
        if errs:
            raise ValueError(
                "storyboard failed validation:\n  " + "\n  ".join(errs)
            )

    # ------------------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        # Drop keys that later steps have not filled in yet, so a fresh
        # storyboard stays readable by a human.
        for shot in d["shots"]:
            for k in ("audio_sec", "audio_path", "frame_path", "clip_path",
                      "seed", "query", "asset"):
                if shot.get(k) is None:
                    shot.pop(k, None)
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Storyboard":
        shots = []
        for raw in d["shots"]:
            raw = dict(raw)
            raw["motion"] = Motion(**raw.get("motion", {}))
            if raw.get("asset"):
                raw["asset"] = Asset(**raw["asset"])
            shots.append(Shot(**raw))
        raw_publish = dict(d.get("publish", {}))
        raw_publish["chapters"] = [
            Chapter(**c) for c in raw_publish.get("chapters", [])
        ]
        return cls(
            slug=d["slug"],
            title=d["title"],
            style=Style(**d["style"]),
            voice=Voice(**d.get("voice", {})),
            publish=Publish(**raw_publish),
            aspect=d.get("aspect", "16:9"),
            shots=shots,
        )

    def save(self, path: Path | None = None) -> Path:
        path = path or (self.dir / "storyboard.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return path

    @classmethod
    def load(cls, slug_or_path: str | Path) -> "Storyboard":
        path = Path(slug_or_path)
        if not path.suffix:
            path = config.project_dir(str(slug_or_path)) / "storyboard.json"
        return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))

    # ------------------------------------------------------------------
    def ensure_dirs(self) -> None:
        for sub in ("audio", "frames", "clips"):
            (self.dir / sub).mkdir(parents=True, exist_ok=True)

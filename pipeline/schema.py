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
from dataclasses import dataclass, field, fields, asdict
from pathlib import Path
from typing import Any, Literal

from . import config

# A line ends a sentence if it stops on terminal punctuation, allowing for a
# closing quote or bracket after it.
_SENTENCE_END = re.compile(r"[.!?][\"')\]]*\s*$")

_warned_unknown: set[str] = set()


def _build(cls, raw: dict[str, Any]):
    """Construct a schema object, ignoring fields this build does not know.

    The storyboard is the contract between the steps, and some readers of it
    are long-lived: the status server holds one Python process open for hours.
    Adding `Shot.cast` while that server was running left it with the old
    class in memory and a newer storyboard on disk, and every request died on
    an unexpected keyword argument -- the page went blank mid-render, which is
    exactly when it is wanted.

    A field the reader has never heard of is not its business, so it is
    dropped rather than fatal. Said out loud once per field, because the same
    silence would hide a misspelled key in a hand-written storyboard.
    """
    known = {f.name for f in fields(cls)}
    extra = set(raw) - known
    for key in sorted(extra):
        tag = f"{cls.__name__}.{key}"
        if tag not in _warned_unknown:
            _warned_unknown.add(tag)
            print(f"  note: ignoring unknown storyboard field {tag}")
    return cls(**{k: v for k, v in raw.items() if k in known})


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
    # How this style draws people, kept separate from how it draws everything
    # else, and added only to shots that have people in them.
    #
    # It used to live in base_prompt with the rest. A style describing white
    # blob faces and mitten hands then applied that description to all 268
    # shots, and the model obliged: a vat of snail shells came back with
    # somebody sitting in it, and clothes asked for "laid out flat and
    # separate on the snow" came back with a person wearing them. Describing
    # a cast is a request for a cast.
    cast_prompt: str = ""
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

    # Are there people in this shot? Only these get Style.cast_prompt.
    cast: bool = False

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
    def tail(self) -> float:
        """The pause after this line, decided by where the line stops.

        A shot ending mid-sentence is followed by the rest of its own
        sentence, so it gets the shortest gap that will not click. A shot
        ending on a full stop keeps a real beat. See config.SHOT_TAIL_SEC.
        """
        return (config.SENTENCE_TAIL_SEC if _SENTENCE_END.search(self.vo)
                else config.SHOT_TAIL_SEC)

    @property
    def duration(self) -> float:
        """Playback length including the tail pause."""
        if self.audio_sec is None:
            raise ValueError(f"shot {self.id} has no audio yet; run step 2 first")
        return self.audio_sec + self.tail


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
        bits = [self.style.base_prompt]
        if shot.cast and self.style.cast_prompt:
            bits.append(self.style.cast_prompt)
        bits.append(shot.image_prompt)
        return ", ".join(bits)

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
            raw["motion"] = _build(Motion, raw.get("motion", {}))
            if raw.get("asset"):
                raw["asset"] = _build(Asset, raw["asset"])
            shots.append(_build(Shot, raw))
        raw_publish = dict(d.get("publish", {}))
        raw_publish["chapters"] = [
            _build(Chapter, c) for c in raw_publish.get("chapters", [])
        ]
        return cls(
            slug=d["slug"],
            title=d["title"],
            style=_build(Style, d["style"]),
            voice=_build(Voice, d.get("voice", {})),
            publish=_build(Publish, raw_publish),
            aspect=d.get("aspect", "16:9"),
            shots=shots,
        )

    def save(self, path: Path | None = None) -> Path:
        path = path or (self.dir / "storyboard.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        self._warn_if_clobbering(path)
        path.write_text(
            json.dumps(self.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        self._stamp = self._stamp_of(path)
        return path

    @staticmethod
    def _stamp_of(path: Path) -> tuple[int, int] | None:
        if not path.exists():
            return None
        stat = path.stat()
        return (stat.st_size, stat.st_mtime_ns)

    def _warn_if_clobbering(self, path: Path) -> None:
        """Say so out loud when this save is about to lose somebody's edit.

        Step 3 holds one storyboard in memory for the length of a render and
        writes it back after every frame. Anything edited on disk during those
        ten minutes -- a title, a thumbnail line, a prompt for a shot that has
        not been reached yet -- is silently overwritten by the in-memory copy
        on the very next frame.

        That is not hypothetical. A title rewritten for one video during a
        batch render came back to its old value, and the only reason it was
        noticed was a check against the list of intended edits. Detecting it
        is nearly free: remember what the file looked like when it was read,
        and compare before writing over it.

        This warns rather than raises on purpose. Raising here would throw
        away a part-finished render, which is a worse outcome than a stale
        field, and the render is usually the expensive half.
        """
        seen = getattr(self, "_stamp", None)
        if seen is None:
            return
        now = self._stamp_of(path)
        if now is not None and now != seen:
            print(f"  WARNING: {path.name} changed on disk since it was read. "
                  f"Saving over it now; any edit made to {self.slug} in the "
                  f"meantime is lost. Do not edit a storyboard while a step "
                  f"is running.", flush=True)

    @classmethod
    def load(cls, slug_or_path: str | Path) -> "Storyboard":
        path = Path(slug_or_path)
        if not path.suffix:
            path = config.project_dir(str(slug_or_path)) / "storyboard.json"
        sb = cls.from_dict(json.loads(path.read_text(encoding="utf-8")))
        sb._stamp = cls._stamp_of(path)
        sb.rebase_paths()
        return sb

    def rebase_paths(self) -> None:
        """Point recorded media at where it actually is now.

        Steps record absolute paths, and a project folder can move -- it did,
        when deliveries started carrying their own sources. The recorded path
        then names a directory that no longer exists, and step 4 rebuilds a
        video out of nothing. Layout is derivable from the slug and the shot
        stem, so a path that has gone missing is repaired rather than trusted.
        """
        for shot in self.shots:
            for attr, sub, ext in (("audio_path", "audio", ".wav"),
                                   ("frame_path", "frames", ".png"),
                                   ("clip_path", "clips", ".mp4")):
                recorded = getattr(shot, attr, None)
                if not recorded or Path(recorded).exists():
                    continue
                here = self.dir / sub / f"{shot.stem}{ext}"
                setattr(shot, attr, str(here) if here.exists() else None)

    # ------------------------------------------------------------------
    def ensure_dirs(self) -> None:
        for sub in ("audio", "frames", "clips"):
            (self.dir / sub).mkdir(parents=True, exist_ok=True)

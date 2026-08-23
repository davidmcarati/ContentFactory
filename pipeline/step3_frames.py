"""Step 3: one image per shot, via a running ComfyUI.

This is the slow stage -- most of the wall clock for a 100-shot video lands
here -- so it is built to be interrupted. Every finished frame is recorded in
storyboard.json immediately, and a rerun skips whatever already exists on disk.
Killing the process at shot 60 and restarting costs nothing but shot 60.

Seeds are derived from the storyboard, not drawn at random, so regenerating a
single frame two days later reproduces the same picture rather than a new one
that no longer matches its neighbours.
"""
from __future__ import annotations

import argparse
import time
from pathlib import Path

from . import assets, workflows
from .comfy_client import ComfyClient, ComfyError
from .schema import Shot, Storyboard


def render_shot(client: ComfyClient, sb: Storyboard, shot: Shot) -> Path:
    dest = sb.dir / "frames" / f"{shot.stem}.png"
    seed = sb.seed_for(shot)
    graph = workflows.build(
        sb.style.model,
        prompt=sb.prompt_for(shot),
        negative=sb.style.negative,
        seed=seed,
        filename_prefix=f"{sb.slug}_{shot.stem}",
    )
    client.render(graph, dest)
    shot.frame_path = str(dest)
    shot.seed = seed
    return dest


def fetch_shot(sb: Storyboard, shot: Shot) -> Path:
    """Get a real image for this shot instead of generating one.

    A pinned asset (one already chosen and recorded in the storyboard) is
    re-downloaded verbatim. Only an unpinned shot runs a search, and the
    winner is written back so the next run is reproducible rather than
    at the mercy of whatever the search returns that day.
    """
    dest = sb.dir / "frames" / f"{shot.stem}.png"
    raw_dir = sb.dir / "assets"

    if shot.asset and shot.asset.file_url:
        raw = raw_dir / f"{shot.stem}.orig"
        assets.download(shot.asset, raw)
        assets.fit_to_frame(raw, dest)
        shot.asset.local_path = str(dest)
    else:
        if not shot.query:
            raise ValueError(f"shot {shot.id}: asset shot with no query")
        raw_dir.mkdir(parents=True, exist_ok=True)
        shot.asset = assets.resolve(shot.query, dest)

    shot.frame_path = str(dest)
    return dest


def _fmt(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    return f"{m}m{s:02d}s" if m else f"{s}s"


def frames(sb: Storyboard, *, force: bool = False, only: set[int] | None = None,
           url: str | None = None) -> None:
    sb.ensure_dirs()

    todo = [
        s for s in sb.shots
        if (only is None or s.id in only)
        and (force or not (sb.dir / "frames" / f"{s.stem}.png").exists())
    ]
    # Fetches first, then everything generated. Interleaving the two let
    # ComfyUI drop the 16 GB checkpoint during the network-bound stretches and
    # reload it afterwards, which cost more than the fetches did. Order within
    # each group is preserved, and the storyboard is keyed by shot id, so this
    # changes nothing about the result.
    todo.sort(key=lambda s: s.kind != "asset")
    done = len(sb.shots) - len(todo)
    if not todo:
        print(f"all {len(sb.shots)} frames already rendered; use --force to redo")
        return

    # ComfyUI is only needed if something is actually being generated, so a
    # storyboard made entirely of real images runs without it.
    client = None
    if any(s.kind == "generate" for s in todo):
        client = ComfyClient(url) if url else ComfyClient()
        client.require_up()
        problems = workflows.verify_backend(client, sb.style.model)
        if problems:
            raise ComfyError(
                "ComfyUI is running but cannot serve this storyboard:\n  "
                + "\n  ".join(problems)
            )

    n_asset = sum(1 for s in todo if s.kind == "asset")
    print(f"rendering {len(todo)} frames "
          f"({len(todo) - n_asset} generated, {n_asset} fetched, "
          f"{done} already done)")
    started = time.monotonic()

    unfetched: list[tuple[int, str]] = []

    for n, shot in enumerate(todo, start=1):
        t0 = time.monotonic()
        if shot.kind == "asset":
            # A throttled or unreachable collection must not cost the GPU
            # work. Commons rate-limits per IP, and a 429 on the first fetch
            # used to abort the whole batch with sixty-five frames still to
            # generate. Record it, carry on, and say so at the end; the step
            # is resumable, so a later run picks up just the misses.
            try:
                fetch_shot(sb, shot)
                label = f"asset  {shot.asset.title[:34] if shot.asset else ''}"
            except Exception as exc:
                unfetched.append((shot.id, f"{type(exc).__name__}: {exc}"))
                label = f"asset  FAILED, will retry on rerun"
        else:
            render_shot(client, sb, shot)
            label = "gen"
        took = time.monotonic() - t0

        # Persist after every frame; an interrupted run stays resumable.
        sb.save()

        elapsed = time.monotonic() - started
        eta = (elapsed / n) * (len(todo) - n)
        print(f"  [{n:3d}/{len(todo)}] shot {shot.stem}  {took:5.1f}s  "
              f"{label:40s} eta {_fmt(eta)}")

    write_credits(sb)

    # Re-attach paths for frames that were skipped, so the storyboard is
    # complete even on a partial rerun.
    for shot in sb.shots:
        path = sb.dir / "frames" / f"{shot.stem}.png"
        if path.exists() and not shot.frame_path:
            shot.frame_path = str(path)
    sb.save()

    total = time.monotonic() - started
    print(f"\n{len(todo) - len(unfetched)} of {len(todo)} frames in "
          f"{_fmt(total)} ({total / len(todo):.1f}s each)")

    if unfetched:
        print(f"\n{len(unfetched)} asset shots did not fetch. Rerun this step "
              f"once the collection stops throttling; nothing else is lost.")
        for shot_id, reason in unfetched:
            print(f"  shot {shot_id:03d}  {reason[:110]}")


def write_credits(sb: Storyboard) -> Path | None:
    """Write credits.md for every real image used.

    Generated frames need no credit. Fetched ones above the public-domain tier
    legally do, and the only reliable moment to record that is while the
    provenance is still in hand.
    """
    used = [s.asset for s in sb.shots if s.asset]
    if not used:
        return None
    path = sb.dir / "credits.md"
    path.write_text(assets.credits_for(used), encoding="utf-8")
    needs = [a for a in used if a.tier != assets.SAFE]
    print(f"\ncredits: {path}  ({len(used)} images, "
          f"{len(needs)} requiring attribution)")
    return path


def _parse_only(spec: str | None) -> set[int] | None:
    """Accept `7`, `7,9`, or `12-40`."""
    if not spec:
        return None
    out: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            lo, hi = part.split("-", 1)
            out.update(range(int(lo), int(hi) + 1))
        elif part:
            out.add(int(part))
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Render one frame per shot.")
    ap.add_argument("slug")
    ap.add_argument("--force", action="store_true", help="redo existing frames")
    ap.add_argument("--only", help="shot ids to render, e.g. 7 or 12-40")
    ap.add_argument("--url", help="ComfyUI base url if not the default")
    a = ap.parse_args()

    sb = Storyboard.load(a.slug)
    sb.require_valid()
    frames(sb, force=a.force, only=_parse_only(a.only), url=a.url)


if __name__ == "__main__":
    main()

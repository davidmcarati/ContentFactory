"""Real images from open collections, for the shots that should not be invented.

A generated "Mona Lisa" is a lie with a melted face. The actual painting is in
the public domain, hosted for free, and correct. For anything a viewer could
recognise -- an artwork, a historical photograph, a named place, a scientific
image -- fetching beats generating.

Sources, both keyless:
  Wikimedia Commons -- enormous, carries explicit licence metadata
  The Met           -- Open Access, everything flagged isPublicDomain is CC0

## Licence handling

This is the part that matters for a monetised channel, so it is deliberately
strict and everything is recorded. Results are sorted into tiers:

  safe         public domain, CC0        no obligations
  attribution  CC-BY                     must credit; credits.md is generated
  share_alike  CC-BY-SA                  credit AND the share-alike term can
                                         arguably reach the finished video, so
                                         it is excluded unless asked for
  rejected     NC, ND, fair use, unknown never used

One honest caveat the metadata cannot settle: a photograph *of* a
public-domain 2D painting is itself public domain in the US (Bridgeman v.
Corel) and Commons tags it PD-Art on that basis, but a few jurisdictions
disagree. For old flat artwork this is the normal, widely relied-on practice;
for 3D objects and modern photographs the photographer's own rights apply and
the tier above is what governs.
"""
from __future__ import annotations

import argparse
import html
import json
import re
import textwrap
import time
from dataclasses import asdict
from pathlib import Path

import requests
from PIL import Image, ImageFilter

from . import config
from .schema import Asset

UA = {"User-Agent": "ContentFactory/0.1 (local video pipeline)"}

# Wikimedia throttles bursts per IP and answers 429. Searching a batch of
# shots and then immediately downloading them is exactly such a burst, and
# without this the whole thing surfaced as a 73-frame render dying on shot 7
# after the 65 generated frames had already been queued. A shared floor
# between requests plus honouring Retry-After is enough; the volume here is
# tiny, it is only ever bursty.
_MIN_INTERVAL = 0.6
_RETRYABLE = {429, 500, 502, 503, 504}
_last_request = 0.0


def _get(url: str, *, params: dict | None = None, stream: bool = False,
         timeout: int = 30, attempts: int = 5):
    """GET with a rate floor and backoff on the statuses worth retrying."""
    global _last_request
    backoff = 2.0
    for attempt in range(1, attempts + 1):
        gap = _MIN_INTERVAL - (time.monotonic() - _last_request)
        if gap > 0:
            time.sleep(gap)
        response = requests.get(url, params=params, headers=UA,
                                timeout=timeout, stream=stream)
        _last_request = time.monotonic()

        if response.status_code not in _RETRYABLE or attempt == attempts:
            response.raise_for_status()
            return response

        # Retry-After is usually seconds, and is worth obeying rather than
        # guessing: guessing low is how a soft throttle becomes a hard block.
        try:
            wait = float(response.headers.get("Retry-After", backoff))
        except ValueError:
            wait = backoff
        wait = min(max(wait, 1.0), 60.0)
        print(f"    {response.status_code} from {url.split('/')[2]}, "
              f"waiting {wait:.0f}s (attempt {attempt}/{attempts})")
        time.sleep(wait)
        backoff *= 2
    raise AssertionError("unreachable")

SAFE = "safe"
ATTRIBUTION = "attribution"
SHARE_ALIKE = "share_alike"
REJECTED = "rejected"

DEFAULT_TIERS = (SAFE, ATTRIBUTION)

_TAGS = re.compile(r"<[^>]+>")
# Commons packs structured data into the same fields as the human-readable
# text: every translation of a title as `label QS:Lru,"..."` repeated for
# eighty languages, and property statements as `title QS:P1476,en:"..."`.
_STRUCTURED_LABELS = re.compile(r"\s*(?:label|title) QS:[LP].*", re.DOTALL)
_WHITESPACE = re.compile(r"\s+")
# Tags are replaced by a space, so `<i>La Joconde</i>,` would otherwise leave
# `La Joconde ,` in the credits.
_SPACE_BEFORE_PUNCT = re.compile(r"\s+([,.;:!?)\]])")
_SPACE_AFTER_OPEN = re.compile(r"([(\[])\s+")


def _clean(value: str | None, limit: int = 160) -> str:
    """Commons metadata arrives as HTML fragments with structured-data tails."""
    if not value:
        return ""
    text = html.unescape(_TAGS.sub(" ", str(value)))
    text = _STRUCTURED_LABELS.sub("", text)
    text = _WHITESPACE.sub(" ", text)
    text = _SPACE_BEFORE_PUNCT.sub(r"\1", text)
    text = _SPACE_AFTER_OPEN.sub(r"\1", text).strip()
    return text[:limit].rstrip(" ,;-")


def classify(license_code: str, usage_terms: str = "") -> str:
    """Sort a licence string into one of the tiers above.

    Unknown is rejected, never assumed permissive. Being wrong in this
    direction only costs a generated image; being wrong the other way costs a
    takedown on a monetised video.
    """
    text = f"{license_code} {usage_terms}".lower()

    if any(bad in text for bad in ("-nc", "noncommercial", "non-commercial",
                                   "-nd", "noderiv", "fair use", "fairuse")):
        return REJECTED
    if "sa" in re.split(r"[^a-z0-9]+", text) or "share-alike" in text \
            or "sharealike" in text:
        return SHARE_ALIKE
    if text.startswith("cc0") or "cc0" in text or "public domain" in text \
            or license_code.lower().startswith("pd"):
        return SAFE
    if "cc-by" in text or "cc by" in text:
        return ATTRIBUTION
    return REJECTED


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------
def search_wikimedia(query: str, limit: int = 8,
                     thumb_width: int = 2560) -> list[Asset]:
    """Commons full-text search over the File namespace.

    A thumbnail URL is requested rather than the original on purpose: originals
    here run to 30000 px on a side and hundreds of megabytes, which is a very
    slow way to fill a 2304 px frame.
    """
    r = _get(
        "https://commons.wikimedia.org/w/api.php",
        params={
            "action": "query", "format": "json", "generator": "search",
            "gsrsearch": f"filetype:bitmap {query}", "gsrnamespace": "6",
            "gsrlimit": str(limit), "prop": "imageinfo",
            "iiprop": "url|extmetadata|size", "iiurlwidth": str(thumb_width),
        },
    )
    pages = (r.json().get("query") or {}).get("pages") or {}

    out = []
    for page in pages.values():
        info = (page.get("imageinfo") or [{}])[0]
        meta = info.get("extmetadata") or {}
        code = _clean(meta.get("License", {}).get("value"))
        terms = _clean(meta.get("UsageTerms", {}).get("value"))
        out.append(Asset(
            source="wikimedia",
            title=_clean(meta.get("ObjectName", {}).get("value")) or
                  page.get("title", "").removeprefix("File:"),
            author=_clean(meta.get("Artist", {}).get("value")),
            license=terms or code or "unknown",
            tier=classify(code, terms),
            page_url=info.get("descriptionurl", ""),
            file_url=info.get("thumburl") or info.get("url", ""),
            license_url=_clean(meta.get("LicenseUrl", {}).get("value")),
            width=info.get("thumbwidth") or info.get("width", 0),
            height=info.get("thumbheight") or info.get("height", 0),
        ))
    return out


def search_met(query: str, limit: int = 8) -> list[Asset]:
    """The Met's Open Access collection. Everything returned is CC0."""
    base = "https://collectionapi.metmuseum.org/public/collection/v1"
    r = _get(f"{base}/search", params={"q": query, "hasImages": "true"})
    ids = (r.json().get("objectIDs") or [])[: limit * 2]

    out = []
    for object_id in ids:
        if len(out) >= limit:
            break
        try:
            obj = _get(f"{base}/objects/{object_id}").json()
        except requests.RequestException:
            continue
        if not obj.get("isPublicDomain") or not obj.get("primaryImage"):
            continue
        out.append(Asset(
            source="met",
            # Met titles carry hard line breaks; same cleanup applies.
            title=_clean(obj.get("title", "")),
            author=_clean(obj.get("artistDisplayName", "")) or "Unknown artist",
            license="CC0 / Public domain",
            tier=SAFE,
            page_url=obj.get("objectURL", ""),
            file_url=obj["primaryImage"],
        ))
    return out


def search(query: str, limit: int = 8,
           tiers: tuple[str, ...] = DEFAULT_TIERS) -> list[Asset]:
    """Search every source, keep only usable licences, best tier first."""
    found: list[Asset] = []
    for fn in (search_met, search_wikimedia):
        try:
            found += fn(query, limit=limit)
        except requests.RequestException as exc:
            print(f"  warning: {fn.__name__} unavailable ({exc})")

    usable = [a for a in found if a.tier in tiers and a.file_url]
    order = {SAFE: 0, ATTRIBUTION: 1, SHARE_ALIKE: 2}
    usable.sort(key=lambda a: (order.get(a.tier, 9), -(a.width * a.height)))
    return usable[:limit]


# ---------------------------------------------------------------------------
# Fetch and fit
# ---------------------------------------------------------------------------
def download(asset: Asset, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    r = _get(asset.file_url, timeout=180, stream=True)
    with dest.open("wb") as fh:
        for chunk in r.iter_content(1 << 20):
            fh.write(chunk)
    asset.local_path = str(dest)
    return dest


def fit_to_frame(src: Path, dest: Path,
                 width: int = config.GEN_W, height: int = config.GEN_H,
                 tolerance: float = 0.25) -> Path:
    """Put an arbitrary image into the delivery aspect ratio.

    Close enough to 16:9, and it is cropped to fill -- that is what a
    documentary does with a landscape photograph.

    A tall portrait cropped to fill would cut the subject's head off, so those
    are contained instead, over a blurred enlargement of themselves. It is the
    standard treatment precisely because it never lies about the framing.
    """
    img = Image.open(src).convert("RGB")
    target = width / height
    actual = img.width / img.height

    if abs(actual - target) / target <= tolerance:
        scale = max(width / img.width, height / img.height)
        resized = img.resize((max(round(img.width * scale), width),
                              max(round(img.height * scale), height)),
                             Image.LANCZOS)
        left = (resized.width - width) // 2
        top = (resized.height - height) // 2
        out = resized.crop((left, top, left + width, top + height))
    else:
        cover = max(width / img.width, height / img.height)
        bg = img.resize((max(round(img.width * cover), width),
                         max(round(img.height * cover), height)), Image.LANCZOS)
        bg = bg.crop(((bg.width - width) // 2, (bg.height - height) // 2,
                      (bg.width - width) // 2 + width,
                      (bg.height - height) // 2 + height))
        bg = bg.filter(ImageFilter.GaussianBlur(radius=width // 40))
        # Darken hard. Two consecutive asset shots with differently-lit
        # backdrops -- a dark oil painting then a pale sheet of paper -- read as
        # a lighting jump on the cut. Pushing both well down makes the backdrop
        # a consistent surround rather than a second competing image.
        bg = Image.blend(bg, Image.new("RGB", bg.size, (0, 0, 0)), 0.62)

        fit = min(width / img.width, height / img.height) * 0.92
        fg = img.resize((round(img.width * fit), round(img.height * fit)),
                        Image.LANCZOS)
        bg.paste(fg, ((width - fg.width) // 2, (height - fg.height) // 2))
        out = bg

    dest.parent.mkdir(parents=True, exist_ok=True)
    out.save(dest)
    return dest


def resolve(query: str, dest: Path, *, tiers: tuple[str, ...] = DEFAULT_TIERS
            ) -> Asset:
    """Search, take the best licensed hit, download it, fit it to frame."""
    hits = search(query, limit=6, tiers=tiers)
    if not hits:
        raise LookupError(
            f"no usably licensed image found for {query!r}. Either loosen the "
            f"tiers, pin a file by hand, or let this shot be generated."
        )
    asset = hits[0]
    raw = dest.with_suffix(".orig" + Path(asset.file_url).suffix[:5] or ".jpg")
    download(asset, raw)
    fit_to_frame(raw, dest)
    asset.local_path = str(dest)
    return asset


# ---------------------------------------------------------------------------
def credits_for(assets: list[Asset]) -> str:
    lines = ["# Image credits", ""]
    for tier, heading in ((SAFE, "Public domain / CC0"),
                          (ATTRIBUTION, "Creative Commons Attribution"),
                          (SHARE_ALIKE, "Creative Commons Share-Alike")):
        group = [a for a in assets if a.tier == tier]
        if not group:
            continue
        lines += [f"## {heading}", ""]
        lines += [f"- {a.credit_line()}" for a in group]
        lines.append("")
    if not any(a.tier != SAFE for a in assets):
        lines.append("_No attribution is legally required, but crediting the "
                     "collections is good practice._")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description="Find openly licensed images.")
    ap.add_argument("query")
    ap.add_argument("--limit", type=int, default=6)
    ap.add_argument("--all-tiers", action="store_true",
                    help="include share-alike results as well")
    ap.add_argument("--json", action="store_true", help="machine readable")
    a = ap.parse_args()

    tiers = (SAFE, ATTRIBUTION, SHARE_ALIKE) if a.all_tiers else DEFAULT_TIERS
    hits = search(a.query, limit=a.limit, tiers=tiers)

    if a.json:
        print(json.dumps([asdict(h) for h in hits], indent=2))
        return

    if not hits:
        print(f"nothing usably licensed for {a.query!r}")
        return
    for i, h in enumerate(hits, start=1):
        print(f"\n[{i}] {h.title}   ({h.source}, {h.tier})")
        if h.author:
            print(f"    {textwrap.shorten(h.author, 76)}")
        print(f"    licence : {textwrap.shorten(h.license, 68)}")
        if h.width:
            print(f"    size    : {h.width}x{h.height}")
        print(f"    page    : {h.page_url}")


if __name__ == "__main__":
    main()

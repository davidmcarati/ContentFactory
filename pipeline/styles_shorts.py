"""The palette, rewritten for a tall frame watched at arm's length.

Same four keys as `styles.py`, so `--style midcentury` keeps meaning the same
editorial choice, and the same `use_for` / `avoid` / `cast` guidance, which is
about the subject and does not change with the aspect ratio. What changes is
the `prompt`, and it is written out in full here rather than derived from the
horizontal one, because the whole point of this file is to be tuned by eye
against a real cut without that tuning leaking back into the twelve-minute
videos.

Three things a vertical frame changes, all of them learned the same way the
rest of this codebase learned things -- by looking at the sheet:

**Do not describe the shape of the frame. The canvas already is that shape.**

This is the expensive lesson in this file, and it cost two full batches of
the gold Short to learn, so it is written out rather than summarised.

The obvious thing to do for a vertical profile is to tell the model the frame
is vertical. It was tried twice and failed twice, differently:

    "one clear subject centred in a tall vertical frame"
        -> fourteen of twenty frames came back as a *mounted print*: a
           white-bordered vertical panel with an abstract shape inside it,
           standing in the scene, the same object shot after shot.

    "tall upright composition, one clear subject standing full height"
        -> the panel went away and a *person* arrived instead, in all three
           probe frames, none of which asked for one. A cluster of pale
           spheres became a figure in a dress; an empty dust field grew a
           giant.

Both readings are correct English and both are requests for an object. "A
tall vertical frame" names a frame. "Standing full height" describes a
standing figure. The horizontal presets say "fills the frame" and have never
done either, so the trigger is not the word `frame` -- it is naming the format
as a thing rather than letting the latent size be it.

So the presets below are the horizontal prompts verbatim, plus one clause
about contrast, and nothing at all about the aspect ratio. The model composes
for the canvas it is given; 864x1536 is the instruction.

The same slip is easier to make in shot prompts: "deep black sky filling the
upper frame" fed the panel too, and became "deep black sky above".

This is the third time in this repository that describing a thing in a style
prompt has drawn that thing into every shot -- after the paper grain that
summoned signatures and the cast description that put a person in 268 frames.
A style prompt is not a note to the reader. It is a request, repeated once per
shot.

**Muted loses.** A Short is watched small, at speed, often in daylight, and
`midcentury` was chosen precisely for a restrained palette. Restraint reads as
grey at this size, so its palette clause is the one substantive rewrite in
this file: the palette stays narrow but is given a single strong accent to
carry the frame. That is a change of look, so it is stated rather than
smuggled in.

**A cut lands every two or three seconds.** There is no time to read a
picture, which raises the value of a bold silhouette and lowers the value of
detail. The density clause is kept -- without it all four presets drift toward
empty backgrounds -- but stated as clear shapes rather than fine detail.

Everything AGENTS.md and STYLE.md say about prompts still holds here and is
not repeated: no numbers, no object that carries writing, no naming an absence,
no hand as a subject, and people anchored to a period.

    CF_PROFILE=shorts python -m pipeline.styles_shorts     # print the palette
"""
from __future__ import annotations

from dataclasses import replace

from .styles import PRESETS as LONG_PRESETS, Preset

# The clause every preset ends on, and it is deliberately one thing rather
# than the paragraph it started as. Nothing here describes the shape of the
# frame; see the docstring for the two batches that cost.
EMPHASIS = "strong contrast"

# Only the drawing style is rewritten. use_for, avoid and cast are editorial
# guidance about the subject and are inherited verbatim -- a style that is
# tasteless on grief in a twelve-minute video is tasteless on grief in sixty
# seconds, and a style that cannot hold a recurring face still cannot.
# Each one is the horizontal prompt verbatim plus EMPHASIS. That is not
# laziness: the horizontal prompts have between them survived roughly nine
# hundred reviewed frames, and every clause in them is load-bearing -- the
# density clause against empty backgrounds, "2D" stated positively because the
# negative prompt is inert, and the absence of any printed-artefact token
# because those summoned signatures. Rewriting them for a tall canvas was
# tried twice and produced two broken batches.
_PROMPTS: dict[str, str] = {
    "cartoon": (
        "2D cel animation illustration, clean confident black line art, "
        "flat cel shading, bold colour blocking, hand drawn animation "
        "production look, detailed layered composition that fills the frame, "
        + EMPHASIS
    ),
    "webcomic": (
        "simple 2D cartoon illustration, thick uniform black outline, "
        "flat unshaded colour blocking, "
        "simple flat scenery with a few clear props, "
        "clean modern webcomic look, "
        + EMPHASIS
    ),
    # The one real divergence, and the one change from this file that survived
    # both bad batches: it is a palette, not a geometry. The horizontal preset
    # opens with "muted restrained palette", which is deliberate -- it is what
    # makes the style usable on depression and medicine. At Short size the same
    # clause comes back grey, so the restraint is kept and given something to
    # push against, and the coral against dark teal is what made the first
    # sheet read at thumbnail size even while the compositions were wrong.
    #
    # If a subject needs the austere version, pass the horizontal prompt to
    # --style in full.
    "midcentury": (
        "mid-century modern illustration, restrained palette with one strong "
        "accent colour, 2D flat colour, detailed layered composition that "
        "fills the frame, strong graphic shapes, "
        + EMPHASIS
    ),
    "papercut": (
        "flat cut paper collage illustration, bold simple coloured paper "
        "shapes, flat graphic silhouettes, straight on, limited warm palette, "
        "composition fills the frame, "
        + EMPHASIS
    ),
}

PRESETS: dict[str, Preset] = {
    key: replace(preset, prompt=_PROMPTS[key])
    for key, preset in LONG_PRESETS.items()
}

# A key added to styles.py and forgotten here would silently fall back to
# nothing, so the two are held level at import.
_missing = sorted(set(LONG_PRESETS) - set(_PROMPTS))
if _missing:
    raise RuntimeError(
        f"styles_shorts has no vertical prompt for {', '.join(_missing)}. "
        f"Write one, or the shorts profile cannot render that style."
    )

_BY_NUMBER = {str(p.number): p for p in PRESETS.values()}


def get(value: str) -> Preset | None:
    value = value.strip().lower()
    return PRESETS.get(value) or _BY_NUMBER.get(value)


def cast_for(value: str) -> str:
    preset = get(value)
    return preset.cast if preset else ""


def resolve(value: str) -> str:
    """Same contract as styles.resolve: preset name in, base prompt out.

    A raw prompt passes through, and a short bare word is rejected rather than
    rendered ninety times as the single word "midcentruy".
    """
    preset = get(value)
    if preset:
        return preset.prompt
    if "," not in value and len(value.split()) < 4:
        raise ValueError(
            f"unknown style {value!r}. Pick one of "
            f"{', '.join(sorted(PRESETS))} (or 2, 4, 6, 7), or pass a full "
            f"prompt with commas in it."
        )
    return value


def catalogue() -> str:
    lines = ["vertical palette (CF_PROFILE=shorts)", ""]
    for preset in sorted(PRESETS.values(), key=lambda p: p.number):
        lines += [
            f"{preset.key}  (sheet #{preset.number})  {preset.label}",
            f"    prompt   {preset.prompt}",
            f"    use for  {preset.use_for}",
            f"    avoid    {preset.avoid}",
            "",
        ]
    return "\n".join(lines)


if __name__ == "__main__":
    print(catalogue())

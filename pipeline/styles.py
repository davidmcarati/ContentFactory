"""The channel's visual palette: four styles, chosen per video.

One `base_prompt` applies to every shot of a video and is what keeps ninety
shots looking like one piece, so this is a per-video decision made once while
the script is being written -- never per shot.

The palette was picked by eye from a six-way probe (`tests/style_probe.py`)
rendering one scene at one seed under every candidate. Three survived. The
three that did not: flat vector was too empty to hold a full screen for eight
seconds, storybook watercolour went soft under a camera move, and ink novel
overlapped with cel without adding anything.

Each preset already carries the two clauses that are easy to forget:

  "2D" stated positively, because the negative prompt does nothing on
  FLUX.1-schnell (see AGENTS.md) and listing "3d render" there is theatre.

  a density clause, because all three drift toward near-empty backgrounds
  without one, and empty reads as unfinished when held full-screen.

And none of them describes the frame as a *printed artefact*. "screen print
texture", "hand printed" and "subtle paper grain" all shipped in the original
mid-century prompt, and a dozen frames came back signed: "Nzainful",
"S0/20IG 1918", a deckled paper border around the picture. A print has a
signature and an edition number, so the model drew them. Dropping the grain
token removed them; the style survived intact.

Adding "unsigned" instead made it worse and put the signature back. Naming a
thing summons it -- the same trap as the negative prompt, one level up.

`webcomic` arrived later, from tests/character_probe.py, and it is the one
preset that solves problems rather than just setting a look:

  Hands. Every other preset has to route around them -- the lint rule exists
  because "a stylised open palm" produced six fingers and a wrist opening
  into loose bones. A mitten is a convention with no fingers to get wrong, so
  the prompts can simply show people holding things.

  Invented text. Simplifying the drawing far enough removed the signatures,
  the fake body copy and the labels, without any of the avoidance the other
  presets need.

  Its own failure is crowds: without "all the same size, at the same
  distance" one figure balloons into the foreground and the rest shrink
  behind it.

`papercut` needed a second correction for the same underlying reason. Asking
for layered paper with drop shadows and calling it "detailed" produced a very
convincing paper *relief sculpture* -- coherent, handsome, and three
dimensional, which is the one thing this channel asked not to be. Flatness has
to be demanded outright ("flat graphic silhouettes, straight on"), and the
detail clause relaxed, or the model builds a diorama.

    python -m pipeline.styles          # print the palette
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Preset:
    key: str
    number: int              # the number it carried on the probe sheet
    label: str
    prompt: str
    use_for: str
    avoid: str


PRESETS: dict[str, Preset] = {
    "cartoon": Preset(
        key="cartoon",
        number=2,
        label="Cel cartoon",
        prompt=(
            "2D cel animation illustration, clean confident black line art, "
            "flat cel shading, bold colour blocking, hand drawn animation "
            "production look, detailed layered composition that fills the frame"
        ),
        use_for=(
            "Curious, human, faintly absurd subjects. The heavy line survives "
            "being watched on a phone better than anything else here, and it "
            "is the only preset that draws a face worth looking at."
        ),
        avoid=(
            "Grief, illness, violence, anything with real victims. The style "
            "reads as light and will argue with the narration."
        ),
    ),
    "webcomic": Preset(
        key="webcomic",
        number=7,
        label="Flat webcomic characters",
        prompt=(
            "simple 2D cartoon illustration, thick uniform black outline, "
            "flat white faces with small round dot eyes and thin eyebrows, "
            "simple white mitten hands, flat unshaded colour blocking, "
            "simple flat scenery with a few clear props, "
            "clean modern webcomic look"
        ),
        use_for=(
            "Anything carried by people doing things. It is the only preset "
            "that survives a recurring cast: measured in "
            "tests/character_probe.py, seven scenes at seven different seeds "
            "came back unmistakably the same drawing, because the consistency "
            "lives in the style rather than in the seed."
        ),
        avoid=(
            "Landscape, texture and material -- the flat blocking has nothing "
            "to say about them. And anything that needs a real face: these "
            "people are deliberately blank."
        ),
    ),
    "midcentury": Preset(
        key="midcentury",
        number=4,
        label="Mid-century print",
        prompt=(
            "mid-century modern illustration, muted restrained palette, "
            "2D flat colour, detailed layered composition that fills the "
            "frame, strong graphic shapes"
        ),
        use_for=(
            "Serious explanatory work: science, medicine, institutions, "
            "anything where the subject deserves to be taken straight. It "
            "abstracts people instead of portraying them, which on a subject "
            "like depression is the difference between tactful and ghoulish."
        ),
        avoid=(
            "Scripts that turn on a recognisable recurring person, and "
            "anything that wants to feel warm. The palette is deliberately "
            "cool and a little austere."
        ),
    ),
    "papercut": Preset(
        key="papercut",
        number=6,
        label="Paper cut collage",
        prompt=(
            "flat cut paper collage illustration, bold simple coloured paper "
            "shapes, flat graphic silhouettes, straight on, limited warm "
            "palette, composition fills the frame"
        ),
        use_for=(
            "Physical processes and things built in stages: geology, "
            "engineering, construction, the history of an object. Stacked "
            "paper gives depth without a hint of 3D rendering."
        ),
        avoid=(
            "Fine mechanical detail and crowds. Simple shapes are the whole "
            "idea, and they flatten anything intricate into mush."
        ),
    ),
}

# The probe sheet numbered them 1-6 and that is how they get talked about.
_BY_NUMBER = {str(p.number): p for p in PRESETS.values()}


def get(value: str) -> Preset | None:
    """Look a preset up by key or by its probe-sheet number."""
    value = value.strip().lower()
    return PRESETS.get(value) or _BY_NUMBER.get(value)


def resolve(value: str) -> str:
    """Turn a preset name into a base prompt, passing literal prompts through.

    A raw prompt is still accepted so an experiment does not need a code
    change. But a short bare word is far more likely to be a misspelled preset
    than a genuine style description, and silently accepting it would render
    ninety frames prompted with the single word "midcentruy" before anyone
    noticed.
    """
    preset = get(value)
    if preset:
        return preset.prompt
    if "," not in value and len(value.split()) < 4:
        raise ValueError(
            f"unknown style {value!r}. Pick one of "
            f"{', '.join(sorted(PRESETS))} (or 2, 4, 6), or pass a full "
            f"prompt with commas in it."
        )
    return value


def catalogue() -> str:
    lines = []
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

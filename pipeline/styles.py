"""The channel's visual palette: three styles, chosen per video.

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
    "midcentury": Preset(
        key="midcentury",
        number=4,
        label="Mid-century print",
        prompt=(
            "mid-century modern illustration, screen print texture, muted "
            "restrained palette, 2D hand printed, detailed layered composition "
            "that fills the frame, strong graphic shapes, subtle paper grain"
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
            "2D paper cut collage illustration, layered cut coloured paper, "
            "soft drop shadows between the layers, torn and clean cut edges, "
            "visible paper fibre texture, detailed layered composition that "
            "fills the frame"
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

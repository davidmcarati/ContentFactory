"""The vertical profile: the same factory, turned ninety degrees.

Activated by an environment variable, read once at import:

    CF_PROFILE=shorts .venv-pipeline/Scripts/python.exe -m pipeline.run gold-short

An environment variable rather than a runtime switch, and that is not a
stylistic preference. Half the geometry in this codebase is bound as a default
argument -- `kenburns.render(out_w=config.OUT_W)`, `workflows.qwen_image(
width=config.GEN_W)`, `outro.build_overlay(width=config.OUT_W)` -- and a
default is evaluated when the module is imported, not when the function is
called. Assigning `config.OUT_W = 1080` at runtime therefore reaches none of
them, silently, and the frames come back the old shape with nothing to show
that anything was ignored.

The variable also survives the one thing a monkeypatch cannot: `kenburns.
render_many` renders clips in a process pool, and on Windows that is spawn, so
every worker imports `pipeline.config` from disk again. A patched module in the
parent is invisible to them. An inherited environment is not.

Everything here is meant to be tuned by eye against a real cut. The numbers
below are a starting point derived from the horizontal ones, not measurements
-- unlike almost everything in AGENTS.md, which is. When one of them turns out
to be wrong, change it here and say so in the comment.
"""
from __future__ import annotations

# --- Not negotiable from a profile -----------------------------------------
# The timeline arithmetic is a shared invariant: `shot.duration == audio_sec +
# tail`, narration.wav concatenates the same tail, and step 4 places every
# boundary at the running sum of the same value. tests/smoke.py holds the whole
# chain to 24 ms across five shots and an end card.
#
# A profile that reached into those constants would break the lock in one
# aspect ratio and not the other, and the smoke baseline would go on passing
# because it runs under the default profile. So the geometry is a profile
# concern and the clock is not, and the split is enforced rather than trusted:
# put any of these in OVERRIDES and `apply` refuses to run.
#
# STILL_FRAMES is in the list for a slightly different reason. It selects
# cut-driven pacing, which is what makes GEN equal to OUT; a vertical video
# with pans would want frames above 1080x1920 again and a crossfade chain
# through step 4. That is a second pipeline, not a profile.
PROTECTED = frozenset({
    "FPS",
    "SHOT_TAIL_SEC",
    "SENTENCE_TAIL_SEC",
    "CROSSFADE_SEC",
    "TRIM_SILENCE",
    "TRIM_FLOOR_DBFS",
    "TRIM_MARGIN_SEC",
    "STILL_FRAMES",
    "DELIVERY_ROOT",
    "SOURCES_NAME",
})

# Deliberately *not* protected, and worth saying why, because both look like
# clock constants at a glance.
#
# LEAD_IN_SEC and the SEGMENT_* pair are consumed by step 2 and written into
# the storyboard -- `shot.lead_sec`, `shot.tail_sec` -- so step 4 reads the
# values that were actually spoken rather than re-deriving them from config.
# Changing either changes the next narration and nothing about an existing
# one, which is exactly what a profile is allowed to do.
#
# MIN_SHOT_SEC and MAX_SHOT_SEC only feed the warning step 2 prints about a
# badly chunked script. They are bounds, not arithmetic.

# --- The profile -----------------------------------------------------------
OVERRIDES: dict[str, object] = {
    "ASPECT": "9:16",

    # 1080x1920 is what YouTube serves a Short at. Frames are shown 1:1 under
    # cut pacing, so there is nothing to gain above it.
    "OUT_W": 1080,
    "OUT_H": 1920,
    "GEN_W": 1080,
    "GEN_H": 1920,

    # The horizontal profile composes at 1536x864 = 1.33 MP because FLUX stops
    # composing a scene and starts fusing local structure above roughly 1.5 MP
    # -- the measurement that shipped a broken video before anyone made it.
    # 864x1536 is that same 1.33 MP transposed, so the ceiling is respected
    # rather than re-tested: both sides are multiples of 16, and the
    # enlargement to 1080x1920 is exactly 1.25x with no reframing.
    "COMPOSE_W": 864,
    "COMPOSE_H": 1536,

    # --- One take ----------------------------------------------------------
    # A Short is short enough to be performed in one go, so it is: the whole
    # script goes to Kokoro as a single utterance and the shots take their
    # boundaries from its word timestamps. Nothing is glued.
    #
    # In long form the 40-word target is a compromise -- 268 shots is 268
    # seams if you speak them one at a time, and a whole ten-minute script is
    # far past what the model will hold in one breath. A Short is capped at
    # 180 s by MAX_TOTAL_SEC, which is about 440 words at the measured 146
    # words a minute, so a ceiling of 2000 means "never split" for anything
    # that is legally a Short while still being a number rather than infinity.
    #
    # `segments` only ever breaks at a sentence end, so raising the target
    # cannot introduce a mid-clause seam; it can only remove seams.
    #
    # What this does *not* guarantee is a single utterance inside the model.
    # KPipeline splits its own input to stay under the token limit, and
    # step2's speak_run now reports when it did and how much silence it left
    # at each join -- read that line rather than trusting this one.
    "SEGMENT_WORDS": 2000,
    "SEGMENT_MAX_WORDS": 2000,
    # And the other half of the same request. One run is not one utterance:
    # the 191-word gold Short came back as three chunks with 1066 ms and
    # 989 ms of dead air at the joins, which is worse than the five short runs
    # it replaced, because those at least had their edges trimmed. Rebuild
    # each join as the beat a sentence ending actually deserves.
    "JOIN_GAP_SEC": 0.34,

    # --- Opening -----------------------------------------------------------
    # A second of silence over the first picture is breathing room at the
    # start of twelve minutes and a lost viewer at the start of sixty seconds:
    # the thumb is already moving, and the hook has to be audible before it
    # decides. Enough to land on the frame, not enough to wait.
    "LEAD_IN_SEC": 0.3,

    # --- Subtitles ---------------------------------------------------------
    # These are the numbers most likely to be wrong, and the cheapest to
    # check: burn one cut and look at it on a phone.
    #
    # Size 34 was measured at 1080p across 1520 px of usable width. A vertical
    # frame has 960 px between much smaller side margins, and it is watched
    # held in one hand, so the type has to grow twice as fast as the frame
    # shrank. 58 px is roughly 3% of frame height, which is what caption-first
    # vertical video settles on.
    "SUB_FONT_SIZE": 58,
    "SUB_MARGIN_H": 60,
    # And the reason the text sits so far off the bottom: the Shorts player
    # puts the title, channel line and description over the lower part of the
    # frame, and the like/comment/share column up the right side. Anything
    # below about 330 px is behind the interface. 420 keeps a two-line cue
    # clear of it with room to spare.
    "SUB_MARGIN_V": 420,
    # Chars per line. This started at 26, derived by proportion from the
    # horizontal 54, and the proportion was wrong in the expensive direction:
    # too small forces a *third* line and strands single words on their own
    # cue, both of which STYLE.md calls a fault. "Which is why the first great
    # technology of the night" wrapped to three lines with "night" alone on the
    # last one, and an eleven-word line paged into two cues so that "can see."
    # arrived by itself.
    #
    # So measured instead of reasoned, in Arial at 58 px against the 960 px
    # between the margins:
    #
    #     28 chars   690 px      32 chars   783 px
    #     30 chars   722 px      34 chars   847 px      36 chars   911 px
    #
    # 32 leaves 177 px of headroom for a wide line and fits every cue in this
    # script on two lines. The cue limit is twice this, so it also decides
    # where a long narration line pages -- which is why raising it removes
    # stranded cues rather than creating them.
    "SUB_MAX_CHARS": 32,
    # Outline and shadow are in pixels, so they have to grow with the type or
    # white text over a pale frame goes unreadable again.
    "SUB_OUTLINE": 5,
    "SUB_SHADOW": 2,

    # --- End card ----------------------------------------------------------
    # Six seconds is a tenth of a Short. The card is kept because it is where
    # the subscribe actually happens, but cut to a beat, and the fade with it
    # -- a 1.2 s fade out of a 2.5 s card is half the card.
    "OUTRO_SECONDS": 2.5,
    "OUTRO_FADE_OUT": 0.8,
    # "Subscribe for more" at 96 px needs about 990 px of a 1080 px frame.
    # Two lines of 76 px sit inside it with margins.
    "OUTRO_TEXT_SIZE": 76,
    "OUTRO_SUBTEXT_SIZE": 34,

    # --- Packaging ---------------------------------------------------------
    # A Short's tile is vertical, so the 1280x720 thumbnail is the wrong
    # picture, not merely the wrong size. Composed at the same 1.25x ratio as
    # the frames for the same reason.
    "THUMB_W": 1080,
    "THUMB_H": 1920,
    "THUMB_COMPOSE_W": 864,
    "THUMB_COMPOSE_H": 1536,

    # --- Bounds ------------------------------------------------------------
    # A shot held longer than this on a vertical feed is a shot the viewer has
    # already scrolled past. Purely a validation bound; duration still comes
    # from the measured audio.
    "MAX_SHOT_SEC": 6.0,
    # YouTube stops treating an upload as a Short past three minutes, and the
    # difference is not cosmetic -- it lands in a different feed. Checked
    # against measured narration, never against a word count.
    "MAX_TOTAL_SEC": 180.0,
}


def apply(namespace: dict) -> None:
    """Overwrite config's module globals with the vertical values.

    Two guards, both catching a mistake that would otherwise be silent.

    A name in PROTECTED is refused outright: that is the timeline arithmetic,
    and a profile has no business in it.

    A name config has never heard of is also refused, because the only way to
    get one is a typo -- and a misspelled `SUB_FONT_SIZE` would leave the
    horizontal value in place and produce a cut that looks merely a bit wrong.
    """
    clash = sorted(PROTECTED.intersection(OVERRIDES))
    if clash:
        raise RuntimeError(
            f"config_shorts.OVERRIDES touches the shared timeline: "
            f"{', '.join(clash)}. Those constants are the audio/video lock "
            f"and are not a per-profile decision; see PROTECTED above."
        )

    unknown = sorted(set(OVERRIDES) - set(namespace))
    if unknown:
        raise RuntimeError(
            f"config_shorts.OVERRIDES names settings config does not define: "
            f"{', '.join(unknown)}. Add them to config.py first, or fix the "
            f"spelling -- a name that lands nowhere leaves the horizontal "
            f"value in place and says nothing."
        )

    namespace.update(OVERRIDES)

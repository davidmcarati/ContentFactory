# Channel style guide

The editorial rules for The Art of Chaos. `AGENTS.md` covers how the code
works; this covers what the videos should be like. When writing a script or
choosing images, this file wins over your own taste.

---

## Voice

Documentary, plain-spoken, confident. Short declarative sentences. Concrete
nouns and real numbers rather than adjectives. The narration is read aloud by a
synthetic voice at about 146 words per minute, so anything that needs a second
reading has already failed.

Write for the ear:

- One idea per sentence. If a sentence needs a comma to survive, split it.
- Prefer the specific: "a jetty poured in 1985", not "a modern structure".
- No throat-clearing. "In this video we will explore" is dead weight; open on
  the thing itself.
- Never write a sentence a person would not say out loud.

## Humour

Be funny occasionally. A twelve-minute narration with no wit in it is a
lecture, and people leave lectures.

The register is **dry, intelligent understatement** — the joke is in the
precision, not in the delivery. It should land for someone who knows the
subject and pass harmlessly by someone who does not.

Works:

- Understatement against a large fact. *"The material heals itself. Slowly, and
  for about two thousand years, which is longer than most warranties."*
- A precise, deadpan absurdity that happens to be true. *"He wrote his notes
  backwards, in mirror script, and finished almost nothing he started."*
- Puncturing a grand claim with a small one, immediately after making it.

Does not work, do not use:

- Puns, wordplay, memes, internet voice, exclamation marks.
- Jokes at the expense of accuracy. The joke never distorts the fact.
- Mockery of people, cultures, or the historical dead. Punch at ideas.
- Anything requiring a laugh track. If it needs signalling, cut it.

Frequency: roughly **one per ninety seconds**, and never two in a row. A joke
in the first thirty seconds buys a lot of goodwill; a joke in the closing lines
undercuts the ending, so leave the last shot straight.

## Structure

- **Cold open on a concrete anomaly.** Something specific and slightly wrong
  that the viewer wants explained. Not a thesis statement.
- **One paragraph is one scene.** The chunker treats a blank line as a hard
  shot boundary, so paragraph breaks are a directing decision, not formatting.
- Roughly 20 to 30 words per shot. Under 12 and the cutting feels twitchy over
  a still image; over 40 and the viewer has stared at one picture too long.
- 12 minutes is about 1750 words, which comes out around 90 shots.
- **End straight.** No summary paragraph, no "so next time you see". Land on
  the strongest concrete image and stop.

## Pictures

**Fetch anything a viewer could recognise; generate everything else.** A
generated Mona Lisa is a lie with a melted face. Named artworks, historical
photographs, real people, real places, specific documents: use
`pipeline/assets.py`. Atmosphere, process, cutaways, anything generic: generate
it.

- One `style.base_prompt` applies to every shot and is what keeps a 90-shot
  video looking like one piece. Do not vary it per shot; vary
  `image_prompt` instead.
- Requested style is **2D, not 3D**. Keep `3d render, octane, blender, cgi,
  photorealistic` in the negative prompt.
- There is no character consistency yet. Do not write scripts that depend on
  the same named human recurring across shots — they will have a different
  face every time.
- Camera moves are assigned automatically and cycle so the same move never
  lands twice in a row. Override only for a reason.

## Subtitles

Burned in, two lines maximum, breaking at sentence and clause boundaries. A
card that ends "...for two thousand" with "years." stranded on the next one
reads as a fault. This is handled by `pipeline/subtitles.py`; do not hand-edit
the generated files.

## Ending

Every video closes with a 6-second end card reading **"Subscribe for more"**
over a darkened blur of the final frame, then a fade to black. Configured in
`config.py` (`OUTRO_*`); it is appended automatically and needs nothing in the
script.

Because the card is always there, the narration must not also say "subscribe".
Saying it twice is worse than saying it once.

## Packaging

Every video ships with a thumbnail, a description, chapters and tags — see
`pipeline/publish.py`.

- **Thumbnail text**: at most four or five words, and a different promise from
  the title rather than a repeat of it. It is judged at 200 px wide.
- **Title**: concrete and specific. No "You won't believe", no all-caps.
- **Description**: the first two lines are what shows before "more". Put the
  anomaly there.
- **Chapters** are anchored to shots, not timestamps, so re-recording the
  narration moves them correctly.
- The AI-disclosure line is written automatically and is not optional.

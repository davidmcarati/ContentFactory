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
- Requested style is **2D, not 3D**. Say so in the *positive* prompt. The
  negative prompt has no effect on FLUX.1-schnell — see AGENTS.md — so
  listing `3d render, cgi` there accomplishes nothing. All three presets
  below already say it.

### Choosing the style

The channel has **three** styles, and every video uses exactly one of them.
They live in `pipeline/styles.py`; run `python -m pipeline.styles` to print
them. Do not invent a fourth without asking — a new look is a channel
decision, not a per-video one.

| # | key | what it is | reach for it when | it fails at |
|---|---|---|---|---|
| 2 | `cartoon` | cel animation, black line art | the subject is curious, human, faintly absurd; the video will be watched on a phone | grief, illness, violence — it reads light and argues with the narration |
| 4 | `midcentury` | screen print, muted palette | serious explanatory work: science, medicine, institutions | warmth, and any script turning on a recognisable person |
| 6 | `papercut` | layered cut paper, soft shadows | physical processes and things built in stages: geology, engineering, the life of an object | fine mechanical detail and crowds |

**The choice is made while writing the script, not after.** It follows from
the subject, and the deciding question is what the style says about the people
in it. `cartoon` is the only one that draws a face worth looking at, which
also makes it the only one that can be tasteless: a cel-shaded depressive is a
cartoon of a sick person. `midcentury` abstracts figures instead of portraying
them, and on that subject the abstraction *is* the tact. `papercut` barely
does people at all, which is fine when the subject is a thing.

When two fit, prefer the one whose failure mode the script never touches.
Say which one you picked and why when you hand the video over, so the call can
be overruled.

Pass it by key or by number:

    step1_script.py new my-slug --style papercut ...
- **Never prompt for an object that carries writing.** No charts, posters,
  diagrams, calendars, newspapers, manuals or labels. The model renders
  convincing-looking gibberish onto every one of them and there is no negative
  prompt to stop it. Describe the physical object instead: "thick stacked
  paper strips of different heights", not "a bar chart".
- **Never ask for anything that carries a name.** This is the writing trap in
  its subtler form: no object is named, so the rule above does not fire, but
  the request still implies legible words. "Weekday blocks marked with
  planetary symbols from different traditions" produced a table reading
  *Weelay, Dicky, Gunday, Flerday*. "A set of month blocks with their names
  out of order" produced the word **MOLNTUR** in letters a foot high across
  the frame. If the idea is that something is named, show the things and let
  the narration name them.
- **Abstract diagram prompts fail in a way concrete scenes do not.** Roughly
  half the "flat graphic band of X" shots in the time video came back as
  pleasant abstract landscapes with no relation to the idea. The style prompt
  already supplies the graphic flatness; the shot prompt should supply a
  scene. "An ancient Greek astronomer's desk with a marked bronze ring and
  brass dividers" lands every time. "A flat graphic ring where a marker steps
  further along on each cycle" lands about half the time.
- **Never state a number.** The model cannot count. "Six open books" comes
  back as a bookshelf, "three vessels" as two, "two people" as one. Describe
  the arrangement instead — "a row of identical cups, one of them filled with
  something black" — and let the count fall where it may.
- **Never make a hand the subject.** Close-range hands come back with the
  wrong number of fingers, joints bending the wrong way, or a second hand
  fused to the first. "A scroll rolled shut by hands at the edge of the frame"
  produced one enormous malformed claw. Show the object mid-action without the
  actor — "a tightly rolled scroll bound with cord" — or push the person far
  enough back that the hands are a few pixels. The same goes for faces in
  close-up: keep people small, distant, in silhouette, or turned away.
  **A palm is a hand.** So is a finger, a thumb, a knuckle and a wrist. The
  time video needed to show twelve finger bones counted off with a thumb, the
  word "hand" never appeared in any of the three prompts, and the frames came
  back with six fingers, a wrist opened into loose bones, and a pair of palms
  sharing three thumbs. The lint rule had been there the whole time; it was
  defeated by a synonym. Give the narration the anatomy and the picture the
  arithmetic: a column of stacked segments beside a row of tally tokens says
  sixty without asking the model to draw a hand.
- **Anchor every person to a period.** Without one the model dresses them
  now. A prompt for "a painter still working from a ladder" on a medieval
  genealogy delivered a man in jeans and a baseball cap. Say "medieval",
  "eighteenth century", "nineteen twenties" — the era word does the work.
- **Do not ask for a picture of a picture.** "A painted wall panel of a family
  tree of crowned figures" was rendered as a literal tree with crowned heads
  growing in the foliage. The model collapses the frame within the frame.
  Describe the thing itself, or describe the surface it is painted on, but not
  both at once.
- **Watch for words that mean two things.** "A long caravan of camels and
  travellers crossing pale desert dunes, fourteenth century West African
  dress" put a modern touring caravan on the dune beside the camels. The era
  was named, the rest of the prompt was unambiguous, and the model still took
  the other sense of the one word that had two. Prefer the unambiguous
  phrasing -- "a long line of laden camels" -- over trusting context to
  disambiguate.
- **A bare surface is the way to keep writing off it.** On Qwen, asking for a
  nineteen thirties bank facade produced NICMERIICBIlI and AMRICIANK across
  it; asking for the same scene with "plain stone bank, bare facade"
  produced a building whose one sign reads BANK, correctly spelled. Removing
  the room for text is more reliable than removing the request for it.
- **Never name what you do not want.** There is no way to ask for an absence:
  the negative prompt is inert, and naming a thing in the positive prompt is a
  request for it. "Unsigned" produced a signature.
- Ask for density. Without "detailed layered composition that fills the frame"
  this style drifts into near-empty backgrounds, which look unfinished held
  full-screen for eight seconds.
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

"""Central configuration for the content factory.

Everything that a human might want to tweak lives here. The pipeline steps
import from this module rather than hardcoding paths or magic numbers.
"""
from pathlib import Path

# --- Locations -------------------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
LOGS = ROOT / "logs"
COMFY_DIR = ROOT / "comfy"
MODELS = Path("D:/ai-models")

# Finished videos are delivered here, one folder per video, with everything
# needed to upload: the film, a thumbnail, the description and tags, subtitles
# and credits. Kept off the repo drive so nothing large is ever near git.
DELIVERY_ROOT = Path("D:/TheArtOfChaosVideos")

# Working material -- script, storyboard, narration, frames, clips -- lives in
# the video's own folder, under `sources`, rather than in the repository. It
# ran to 1.7 GB across five projects, which has no business sitting next to
# the code, and the .gitignore rules keeping it out of commits were one
# careless `git add -f` from failing. Everything that made a video now travels
# with it.
SOURCES_NAME = "sources"


def video_dir(slug: str) -> Path:
    """The folder for one video: the delivery, with its sources inside.

    Matches an existing folder called `<slug>` or `<anything>_<slug>`. The
    delivery folders are numbered by release order on disk -- 0_depression,
    1_chronicles -- and the pipeline has no business renaming them back or
    creating a duplicate beside them.
    """
    exact = DELIVERY_ROOT / slug
    if exact.is_dir():
        return exact
    if DELIVERY_ROOT.is_dir():
        suffixed = sorted(p for p in DELIVERY_ROOT.iterdir()
                          if p.is_dir() and p.name.endswith(f"_{slug}"))
        if suffixed:
            return suffixed[-1]
    return exact


def project_dir(slug: str) -> Path:
    return video_dir(slug) / SOURCES_NAME

# YouTube's thumbnail size. It has to survive being shown 200 px wide.
THUMB_W, THUMB_H = 1280, 720
THUMB_FONT = "segoeuib.ttf"
THUMB_MAX_TEXT_SIZE = 150

# --- ComfyUI ---------------------------------------------------------------
COMFY_HOST = "127.0.0.1"
COMFY_PORT = 8188
COMFY_URL = f"http://{COMFY_HOST}:{COMFY_PORT}"

# --- Video output ----------------------------------------------------------
FPS = 30
OUT_W, OUT_H = 1920, 1080

# Frames are delivered above video resolution so a Ken Burns zoom is still
# showing real pixels at its tightest. Max zoom is ~1.16, so 1920 * 1.16
# rounds up to 2304x1296.
#
# That headroom is only worth paying for when the camera actually moves. A
# cut-driven video holds each frame still for two to four seconds, so the
# frame is shown 1:1 and every pixel above 1920x1080 is thrown away. Set
# STILL_FRAMES and GEN drops to output size, which takes about a third off
# the render: the pan was what demanded the extra pixels, not the picture.
STILL_FRAMES = True
GEN_W, GEN_H = (OUT_W, OUT_H) if STILL_FRAMES else (2304, 1296)

# But they are *composed* near FLUX's training resolution and enlarged
# afterwards. Asking the model directly for 2.99 MP does not produce a bigger
# picture, it produces a different and worse one: at 3x native the model stops
# composing a scene and starts repeating and fusing local structure. A dozen
# keys around a keyhole came back as one melted mass, a valve wheel as pulp, a
# seahorse fused into the calipers holding it.
#
# Measured across 1.03 / 1.43 / 1.84 / 2.36 / 2.99 MP on the same seeds: 1.43
# is the largest that still holds together on every case. Damage starts at
# 1.84 and is total by 2.99.
#
# 1536x864 sits just under that ceiling and is exactly 16:9 on a multiple of
# 16, so the enlargement to GEN is a clean 1.5x with no reframing. At 1.5x
# plain resampling is indistinguishable from a 4x ESRGAN in a 1:1 crop, and
# four times faster. See AGENTS.md.
COMPOSE_W, COMPOSE_H = 1536, 864

# --- Shot pacing -----------------------------------------------------------
# A shot lasts exactly as long as its narration line. These bounds catch
# storyboards that chunked the script badly.
#
# The floor was 2.5 s when every shot carried a slow pan and needed time to
# read. Cut-driven pacing deliberately runs shorter than that, so the floor
# drops with it; the ceiling is what matters now, because a held still frame
# with no motion is what makes a video feel like a slideshow.
MIN_SHOT_SEC = 1.2 if STILL_FRAMES else 2.5
MAX_SHOT_SEC = 12.0
# Breathing room appended after each line. 0.35 s is a natural beat between
# paragraph-length shots; after a two-second line it is a stutter, and across
# 235 shots it adds a minute and a half of silence to the video. Under cuts
# the voice should run continuously and the picture should change underneath
# it, so the tail shrinks to just enough to keep the splice from clicking.
SHOT_TAIL_SEC = 0.12 if STILL_FRAMES else 0.35
CROSSFADE_SEC = 0.5

# Ease the camera in and out of each move instead of snapping to full speed.
MOTION_EASING = True

# Clips are rendered in parallel processes. Each one also runs a multithreaded
# encoder, so this stays well below the core count.
CLIP_WORKERS = 8

# --- Text to speech --------------------------------------------------------
# Kokoro-82M, Apache-2.0. American male narrator by default.
TTS_VOICE = "am_michael"
TTS_SPEED = 1.0
TTS_SAMPLE_RATE = 24000

# --- End card --------------------------------------------------------------
# A video that stops the instant the narration does feels cut off. The outro is
# appended after the last shot, so it sits outside the narration timeline and
# does not disturb any of the crossfade arithmetic.
OUTRO_ENABLED = True
OUTRO_SECONDS = 6.0
OUTRO_TEXT = "Subscribe for more"
OUTRO_SUBTEXT = ""            # optional second line, e.g. a channel name
# Drawn with Pillow, not ffmpeg's drawtext, which segfaults in this build for
# want of a fontconfig default. Looked up in assets/fonts first, then Windows.
OUTRO_FONT = "segoeuib.ttf"
OUTRO_FONT_FALLBACK = "arialbd.ttf"
OUTRO_TEXT_SIZE = 96
OUTRO_SUBTEXT_SIZE = 40
OUTRO_SCRIM = 0.68            # how far the background is pushed toward black
OUTRO_FADE_OUT = 1.2          # fade to black over the closing seconds

# --- Subtitles -------------------------------------------------------------
# Burned in from a generated .ass, never from the .srt. libass assumes a
# 288-line canvas for SRT input and scales the font by 1080/288, so a nominal
# "size 22" arrived on screen 82 px tall and swallowed a third of the frame.
# An .ass carrying an explicit PlayRes means the number below is real pixels.
BURN_SUBTITLES = True
SUB_FONT = "Arial"
SUB_FONT_SIZE = 34            # real pixels at 1080p
SUB_MARGIN_V = 60             # from the bottom edge
SUB_MARGIN_H = 200            # keeps lines off the sides, forces earlier wrap
SUB_MAX_CHARS = 54            # per line; longer narration pages into more cues
                              # (measured ~14 px/char at size 34, so a full line
                              #  is ~760 px of the 1520 px between the margins)
# White text sat unreadable over bright water with a thin outline. A heavier
# black outline plus a soft shadow survives any background without needing an
# opaque box behind the text.
SUB_OUTLINE = 3
SUB_SHADOW = 1

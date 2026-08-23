"""Content factory pipeline.

Every module here is run as a console script on Windows, where stdout defaults
to cp1252 and dies on the first accented artist name or typographic dash. The
narration is English but the metadata coming back from museum APIs is not, so
the streams are forced to UTF-8 once, here, rather than in five separate
main() functions.

Line buffering is forced at the same time. Redirected to a file, Python block
buffers stdout, so a step that prints progress after every frame writes
nothing at all until it finishes. That went unnoticed for a long time because
the runs that were being watched were the ones that crashed, and a crash
flushes on exit -- so the log always looked complete. A twelve-minute render
that reports nothing while it works is indistinguishable from a hung one.
"""
import sys

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace",
                            line_buffering=True)

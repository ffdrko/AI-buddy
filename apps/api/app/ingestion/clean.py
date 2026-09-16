"""Conservative text cleaning for extracted PDF text.

Rules (BUILD_FLOW Phase 2): dehyphenate, fix ligatures, collapse whitespace.
Preserves paragraph boundaries (blank lines). Never drops content.
"""

import re
import unicodedata

_LIGATURES = {
    "ﬁ": "fi",
    "ﬂ": "fl",
    "ﬀ": "ff",
    "ﬃ": "ffi",
    "ﬄ": "ffl",
    "ﬅ": "ft",
    "ﬆ": "st",
    "æ": "ae",
    "œ": "oe",
}


def dehyphenate(text: str) -> str:
    """Join words split by end-of-line hyphens: 'sys-\\ntem' -> 'system'."""
    return re.sub(r"(\w)-\n(\w)", r"\1\2", text)


def fix_ligatures(text: str) -> str:
    for lig, repl in _LIGATURES.items():
        text = text.replace(lig, repl)
    return unicodedata.normalize("NFKC", text)


def collapse_whitespace(text: str) -> str:
    # Normalise line endings, then collapse 3+ newlines to a paragraph break.
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\n{3,}", "\n\n", text)
    # Collapse horizontal whitespace; strip each line's trailing spaces.
    lines = [re.sub(r"[ \t\f\v]+", " ", ln).rstrip() for ln in text.split("\n")]
    return "\n".join(lines).strip()


def clean_text(text: str) -> str:
    """Full pipeline: dehyphenate -> ligatures -> whitespace. Idempotent."""
    return collapse_whitespace(fix_ligatures(dehyphenate(text)))

"""Phrase normalization, identical in Python and TypeScript.

Gate rules match phrases as substrings of normalized text. To make Python and
the browser runtime agree on every input, the normal form uses only operations
both languages define the same way:

  1. Unicode NFKC (full-width letters become ASCII, compatibility forms fold);
  2. lower-case ASCII letters only (``A``-``Z``), never locale or full Unicode
     case folding, which differ between Python and JavaScript;
  3. collapse runs of U+0020 into one and strip U+0020 at both ends.

No regular expressions in rules (v1): that keeps out ReDoS and the differences
between Python and JavaScript regex dialects.
"""
from __future__ import annotations

import re
import unicodedata

_ASCII_UPPER = {c: c + 32 for c in range(ord("A"), ord("Z") + 1)}
_SPACES = re.compile(" +")
_CONTROL = re.compile("[\x00-\x1f\x7f]")


def normalize_phrase(s: str) -> str:
    out = unicodedata.normalize("NFKC", s).translate(_ASCII_UPPER)
    return _SPACES.sub(" ", out).strip(" ")


def has_control(s: str) -> bool:
    return _CONTROL.search(s) is not None


def contains_phrase(text: str, phrase: str) -> bool:
    """Whether normalized ``text`` contains normalized ``phrase``."""
    p = normalize_phrase(phrase)
    return bool(p) and p in normalize_phrase(text)

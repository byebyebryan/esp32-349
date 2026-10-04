"""Pinned Simplified Chinese repertoire for the notification text fonts."""

from __future__ import annotations

import hashlib
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]
CHINESE_TABLE = PROJECT / "main/fonts/repertoires/tgh-2013-level1.txt"
CHINESE_TABLE_SHA256 = "4caca78057eb62257ed475e0a140b02a58c5907fd054613a93cfdec3a7df6320"
CHINESE_PUNCTUATION = {ord(char) for char in "　、。，！？：；（）【】《》〈〉「」『』"}


def simplified_chinese() -> set[int]:
    """Read the first 3,500 kTGH 2013 entries extracted from Unicode 17.0.0."""
    data = CHINESE_TABLE.read_bytes()
    if hashlib.sha256(data).hexdigest() != CHINESE_TABLE_SHA256:
        raise ValueError(f"pinned Chinese repertoire changed: {CHINESE_TABLE}")
    characters = "".join(data.decode("utf-8").split())
    codepoints = set(map(ord, characters))
    if len(characters) != 3500 or len(codepoints) != 3500:
        raise ValueError("Chinese repertoire must contain exactly 3,500 distinct characters")
    if any(not 0x4E00 <= codepoint <= 0x9FFF for codepoint in codepoints):
        raise ValueError("Chinese repertoire contains a character outside the Han block")
    return codepoints

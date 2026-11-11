"""Lets a label break Thai text between words.

Thai is written without spaces between words, and Qt on this machine does not break a line
inside a run of Thai letters: a sentence with no spaces runs past the edge of its box and
is cut off there. A zero-width space (invisible, and a place where a line may break) between
the words fixes that without changing how the text looks.
"""
from __future__ import annotations

import re
from functools import lru_cache

ZERO_WIDTH_SPACE = "​"
_THAI_RUN = re.compile(r"[ก-๛]{2,}")
# A line may not start with these: vowels and tone marks that sit on the letter before,
# the repetition mark ๆ, and the abbreviation mark ฯ.
_NEVER_STARTS_A_LINE = set("ะัาำิีึืฺุูๅๆ็"
                           "่้๊๋์ํ๎ฯ")


def _words(run: str) -> list[str]:
    try:
        from pythainlp.tokenize import word_tokenize
    except ImportError:
        return [run]
    return word_tokenize(run, engine="newmm")


@lru_cache(maxsize=256)
def soft_wrap(text: str) -> str:
    """``text`` with a zero-width space between the Thai words in it."""

    def mark(match: re.Match) -> str:
        pieces: list[str] = []
        for word in _words(match.group()):
            if pieces and word and word[0] not in _NEVER_STARTS_A_LINE:
                pieces.append(ZERO_WIDTH_SPACE)
            pieces.append(word)
        return "".join(pieces)

    return _THAI_RUN.sub(mark, text)

"""One log line per spoken turn: where the time between "the visitor stopped" and "the robot spoke" went.

    Turn timing: waited 1.00 s for the end | transcript +0.62 s | answer +2.10 s | first sound +2.65 s

Every number is seconds after the visitor's last word, so they can be compared turn to turn. The
parts call ``mark`` when they pass a stage; nothing here can fail a turn (it only keeps a few floats).
"""
from __future__ import annotations

import logging
import threading
import time

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_marks: dict[str, float] = {}


def begin(speech_ended_at: float, decided_at: float) -> None:
    """A turn's speech is over: ``speech_ended_at`` is the last moment of speech, ``decided_at`` when we knew.

    Stages marked since that last word stay (the trial transcription is ready before the end is
    decided); anything older belongs to an earlier turn and is dropped.
    """
    with _lock:
        for stage in [name for name, at in _marks.items() if at < speech_ended_at]:
            del _marks[stage]
        _marks["speech_end"] = speech_ended_at
        _marks["decided"] = decided_at


def mark(stage: str, at: float | None = None) -> None:
    """Record a stage ("transcript", "answer") the first time it is reached in a turn."""
    with _lock:
        if stage not in _marks:
            _marks[stage] = time.monotonic() if at is None else at


def finish(at: float | None = None) -> None:
    """The robot's first sound: log the turn and forget it."""
    with _lock:
        if "speech_end" not in _marks:
            return
        marks = dict(_marks)
        _marks.clear()
    now = time.monotonic() if at is None else at
    end = marks["speech_end"]

    def after(stage: str) -> str:
        return f"+{marks[stage] - end:.2f} s" if stage in marks else "-"

    logger.info(
        "Turn timing: waited %.2f s for the end | transcript %s | answer %s | first sound +%.2f s",
        marks["decided"] - end, after("transcript"), after("answer"), now - end,
    )

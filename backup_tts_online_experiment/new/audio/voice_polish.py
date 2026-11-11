"""Finishing touches for every piece of speech, whichever voice made it, so they sound like one voice.

* ``tighten_pauses``: Thai text from the AI is full of spaces, and a neural voice may rest at each of them. A
  silence inside a piece longer than ``max_gap_s`` is shortened to it (the pause the robot means between
  pieces is added by the speech controller, where it knows what kind of break it is).
* ``level``: one loudness for all voices and for the sound effects, soft rather than loud: a target
  of about -23 dBFS RMS with a gentle limiter, so nothing jumps out and nothing is lost.
* ``fade_edges``: 12 ms in and out, no clicks where pieces and cues join.
"""
from __future__ import annotations

import numpy as np

SAMPLE_RATE = 24_000
FRAME_S = 0.01
QUIET_LEVEL = 0.006  # frame RMS under this counts as a pause (the fading end of "ครับ" is louder)
MAX_GAP_S = 0.26
KEEP_EDGE_S = 0.04  # of the quiet kept at each end of a shortened pause, so the cut is not audible
TARGET_RMS = 0.07  # about -23 dBFS: speech at a comfortable, soft level
MAX_GAIN = 6.0
FADE_S = 0.012


def _frame_rms(audio: np.ndarray, frame: int) -> np.ndarray:
    count = len(audio) // frame
    if count == 0:
        return np.zeros(0, dtype=np.float32)
    return np.sqrt(np.mean(audio[: count * frame].reshape(count, frame) ** 2, axis=1))


def longest_pause(audio: np.ndarray, sample_rate: int = SAMPLE_RATE) -> float:
    """The longest quiet stretch strictly inside the speech, in seconds (leading and trailing quiet not counted)."""
    frame = max(1, int(FRAME_S * sample_rate))
    quiet = _frame_rms(audio, frame) < QUIET_LEVEL
    loud = np.flatnonzero(~quiet)
    if len(loud) < 2:
        return 0.0
    longest = run = 0
    for is_quiet in quiet[loud[0]:loud[-1] + 1]:
        run = run + 1 if is_quiet else 0
        longest = max(longest, run)
    return longest * FRAME_S


def tighten_pauses(audio: np.ndarray, sample_rate: int = SAMPLE_RATE, max_gap_s: float = MAX_GAP_S) -> np.ndarray:
    """Shorten every silence inside ``audio`` that is longer than ``max_gap_s`` down to ``max_gap_s``."""
    frame = max(1, int(FRAME_S * sample_rate))
    rms = _frame_rms(audio, frame)
    quiet = rms < QUIET_LEVEL
    loud = np.flatnonzero(~quiet)
    if len(loud) < 2:
        return audio
    first, last = loud[0], loud[-1]
    limit = int(round(max_gap_s / FRAME_S))
    keep = np.ones(len(rms), dtype=bool)
    run_start = None
    for index in range(first, last + 2):
        is_quiet = index <= last and quiet[index]
        if is_quiet and run_start is None:
            run_start = index
        elif not is_quiet and run_start is not None:
            length = index - run_start
            if length > limit:
                edge = int(round(KEEP_EDGE_S / FRAME_S))
                cut_from, cut_to = run_start + max(edge, limit // 2), index - max(edge, limit - limit // 2)
                if cut_to > cut_from:
                    keep[cut_from:cut_to] = False
            run_start = None
    if keep.all():
        return audio
    pieces = []
    for index in range(len(rms)):
        if keep[index]:
            pieces.append(audio[index * frame:(index + 1) * frame])
    pieces.append(audio[len(rms) * frame:])
    return np.concatenate(pieces).astype(np.float32)


def level(audio: np.ndarray, target_rms: float = TARGET_RMS) -> np.ndarray:
    """Bring the speech to one soft loudness; the quiet parts do not count, so pauses cannot inflate the gain."""
    if len(audio) == 0:
        return audio
    frame = max(1, int(FRAME_S * SAMPLE_RATE))
    rms = _frame_rms(audio, frame)
    voiced = rms[rms >= QUIET_LEVEL]
    if len(voiced) == 0:
        return audio
    current = float(np.sqrt(np.mean(voiced ** 2)))
    gain = min(MAX_GAIN, target_rms / max(current, 1e-6))
    out = audio * gain
    peak = float(np.max(np.abs(out)))
    if peak > 0.9:  # a gentle limiter: bends the peaks, never clips them
        out = np.tanh(out / 0.9) * 0.9
    return out.astype(np.float32)


def fade_edges(audio: np.ndarray, sample_rate: int = SAMPLE_RATE, seconds: float = FADE_S) -> np.ndarray:
    count = min(int(seconds * sample_rate), len(audio) // 2)
    if count <= 0:
        return audio
    out = audio.copy()
    ramp = np.linspace(0.0, 1.0, count, dtype=np.float32)
    out[:count] *= ramp
    out[-count:] *= ramp[::-1]
    return out


def polish(audio: np.ndarray, sample_rate: int = SAMPLE_RATE) -> np.ndarray:
    """Tighten pauses, level and fade one piece of speech."""
    return fade_edges(level(tighten_pauses(audio, sample_rate)), sample_rate)

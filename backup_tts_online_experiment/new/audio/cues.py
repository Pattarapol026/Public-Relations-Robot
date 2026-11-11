"""The small sounds of a person: a chuckle, a laugh, a sigh, "hmm", "oh!", "wow".

Made once by ``tools/make_voice_cues.py`` into ``media/voice_cues/`` (``chuckle_1.wav``, ``hmm_th.wav``, ...), then
played from there with no network. Several takes of a laugh exist so the robot does not laugh the same way
twice in a row; "hmm", "oh" and "wow" have one for each language. A sound that was not made is simply
missing: ``get`` returns None and the robot goes on without it.
"""
from __future__ import annotations

import logging
import random
import re
import wave
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

SAMPLE_RATE = 24_000
_FILE = re.compile(r"^(?P<name>[a-z]+)_(?P<kind>[a-z0-9]+)\.wav$")


class CueLibrary:
    def __init__(self, folder: Path | str, rng: random.Random | None = None) -> None:
        self._folder = Path(folder)
        self._rng = rng or random.Random()
        self._files: dict[str, dict[str, Path]] | None = None
        self._cache: dict[Path, np.ndarray] = {}
        self._last: dict[str, str] = {}

    def _scan(self) -> dict[str, dict[str, Path]]:
        if self._files is None:
            self._files = {}
            for path in sorted(self._folder.glob("*.wav")) if self._folder.is_dir() else []:
                match = _FILE.match(path.name)
                if match:
                    self._files.setdefault(match["name"], {})[match["kind"]] = path
        return self._files

    def names(self) -> list[str]:
        return sorted(self._scan())

    def has(self, name: str) -> bool:
        return name in self._scan()

    def get(self, name: str, language: str = "th") -> np.ndarray | None:
        """A take of ``name``: the one in ``language`` if there is one, else one of the others (not the one used last)."""
        takes = self._scan().get(name)
        if not takes:
            return None
        if language in takes:
            kind = language
        else:
            options = [kind for kind in takes if kind.isdigit()] or list(takes)
            fresh = [kind for kind in options if kind != self._last.get(name)] or options
            kind = self._rng.choice(fresh)
        self._last[name] = kind
        return self._load(takes[kind])

    def _load(self, path: Path) -> np.ndarray | None:
        cached = self._cache.get(path)
        if cached is not None:
            return cached
        try:
            with wave.open(str(path), "rb") as handle:
                if handle.getsampwidth() != 2 or handle.getnchannels() != 1:
                    raise ValueError("expected 16-bit mono")
                rate = handle.getframerate()
                audio = np.frombuffer(handle.readframes(handle.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
        except (OSError, ValueError, wave.Error):
            logger.warning("Could not read the sound %s", path, exc_info=True)
            return None
        if rate != SAMPLE_RATE:
            audio = np.interp(np.linspace(0, len(audio) - 1, int(len(audio) * SAMPLE_RATE / rate)), np.arange(len(audio)), audio).astype(np.float32)
        self._cache[path] = audio
        return audio

"""Speech kept on disk, so a line that was spoken once (or prepared in advance) plays at once and without the network.

One small file per piece of speech (16-bit, 24 kHz, compressed), named by a hash of everything that
makes it sound the way it does: the engine, the voice, the speed, the text. The oldest are deleted when the
folder grows past its limit. Nothing here ever raises: a cache that cannot be read is just a miss.
"""
from __future__ import annotations

import hashlib
import logging
import os
import threading
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)


class VoiceCache:
    def __init__(self, folder: Path | str, max_megabytes: float = 300.0) -> None:
        self._folder = Path(folder)
        self._max_bytes = int(max_megabytes * 1024 * 1024)
        self._lock = threading.Lock()
        self._writes = 0

    @staticmethod
    def key(*parts: object) -> str:
        return hashlib.sha1("\x1f".join(str(part) for part in parts).encode("utf-8")).hexdigest()

    def _path(self, key: str) -> Path:
        return self._folder / f"{key}.npz"

    def get(self, key: str) -> np.ndarray | None:
        path = self._path(key)
        try:
            with np.load(path) as data:
                audio = data["a"].astype(np.float32) / 32768.0
            os.utime(path)  # recently used: deleted last
            return audio
        except (OSError, ValueError, KeyError):
            return None

    def put(self, key: str, audio: np.ndarray) -> None:
        try:
            self._folder.mkdir(parents=True, exist_ok=True)
            pcm = np.clip(audio * 32768.0, -32768, 32767).astype(np.int16)
            temporary = self._path(key).with_suffix(".tmp")
            with open(temporary, "wb") as handle:
                np.savez_compressed(handle, a=pcm)
            os.replace(temporary, self._path(key))
        except OSError:
            logger.warning("Could not store speech in the cache", exc_info=True)
            return
        with self._lock:
            self._writes += 1
            check = self._writes % 25 == 0
        if check:
            self._trim()

    def _trim(self) -> None:
        try:
            files = sorted(self._folder.glob("*.npz"), key=lambda p: p.stat().st_mtime)
            total = sum(p.stat().st_size for p in files)
            for path in files:
                if total <= self._max_bytes:
                    break
                total -= path.stat().st_size
                path.unlink(missing_ok=True)
        except OSError:
            logger.warning("Could not trim the speech cache", exc_info=True)

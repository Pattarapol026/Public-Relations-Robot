"""Online neural voices (Microsoft "Read Aloud", through the ``edge-tts`` package, verified with 7.x).

Far more natural than the voices that run on this computer, and quick: on 8 Nov 2026 a short sentence
began to arrive after 0.4 - 0.65 s and was complete after 0.6 - 0.9 s (a few first calls on a cold
connection took 2 - 8 s, which is why callers give the first piece of an answer a short time limit and
then fall back to the voices in this computer).

It is an unofficial endpoint of the Edge browser. If it ever answers 403, update ``edge-tts``; if the
network is down, the caller (``TextToSpeechService``) stops asking for a while (``OnlineGate``) and the
local voices speak. Nothing here ever raises anything but ``EdgeVoiceError``.
"""
from __future__ import annotations

import asyncio
import io
import logging
import threading
import time

import numpy as np

logger = logging.getLogger(__name__)

SAMPLE_RATE = 24_000
DEFAULT_TIMEOUT_S = 10.0
# A request still unanswered after this long gets a second one beside it, and whichever finishes first is used
# (a connection that stalls is the usual cause of a slow piece; a new one is rarely stuck as well).
HEDGE_AFTER_S = 2.0


class EdgeVoiceError(Exception):
    """The online voice could not produce speech (offline, refused, too slow, nothing came back)."""


class EdgeVoice:
    """``synthesize`` blocks (call it from a worker thread) and returns mono float32 at 24 kHz."""

    def __init__(self, timeout_s: float = DEFAULT_TIMEOUT_S, hedge_after_s: float = HEDGE_AFTER_S) -> None:
        self._timeout_s = timeout_s
        self._hedge_after_s = hedge_after_s

    def synthesize(
        self, text: str, voice: str, rate_percent: int = 0, pitch_hz: int = 0, timeout_s: float | None = None,
        starts: tuple[float, ...] | None = None,
    ) -> np.ndarray:
        """Speech for ``text``. ``starts`` are the times (seconds from now) at which a request is sent: ``(0.0, 2.0)``
        is one at once and another if the first has not answered by 2 s; ``(0.0, 0.0, 1.2)`` is for a voice that
        often fails (two at once). The first to come back with audio wins and the rest are dropped."""
        limit = self._timeout_s if timeout_s is None else timeout_s
        try:
            data = asyncio.run(asyncio.wait_for(
                self._race(text, voice, rate_percent, pitch_hz, starts or (0.0, self._hedge_after_s)), limit))
        except asyncio.TimeoutError as exc:
            raise EdgeVoiceError(f"timed out after {limit:.1f} s") from exc
        except EdgeVoiceError:
            raise
        except Exception as exc:  # noqa: BLE001 - network errors, 403, a changed protocol: all mean "not now"
            raise EdgeVoiceError(f"{type(exc).__name__}: {exc}") from exc
        if not data:
            raise EdgeVoiceError("no audio came back")
        return decode_mp3(data)

    async def _race(
        self, text: str, voice: str, rate_percent: int, pitch_hz: int, starts: tuple[float, ...] = (0.0, HEDGE_AFTER_S),
    ) -> bytes:
        """Requests sent at the ``starts`` times; the first one to finish with audio wins.

        A request that FAILS makes the next one start at once instead of at its time: waiting for the clock
        while knowing the first is dead only costs time.
        """
        pending = sorted(starts) or [0.0]
        tasks: list[asyncio.Future] = []
        began = asyncio.get_running_loop().time()
        error: BaseException | None = None

        def send() -> None:
            pending.pop(0)
            tasks.append(asyncio.ensure_future(self._fetch(text, voice, rate_percent, pitch_hz)))

        try:
            while pending or tasks:
                now = asyncio.get_running_loop().time() - began
                while pending and pending[0] <= now:
                    if tasks:
                        logger.info("Online voice slow or failing: another request is made beside the others")
                    send()
                if not tasks:
                    await asyncio.sleep(max(0.0, pending[0] - now))
                    continue
                wait = (pending[0] - now) if pending else None
                done, _still = await asyncio.wait(tasks, timeout=wait, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    tasks.remove(task)
                    if task.cancelled():
                        continue
                    if task.exception() is None and task.result():
                        return task.result()
                    error = task.exception() or EdgeVoiceError("no audio came back")
                    if pending and not tasks:
                        pending[0] = 0.0  # the only request failed: the next one goes now
            raise error or EdgeVoiceError("no audio came back")
        finally:
            for task in tasks:
                task.cancel()

    async def _fetch(self, text: str, voice: str, rate_percent: int, pitch_hz: int) -> bytes:
        import edge_tts

        communicate = edge_tts.Communicate(text, voice, rate=f"{rate_percent:+d}%", pitch=f"{pitch_hz:+d}Hz")
        chunks = []
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                chunks.append(chunk["data"])
        return b"".join(chunks)


def decode_mp3(data: bytes) -> np.ndarray:
    """MP3 bytes -> mono float32 at 24 kHz (PyAV, already installed for speech-to-text)."""
    import av

    try:
        container = av.open(io.BytesIO(data))
        resampler = av.AudioResampler(format="flt", layout="mono", rate=SAMPLE_RATE)
        parts = []
        for frame in container.decode(audio=0):
            for out in resampler.resample(frame):
                parts.append(out.to_ndarray().reshape(-1))
        for out in resampler.resample(None):
            parts.append(out.to_ndarray().reshape(-1))
        container.close()
    except Exception as exc:  # noqa: BLE001
        raise EdgeVoiceError(f"could not decode the audio: {exc}") from exc
    if not parts:
        raise EdgeVoiceError("the audio was empty")
    return np.ascontiguousarray(np.concatenate(parts), dtype=np.float32)


class OnlineGate:
    """A circuit breaker: after two failures in a row the online voice is left alone for a while.

    The pause doubles with every further failure (45 s up to 5 minutes), and one success clears it. While it is
    closed every answer is spoken by the local voice at once, instead of each one waiting for a timeout.
    """

    def __init__(self, failures_to_close: int = 2, first_pause_s: float = 45.0, max_pause_s: float = 300.0,
                 clock=time.monotonic) -> None:
        self._failures_to_close = failures_to_close
        self._first_pause_s = first_pause_s
        self._max_pause_s = max_pause_s
        self._clock = clock
        self._failures = 0
        self._closed_until = 0.0
        self._lock = threading.Lock()

    def allows(self) -> bool:
        with self._lock:
            return self._clock() >= self._closed_until

    def success(self) -> None:
        with self._lock:
            self._failures = 0
            self._closed_until = 0.0

    def failure(self) -> None:
        with self._lock:
            self._failures += 1
            if self._failures >= self._failures_to_close:
                pause = min(self._max_pause_s, self._first_pause_s * 2 ** (self._failures - self._failures_to_close))
                self._closed_until = self._clock() + pause
                logger.warning("Online voice left alone for %.0f s after %d failures", pause, self._failures)

    @property
    def failures(self) -> int:
        return self._failures

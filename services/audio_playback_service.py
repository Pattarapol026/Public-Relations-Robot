"""Plays speech through a chosen output device with sounddevice (PortAudio).

Audio is fed in pieces: playback starts with the first piece while later ones
are still being synthesised. The PortAudio callback runs on its own thread and
only copies samples; callers poll ``is_active``.

Choosing the device matters for echo cancellation: a microphone array such as
the ReSpeaker XVF3800 can only remove the robot's own voice from what it hears
if that voice is played through the array's own audio output.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque

import numpy as np

from audio.microphone import AudioDevice, resample

logger = logging.getLogger(__name__)

# The callback hands samples to the driver, which plays them later: closing the
# stream as soon as the queue is empty threw away the end of every sentence (0.18 s
# measured on MME, enough to lose the "-ับ" of "ครับ"). Silence is played for the
# stream's latency plus this margin before the speech counts as played.
TAIL_MARGIN_S = 0.15
# No callback for this long means the device has gone (unplugged, driver failure).
STALL_S = 3.0


class PlaybackError(Exception):
    """The output device could not be opened; the message is safe to show on screen."""


def _sounddevice():
    try:
        import sounddevice
    except (ImportError, OSError) as exc:
        raise PlaybackError("ระบบเสียงไม่พร้อมใช้งาน (ไม่พบ sounddevice/PortAudio)") from exc
    return sounddevice


class AudioPlaybackService:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._stream = None
        self._pending: deque[np.ndarray] = deque()
        self._position = 0  # read offset inside the first pending piece
        self._finishing = False  # no more pieces will be fed
        self._drained = threading.Event()
        self._stream_rate = 0.0
        self._played = False  # the driver has been given some audio since ``start``
        self._tail_frames = 0  # silence still to play after the last piece
        self._last_callback = 0.0

    def list_output_devices(self) -> list[AudioDevice]:
        """Output devices of the system's default host API."""
        sd = _sounddevice()
        try:
            host_api_index = sd.default.hostapi
            default_output = sd.query_hostapis(host_api_index)["default_output_device"]
            return [
                AudioDevice(name=device["name"], is_default=index == default_output)
                for index, device in enumerate(sd.query_devices())
                if device["max_output_channels"] > 0 and device["hostapi"] == host_api_index
            ]
        except Exception as exc:
            raise PlaybackError("อ่านรายการลำโพงไม่ได้") from exc

    @property
    def is_active(self) -> bool:
        """True from ``start`` until everything fed has been played (or ``stop``)."""
        return self._stream is not None and not self._drained.is_set()

    @property
    def failure(self) -> str | None:
        """Why playback can no longer finish, while it is meant to be playing."""
        stream = self._stream
        if stream is None or self._drained.is_set():
            return None
        stalled = self._last_callback and time.monotonic() - self._last_callback > STALL_S
        if stalled or not getattr(stream, "active", True):
            return "ลำโพงหยุดทำงานระหว่างพูด กรุณาตรวจสอบอุปกรณ์เสียงออก"
        return None

    def start(self, sample_rate: int, device_name: str | None = None) -> None:
        """Open the output device, ready for ``feed``. Raises ``PlaybackError``."""
        sd = _sounddevice()
        self.stop()
        try:
            device = self._resolve_device(sd, device_name)
            # Use the speech's own rate if the driver takes it; else the device's rate.
            stream_rate = float(sample_rate)
            try:
                sd.check_output_settings(
                    device=device, channels=1, dtype="float32", samplerate=stream_rate
                )
            except Exception:
                stream_rate = float(sd.query_devices(device, "output")["default_samplerate"])
            with self._lock:
                self._pending.clear()
                self._position = 0
                self._finishing = False
                self._stream_rate = stream_rate
                self._played = False
            self._drained.clear()
            # "high" latency: larger buffers, so a busy moment (a model loading back, the
            # camera, the UI) does not leave the speaker without audio and break the voice.
            # Stopping is still immediate (abort), and speech starts ~0.1 s later.
            stream = sd.OutputStream(
                samplerate=stream_rate, device=device, channels=1, dtype="float32",
                callback=self._on_audio, latency="high",
            )
            self._tail_frames = int((stream.latency + TAIL_MARGIN_S) * stream_rate)
            self._last_callback = time.monotonic()
            stream.start()
        except PlaybackError:
            raise
        except Exception as exc:
            logger.exception("Could not open the audio output")
            raise PlaybackError("เปิดลำโพงไม่ได้ กรุณาตรวจสอบอุปกรณ์เสียงออก") from exc
        self._stream = stream

    def feed(self, samples: np.ndarray, sample_rate: int) -> None:
        """Queue mono float32 audio to play after what is already queued."""
        if self._stream is None or len(samples) == 0:
            return
        samples = resample(samples, sample_rate, int(self._stream_rate))
        with self._lock:
            self._pending.append(samples)

    def finish(self) -> None:
        """Nothing more will be fed: ``is_active`` turns False once the speaker has played it all."""
        with self._lock:
            self._finishing = True
            if not self._pending and not self._played:
                self._drained.set()

    def stop(self) -> None:
        """Stop immediately and discard anything not yet played."""
        stream, self._stream = self._stream, None
        with self._lock:
            self._pending.clear()
            self._position = 0
        self._drained.set()
        if stream is None:
            return
        try:
            stream.abort()
            stream.close()
        except Exception:
            logger.exception("Error while closing the audio output")

    def _resolve_device(self, sd, device_name: str | None) -> int | None:
        if not device_name:
            return None
        host_api_index = sd.default.hostapi
        for index, device in enumerate(sd.query_devices()):
            if (
                device["name"] == device_name
                and device["hostapi"] == host_api_index
                and device["max_output_channels"] > 0
            ):
                return index
        logger.warning("Saved audio output %r not found; using the system default", device_name)
        return None

    def _on_audio(self, outdata, frames, _time, _status) -> None:
        # PortAudio thread: copy samples, no logging, no blocking work.
        self._last_callback = time.monotonic()
        written = 0
        with self._lock:
            while written < frames and self._pending:
                piece = self._pending[0]
                take = min(frames - written, len(piece) - self._position)
                outdata[written:written + take, 0] = piece[self._position:self._position + take]
                written += take
                self._position += take
                self._played = True
                if self._position >= len(piece):
                    self._pending.popleft()
                    self._position = 0
            if written < frames:
                outdata[written:, 0] = 0.0  # silence while waiting for the next piece
                if self._finishing and not self._pending:
                    self._tail_frames -= frames - written
                    if self._tail_frames <= 0:
                        self._drained.set()

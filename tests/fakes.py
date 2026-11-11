"""Scripted stand-ins for the microphone and speech-to-text, shared by several test modules."""
from __future__ import annotations

import threading

import numpy as np

from audio.microphone import AudioDevice, MicrophoneError

LOUD, QUIET = 0.2, 0.0


class FakeMicrophone:
    def __init__(self) -> None:
        self.level = QUIET
        self.peak = 0.004  # a live microphone always has some noise
        self.recording = False
        self.started_with: list[str | None] = []
        self.channels: list[int] = []
        self.stops = 0
        self.cancels = 0
        self.snapshots = 0
        self.fail_to_open = False
        self.probes = 0
        self.fallbacks: list[bool] = []
        self.device_label = ""

    def list_input_devices(self):
        return [AudioDevice("Fake Mic", True), AudioDevice("USB Mic", False)]

    def probe(self, device_name=None, channel=0) -> str:
        self.probes += 1
        if self.fail_to_open:
            raise MicrophoneError("เปิดไมโครโฟนไม่ได้")
        self.device_label = device_name or "Fake Mic"
        return self.device_label

    def start(self, device_name=None, channel=0, fallback=True) -> None:
        self.channels.append(channel)
        if self.fail_to_open:
            raise MicrophoneError("เปิดไมโครโฟนไม่ได้")
        self.started_with.append(device_name)
        self.fallbacks.append(fallback)
        self.device_label = device_name or "Fake Mic"
        self.recording = True

    def stop(self) -> np.ndarray:
        self.recording = False
        self.stops += 1
        return np.zeros(16_000, dtype=np.float32)

    def recent(self, seconds: float) -> np.ndarray:
        return np.zeros(int(16_000 * seconds), dtype=np.float32)

    def snapshot(self) -> np.ndarray:
        self.snapshots += 1
        return np.zeros(16_000, dtype=np.float32)

    def cancel(self) -> None:
        if self.recording:
            self.cancels += 1
        self.recording = False
        self.level = QUIET


class FakeStt:
    def __init__(self, text: str = "สมัครเรียนได้ที่ไหน") -> None:
        self.text = text
        self.load_error: Exception | None = None
        self.transcribe_error: Exception | None = None
        self.release = threading.Event()
        self.release.set()
        self.transcribed = 0
        self.languages: list[str | None] = []

    def load(self) -> None:
        if self.load_error:
            raise self.load_error

    def transcribe(self, audio: np.ndarray, language: str | None = None, cancelled=None) -> str:
        self.release.wait(5.0)
        if cancelled is not None and cancelled():
            return ""  # as the real one: a job nobody wants any more is not counted
        self.languages.append(language)
        self.transcribed += 1
        if self.transcribe_error:
            raise self.transcribe_error
        return self.text

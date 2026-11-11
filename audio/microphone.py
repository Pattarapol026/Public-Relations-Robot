"""Microphone capture with sounddevice (PortAudio).

Audio is kept in memory only and handed to speech-to-text; nothing is written
to disk. The PortAudio callback runs on its own thread, so it only appends
blocks and updates the current level — callers poll ``level`` and ``duration``.

A microphone counts as open only once audio blocks actually arrive. Windows
lists each microphone once per audio API (WASAPI, MME, DirectSound, WDM-KS),
and some of them can refuse while another works: with "Microphone access"
switched off in Windows privacy settings, every shared-mode API fails but
WDM-KS still reads the built-in microphone. So without a chosen device every
real input is tried in turn, and the one that works is remembered.
"""
from __future__ import annotations

import logging
import sys
import threading
import time
from dataclasses import dataclass

import numpy as np

logger = logging.getLogger(__name__)

TARGET_SAMPLE_RATE = 16_000  # what Whisper expects
BLOCK_SECONDS = 0.05
FIRST_BLOCK_TIMEOUT_S = 1.0  # an opened stream that delivers nothing by then is broken
SILENCE_CHECK_S = 0.3  # without a chosen device, look this long for any signal at all
PROBE_SECONDS = 0.3

# Preferred order of the Windows audio APIs; anything else comes after.
_HOST_API_ORDER = ("Windows WASAPI", "MME", "Windows DirectSound", "Windows WDM-KS")
_HOST_API_SHORT = {
    "Windows WASAPI": "WASAPI", "Windows DirectSound": "DirectSound", "Windows WDM-KS": "WDM-KS",
}
# Inputs that are not microphones: the speaker loopback and the MME/DirectSound aliases.
_NOT_MICROPHONES = (
    "stereo mix", "sound mapper", "primary sound capture", "what u hear", "loopback", "wave out mix",
)

PRIVACY_BLOCKED_MESSAGE = (
    "Windows ปิดสิทธิ์การใช้ไมโครโฟนอยู่ เปิดได้ที่ Settings > Privacy & security > Microphone "
    "แล้วเปิด \"Microphone access\" และ \"Let desktop apps access your microphone\""
)
NO_MICROPHONE_MESSAGE = "ไม่พบไมโครโฟนในเครื่อง กรุณาเสียบไมโครโฟนหรือเปิดใช้ไมโครโฟนในระบบ"
OPEN_FAILED_MESSAGE = (
    "เปิดไมโครโฟนไม่ได้ กรุณาตรวจสอบว่าไมโครโฟนเปิดใช้งานอยู่ และไม่มีโปรแกรมอื่นกำลังใช้ไมโครโฟน"
)


class MicrophoneError(Exception):
    """Capture could not start or failed; the message is safe to show on screen."""


@dataclass(frozen=True)
class AudioDevice:
    name: str
    is_default: bool


def _sounddevice():
    try:
        import sounddevice
    except (ImportError, OSError) as exc:  # OSError: PortAudio library missing
        raise MicrophoneError("ระบบเสียงไม่พร้อมใช้งาน (ไม่พบ sounddevice/PortAudio)") from exc
    return sounddevice


def windows_microphone_blocked() -> bool:
    """True if Windows privacy settings deny microphone access to desktop programs."""
    if sys.platform != "win32":
        return False
    import winreg

    store = r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\microphone"
    for root, key in (
        (winreg.HKEY_LOCAL_MACHINE, store),  # "Microphone access" for the whole device
        (winreg.HKEY_CURRENT_USER, store),
        (winreg.HKEY_CURRENT_USER, store + r"\NonPackaged"),  # "Let desktop apps access..."
    ):
        try:
            with winreg.OpenKey(root, key) as handle:
                value, _type = winreg.QueryValueEx(handle, "Value")
        except OSError:
            continue
        if str(value).lower() == "deny":
            return True
    return False


def _is_microphone(device: dict) -> bool:
    name = device["name"].lower()
    return device["max_input_channels"] > 0 and not any(word in name for word in _NOT_MICROPHONES)


def _api_rank(api_name: str) -> int:
    return _HOST_API_ORDER.index(api_name) if api_name in _HOST_API_ORDER else len(_HOST_API_ORDER)


def _device_key(device: dict, api_name: str) -> str:
    """The name saved in settings: the device name and which audio API it is reached through."""
    return f"{device['name']} [{_HOST_API_SHORT.get(api_name, api_name)}]"


def resample(samples: np.ndarray, source_rate: float, target_rate: int) -> np.ndarray:
    """Linear-interpolation resampling of mono float32 audio (speech quality is enough)."""
    if len(samples) == 0 or int(source_rate) == target_rate:
        return samples.astype(np.float32, copy=False)
    target_length = int(round(len(samples) * target_rate / source_rate))
    if target_length == 0:
        return np.zeros(0, dtype=np.float32)
    # Output sample i is taken at the same instant in the source: i * (source / target).
    source_positions = np.arange(target_length) * (source_rate / target_rate)
    return np.interp(source_positions, np.arange(len(samples)), samples).astype(np.float32)


class MicrophoneService:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        # Held while a device is being opened, so a check at startup (worker
        # thread) and a visitor pressing the button never open it twice at once.
        self._open_lock = threading.Lock()
        self._stream = None
        self._blocks: list[np.ndarray] = []
        self._frames = 0
        self._sample_rate = float(TARGET_SAMPLE_RATE)
        self._channel = 0
        self._level = 0.0
        self._peak = 0.0
        self._overflowed = False
        self._first_block = threading.Event()
        self._device_label = ""
        # (requested device, channel) -> key of the device that worked for it last time
        self._worked: dict[tuple[str | None, int], str] = {}

    # --- devices ---

    def list_input_devices(self) -> list[AudioDevice]:
        """Every microphone on every audio API, the one in use (or the system's default) first."""
        sd = _sounddevice()
        try:
            devices = sd.query_devices()
            apis = sd.query_hostapis()
            keys = [
                _device_key(devices[index], apis[devices[index]["hostapi"]]["name"])
                for index in self._candidates(sd, None)
            ]
        except Exception as exc:
            raise MicrophoneError("อ่านรายการไมโครโฟนไม่ได้") from exc
        # Marked as the default: the one automatic selection found working, else the first to try.
        default_key = self._worked.get((None, 0)) or (keys[0] if keys else None)
        return [AudioDevice(name=key, is_default=key == default_key) for key in keys]

    @property
    def device_label(self) -> str:
        """The device that is open now, or was opened last."""
        return self._device_label

    def _candidates(self, sd, device_name: str | None) -> list[int]:
        """Device indexes to try, best first.

        A saved name (with or without the "[API]" suffix) picks that microphone,
        through the preferred API first. Without one: the system default, then
        each API's default, then every other microphone.
        """
        devices = sd.query_devices()
        apis = sd.query_hostapis()

        def rank(index: int) -> tuple[int, int, int]:
            api = apis[devices[index]["hostapi"]]
            return (_api_rank(api["name"]), int(index != api["default_input_device"]), index)

        microphones = sorted((i for i, d in enumerate(devices) if _is_microphone(d)), key=rank)
        if device_name:
            return [
                index for index in microphones
                if device_name in (
                    devices[index]["name"],
                    _device_key(devices[index], apis[devices[index]["hostapi"]]["name"]),
                )
            ]
        default_input = sd.default.device[0]
        if isinstance(default_input, int) and default_input in microphones:
            microphones.remove(default_input)
            microphones.insert(0, default_input)
        return microphones

    # --- capture ---

    @property
    def is_recording(self) -> bool:
        return self._stream is not None

    @property
    def level(self) -> float:
        """RMS of the most recent block, 0.0–1.0."""
        return self._level

    @property
    def peak(self) -> float:
        """Largest absolute sample since ``start``.

        Exactly 0.0 after a second or more means the device delivers digital
        silence: it is muted, or the OS is withholding microphone access.
        """
        return self._peak

    @property
    def duration(self) -> float:
        """Seconds captured so far."""
        return self._frames / self._sample_rate

    def start(self, device_name: str | None = None, channel: int = 0, fallback: bool = True) -> None:
        """Start capturing one channel of the device; returns once audio is arriving.

        ``channel`` matters for microphone arrays, which put their processed
        (beam-formed, echo-cancelled) signal on a particular channel. If the
        device has fewer channels, the first one is used.

        With ``fallback`` a saved device that is missing or broken is replaced
        by any microphone that works (the visitor still gets to talk); the
        Admin's microphone test passes False to test exactly what was chosen.
        """
        sd = _sounddevice()
        self.cancel()
        with self._open_lock:
            self._stream = self._open_first_working(sd, device_name, channel, fallback)

    def probe(self, device_name: str | None = None, channel: int = 0) -> str:
        """Open the microphone briefly to prove it delivers audio; returns its name.

        Raises ``MicrophoneError`` with the reason when no microphone works.
        """
        sd = _sounddevice()
        with self._open_lock:
            if self._stream is not None:
                return self._device_label  # in use right now, so it works
            stream = self._open_first_working(sd, device_name, channel, True)
            time.sleep(PROBE_SECONDS)
            self._stop_stream(stream)
            with self._lock:
                self._blocks = []
                self._frames = 0
            self._level = 0.0
        return self._device_label

    def _open_first_working(self, sd, device_name: str | None, channel: int, fallback: bool):
        try:
            devices = sd.query_devices()
            apis = sd.query_hostapis()
            order = self._candidates(sd, device_name)
            chosen = set(order) if device_name else set()
            if device_name and not order:
                logger.warning("Saved microphone %r not found", device_name)
            if device_name and fallback:
                order += [index for index in self._candidates(sd, None) if index not in order]
        except Exception as exc:
            logger.exception("Could not list the microphones")
            raise MicrophoneError("อ่านรายการไมโครโฟนไม่ได้") from exc
        if not order:
            raise MicrophoneError(
                f"ไม่พบไมโครโฟนที่เลือกไว้ ({device_name})" if device_name else NO_MICROPHONE_MESSAGE
            )
        keys = {index: _device_key(devices[index], apis[devices[index]["hostapi"]]["name"]) for index in order}
        remembered = self._worked.get((device_name, channel))
        order.sort(key=lambda index: keys[index] != remembered)  # stable: the rest keep their order

        # A microphone nobody chose that delivers exact zeros (an empty jack, a
        # muted array) is kept only as a last resort. A chosen one is used as it is.
        silent: list[int] = []
        failures = []
        for index in order:
            try:
                stream = self._open_device(sd, index, devices[index], channel)
            except Exception as exc:  # PortAudio raises several types; none is fatal here
                failures.append(f"{keys[index]}: {exc}")
                continue
            if index not in chosen and keys[index] != remembered and not self._has_signal():
                self._stop_stream(stream)  # only one stream may feed the buffer at a time
                silent.append(index)
                continue
            return self._opened(stream, keys[index], device_name, channel, failures)
        for index in silent:
            try:
                stream = self._open_device(sd, index, devices[index], channel)
            except Exception as exc:
                failures.append(f"{keys[index]}: {exc}")
                continue
            return self._opened(stream, keys[index], device_name, channel, failures)

        logger.error("No microphone could be opened:\n  %s", "\n  ".join(failures))
        if windows_microphone_blocked():
            raise MicrophoneError(PRIVACY_BLOCKED_MESSAGE)
        if device_name and not fallback:
            raise MicrophoneError(f"เปิดไมโครโฟนที่เลือกไว้ไม่ได้ ({device_name})")
        raise MicrophoneError(OPEN_FAILED_MESSAGE)

    def _opened(self, stream, key: str, device_name: str | None, channel: int, failures: list[str]):
        if failures:
            logger.info("Microphone fallbacks that did not work:\n  %s", "\n  ".join(failures))
        if self._worked.get((device_name, channel)) != key:
            logger.info("Microphone in use: %s (channel %d)", key, self._channel)
            if failures and windows_microphone_blocked():
                logger.warning(
                    "Windows privacy settings deny microphone access; using %s, which bypasses them", key
                )
        self._worked[(device_name, channel)] = key
        self._device_label = key
        return stream

    def _open_device(self, sd, index: int, device: dict, channel: int):
        """Open and start one device; raises unless audio blocks actually arrive."""
        available = int(device["max_input_channels"])
        if not 0 <= channel < available:
            logger.warning(
                "Microphone channel %d does not exist (device has %d); using channel 0",
                channel, available,
            )
            channel = 0
        # Prefer 16 kHz straight from the driver; otherwise capture at the
        # device's own rate and resample when stopping.
        rates = [float(TARGET_SAMPLE_RATE)]
        if float(device["default_samplerate"]) != TARGET_SAMPLE_RATE:
            rates.append(float(device["default_samplerate"]))
        error: Exception = MicrophoneError("ไม่ได้รับเสียงจากไมโครโฟน")
        for sample_rate in rates:
            with self._lock:
                self._blocks = []
                self._frames = 0
                self._level = 0.0
                self._peak = 0.0
                self._overflowed = False
                self._sample_rate = sample_rate
                self._channel = channel
            self._first_block.clear()
            try:
                stream = sd.InputStream(
                    samplerate=sample_rate,
                    blocksize=int(sample_rate * BLOCK_SECONDS),
                    device=index,
                    channels=channel + 1,
                    dtype="float32",
                    callback=self._on_audio,
                )
                stream.start()
            except Exception as exc:
                error = exc
                continue
            if self._first_block.wait(FIRST_BLOCK_TIMEOUT_S):
                return stream
            self._stop_stream(stream)
            error = MicrophoneError("เปิดได้แต่ไม่มีเสียงเข้ามา")
        raise error

    def _has_signal(self) -> bool:
        deadline = time.monotonic() + SILENCE_CHECK_S
        while self._peak == 0.0 and time.monotonic() < deadline:
            time.sleep(0.02)
        return self._peak > 0.0

    def stop(self) -> np.ndarray:
        """Stop capturing and return mono float32 audio at 16 kHz."""
        self._close_stream()
        with self._lock:
            blocks, self._blocks = self._blocks, []
            sample_rate = self._sample_rate
            overflowed = self._overflowed
        self._level = 0.0
        if overflowed:
            logger.warning("Microphone input overflowed; some audio was dropped")
        if not blocks:
            return np.zeros(0, dtype=np.float32)
        return resample(np.concatenate(blocks), sample_rate, TARGET_SAMPLE_RATE)

    def snapshot(self) -> np.ndarray:
        """What has been recorded so far (mono float32, 16 kHz) while the microphone stays open."""
        with self._lock:
            blocks = list(self._blocks)
            sample_rate = self._sample_rate
        if not blocks:
            return np.zeros(0, dtype=np.float32)
        return resample(np.concatenate(blocks), sample_rate, TARGET_SAMPLE_RATE)

    def recent(self, seconds: float) -> np.ndarray:
        """The last ``seconds`` of what has been recorded (mono float32, 16 kHz); the microphone stays open."""
        with self._lock:
            sample_rate = self._sample_rate
            wanted = int(seconds * sample_rate)
            picked, total = [], 0
            for block in reversed(self._blocks):
                picked.append(block)
                total += len(block)
                if total >= wanted:
                    break
        if not picked:
            return np.zeros(0, dtype=np.float32)
        audio = np.concatenate(picked[::-1])[-wanted:]
        return resample(audio, sample_rate, TARGET_SAMPLE_RATE)

    def cancel(self) -> None:
        """Stop capturing and discard what was recorded."""
        self._close_stream()
        with self._lock:
            self._blocks = []
            self._frames = 0
        self._level = 0.0

    def _close_stream(self) -> None:
        stream, self._stream = self._stream, None
        if stream is not None:
            self._stop_stream(stream)

    @staticmethod
    def _stop_stream(stream) -> None:
        try:
            stream.stop()
            stream.close()
        except Exception:
            logger.exception("Error while closing the microphone stream")

    def _on_audio(self, indata, frames, _time, status) -> None:
        # PortAudio thread: no logging, no blocking work.
        block = indata[:, self._channel].copy()
        with self._lock:
            if status.input_overflow:
                self._overflowed = True
            self._blocks.append(block)
            self._frames += frames
        if frames:
            self._level = float(np.sqrt(np.mean(np.square(block))))
            self._peak = max(self._peak, float(np.max(np.abs(block))))
            self._first_block.set()

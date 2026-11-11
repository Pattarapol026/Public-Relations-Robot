"""ReSpeaker XVF3800 4-Mic Array: the direction of the voice and the listening beam, over USB control.

The board computes the direction itself; this module only asks it. Facts verified on the real
board (firmware 2.1.1, Windows 11, 7 Nov 2026) and the reasons for the code below:

* The "reSpeaker Control" interface is bound to WinUSB by the firmware, so nothing has to be
  installed and the microphone keeps working while it is read. Never put WinUSB on the whole
  board (Zadig): the microphone disappears.
* A reply whose status byte is 64 means "busy, ask again" and its payload is garbage (it still
  holds the answer to the previous question). Such a reply is retried and never used.
* ``DOA_VALUE`` keeps the angle of the last voice while the room is quiet; its second number is
  1 only while the board hears speech. That flag is the confidence of a reading.
* ``SAVE_CONFIGURATION`` is never sent: whatever this module sets is gone when the board is
  unplugged, and is set again at every start.

All USB traffic happens in one thread (``Xvf3800DirectionSource``); callers only read a
snapshot or queue a request, so a stuck board can never freeze the screen.
"""
from __future__ import annotations

import logging
import math
import queue
import struct
import threading
import time
from dataclasses import dataclass
from typing import Callable, Protocol

from audio.direction import DirectionReading, DirectionSource, wrap_degrees

logger = logging.getLogger(__name__)

VENDOR_ID = 0x2886
PRODUCT_ID = 0x001A

# (resource id, command id) as in Seeed's python_control/xvf_host.py
CMD_VERSION = (48, 0)
CMD_DOA = (20, 18)
CMD_AEC_AZIMUTHS = (33, 75)
CMD_FIXED_BEAMS_ON = (33, 37)
CMD_FIXED_BEAMS_AZIMUTH = (33, 81)

STATUS_OK = 0
STATUS_RETRY = 64
RETRIES = 10
RETRY_SLEEP_S = 0.02
READ_HZ = 10.0
RECONNECT_S = 3.0
STALE_S = 1.0  # a snapshot older than this is not "now"


class Xvf3800Error(Exception):
    """The board did not answer as expected (message is safe to show to the Admin)."""


class Transport(Protocol):
    """One USB control channel. ``read`` returns (status byte, payload)."""

    def read(self, resid: int, cmdid: int, length: int) -> tuple[int, bytes]: ...

    def write(self, resid: int, cmdid: int, payload: bytes) -> None: ...

    def close(self) -> None: ...


class PyUsbTransport:
    """The real board through pyusb + libusb. Raises Xvf3800Error when it is not there."""

    def __init__(self) -> None:
        try:
            import libusb_package
            import usb.backend.libusb1 as libusb1
            import usb.core
        except Exception as exc:  # noqa: BLE001 - optional dependency
            raise Xvf3800Error(f"ยังไม่ได้ติดตั้ง pyusb / libusb-package ({exc})") from exc
        backend = libusb1.get_backend(find_library=libusb_package.find_library)
        if backend is None:
            raise Xvf3800Error("เริ่ม libusb ไม่ได้")
        self._usb_error = usb.core.USBError
        self._device = usb.core.find(idVendor=VENDOR_ID, idProduct=PRODUCT_ID, backend=backend)
        if self._device is None:
            raise Xvf3800Error("ไม่พบบอร์ด XVF3800 ทาง USB")

    def read(self, resid: int, cmdid: int, length: int) -> tuple[int, bytes]:
        try:
            data = self._device.ctrl_transfer(0xC0, 0, 0x80 | cmdid, resid, length + 1, timeout=1000)
        except self._usb_error as exc:
            raise Xvf3800Error(f"อ่านจากบอร์ดไม่ได้ ({exc})") from exc
        return int(data[0]), bytes(data[1:])

    def write(self, resid: int, cmdid: int, payload: bytes) -> None:
        try:
            self._device.ctrl_transfer(0x40, 0, cmdid, resid, payload, timeout=1000)
        except self._usb_error as exc:
            raise Xvf3800Error(f"เขียนไปที่บอร์ดไม่ได้ ({exc})") from exc

    def close(self) -> None:
        try:
            import usb.util

            usb.util.dispose_resources(self._device)
        except Exception:  # noqa: BLE001 - closing must never raise
            pass


def usb_available() -> tuple[bool, str]:
    """Whether the USB libraries can be imported (call it on the main thread, once)."""
    try:
        import libusb_package  # noqa: F401
        import usb.core  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        return False, f"ยังไม่ได้ติดตั้ง pyusb และ libusb-package ({exc})"
    return True, ""


def degrees_to_board_radians(degrees: float) -> float:
    """Board angles run 0 to 2π."""
    return math.radians(degrees % 360.0)


# --- the board's commands ---


class Xvf3800Control:
    """Reads and writes the few commands the robot needs, on one open transport."""

    def __init__(self, transport: Transport, sleep: Callable[[float], None] = time.sleep) -> None:
        self._transport = transport
        self._sleep = sleep

    def _read(self, command: tuple[int, int], length: int) -> bytes:
        for _ in range(RETRIES):
            status, payload = self._transport.read(command[0], command[1], length)
            if status == STATUS_OK:
                return payload[:length]
            if status != STATUS_RETRY:
                raise Xvf3800Error(f"บอร์ดตอบสถานะ {status}")
            self._sleep(RETRY_SLEEP_S)
        raise Xvf3800Error("บอร์ดไม่ว่างตอบ (ลองซ้ำครบแล้ว)")

    def version(self) -> str:
        major, minor, patch = struct.unpack("<3B", self._read(CMD_VERSION, 3))
        return f"{major}.{minor}.{patch}"

    def doa(self) -> tuple[int, bool]:
        """(angle 0-359 in the board's own frame, True while the board hears speech)."""
        angle, speech = struct.unpack("<HH", self._read(CMD_DOA, 4))
        return angle % 360, speech == 1

    def beam_state(self) -> tuple[bool, list[float]]:
        """(fixed-beam mode on, the two fixed beams' angles in degrees)."""
        (on,) = struct.unpack("<i", self._read(CMD_FIXED_BEAMS_ON, 4))
        azimuths = struct.unpack("<2f", self._read(CMD_FIXED_BEAMS_AZIMUTH, 8))
        return on == 1, [round(math.degrees(a) % 360.0, 1) for a in azimuths]

    def set_fixed_beam(self, board_degrees: float) -> None:
        """Listen only to ``board_degrees``. Verified by reading the setting back."""
        radians = degrees_to_board_radians(board_degrees)
        self._transport.write(*CMD_FIXED_BEAMS_AZIMUTH, struct.pack("<2f", radians, radians))
        self._transport.write(*CMD_FIXED_BEAMS_ON, struct.pack("<i", 1))
        on, azimuths = self.beam_state()
        if not on or any(_angle_gap(a, board_degrees) > 2.0 for a in azimuths):
            raise Xvf3800Error("บอร์ดไม่ยอมรับการล็อกทิศรับเสียง (อ่านค่ากลับมาไม่ตรง)")

    def release_beam(self) -> None:
        """Back to the board's own choice: it turns to the loudest voice."""
        self._transport.write(*CMD_FIXED_BEAMS_ON, struct.pack("<i", 0))
        if self.beam_state()[0]:
            raise Xvf3800Error("บอร์ดไม่ยอมปล่อยการล็อกทิศรับเสียง")


def _angle_gap(a: float, b: float) -> float:
    return abs(wrap_degrees(a - b))


# --- the direction source ---


@dataclass(frozen=True)
class BoardSnapshot:
    """What the board said last. ``board_degrees`` is in the board's own frame."""

    connected: bool
    version: str
    board_degrees: int | None
    speech: bool
    timestamp: float  # time.monotonic() of the last good reading, 0 if none
    beam_locked: bool = False
    beam_degrees: float | None = None
    error: str = ""

    def fresh(self, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        return self.connected and self.timestamp > 0 and now - self.timestamp <= STALE_S


@dataclass(frozen=True)
class Orientation:
    """Where the microphone's front is. Found by calibration, never guessed.

    ``front_deg`` is the board's angle when a person stands straight in front of the camera;
    ``sign`` is +1 when the board's angle grows as that person walks to the right, else -1.
    """

    front_deg: float = 0.0
    sign: int = 1

    def to_mic_frame(self, board_degrees: float) -> float:
        """The code's frame: 0 = ahead, positive = right, (-180, 180]."""
        return wrap_degrees(self.sign * (board_degrees - self.front_deg))

    def to_board_frame(self, mic_degrees: float) -> float:
        return (self.front_deg + self.sign * mic_degrees) % 360.0


Connector = Callable[[], Transport]


class Xvf3800DirectionSource(DirectionSource):
    """Direction readings from the board, collected by a thread; ``read`` never blocks.

    A board that is unplugged, or plugged in later, is found again every few seconds.
    """

    def __init__(
        self,
        connector: Connector | None = None,
        orientation: Orientation | None = None,
        hz: float = READ_HZ,
        reconnect_s: float = RECONNECT_S,
    ) -> None:
        self._connector = connector or PyUsbTransport
        self._orientation = orientation or Orientation()
        self._period = 1.0 / hz
        self._reconnect_s = reconnect_s
        self._lock = threading.Lock()
        self._readings: list[DirectionReading] = []
        self._snapshot = BoardSnapshot(False, "", None, False, 0.0, error="ยังไม่ได้เชื่อมต่อ")
        self._last_voice: tuple[float, float] | None = None  # (monotonic time, mic-frame angle)
        self._voice_log: list[tuple[float, float]] = []  # (monotonic time, the board's own angle) while it heard speech
        self._requests: queue.Queue[tuple[str, float | None]] = queue.Queue()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # --- DirectionSource ---

    @property
    def available(self) -> bool:
        return True  # it keeps looking for the board; ``snapshot`` says whether it is there

    @property
    def status_text(self) -> str:
        snap = self.snapshot()
        if snap.connected:
            return f"ReSpeaker XVF3800 (เฟิร์มแวร์ {snap.version})"
        return f"ReSpeaker XVF3800: {snap.error or 'ยังไม่เชื่อมต่อ'}"

    def read(self) -> list[DirectionReading]:
        with self._lock:
            readings, self._readings = self._readings, []
        return readings

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="xvf3800", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            self._requests.put(("wake", None))
            thread.join(timeout=2.0)

    # --- for the screen ---

    @property
    def orientation(self) -> Orientation:
        return self._orientation

    def set_orientation(self, orientation: Orientation) -> None:
        self._orientation = orientation

    def snapshot(self) -> BoardSnapshot:
        with self._lock:
            return self._snapshot

    def voice_since(self, since: float) -> list[tuple[float, float]]:
        """(time, the board's own angle) of every reading since ``since`` that the board called speech.

        The board's own frame is enough to tell "the same direction as before" from "somewhere else":
        it needs no calibration."""
        with self._lock:
            return [entry for entry in self._voice_log if entry[0] >= since]

    def last_voice(self) -> tuple[float, float] | None:
        """(time, mic-frame angle) of the last reading the board called speech."""
        with self._lock:
            return self._last_voice

    def request_beam(self, board_degrees: float | None) -> None:
        """Lock the listening beam to this board angle, or free it (None). Done by the thread."""
        self._requests.put(("beam", board_degrees))

    # --- the thread ---

    def _run(self) -> None:
        while not self._stop.is_set():
            control = self._connect()
            if control is None:
                self._stop.wait(self._reconnect_s)
                continue
            try:
                self._serve(control)
            except Exception as exc:  # noqa: BLE001 - unplugged, or a driver error: look again later
                logger.warning("XVF3800 lost: %s", exc)
                self._set_snapshot(BoardSnapshot(False, "", None, False, 0.0, error=str(exc)))
            finally:
                control._transport.close()  # noqa: SLF001 - this module owns the transport

    def _connect(self) -> Xvf3800Control | None:
        try:
            transport = self._connector()
        except Xvf3800Error as exc:
            self._set_snapshot(BoardSnapshot(False, "", None, False, 0.0, error=str(exc)))
            return None
        except Exception as exc:  # noqa: BLE001 - the thread must survive anything a driver throws
            logger.warning("XVF3800 connect failed: %r", exc)
            self._set_snapshot(BoardSnapshot(False, "", None, False, 0.0, error=str(exc)))
            return None
        control = Xvf3800Control(transport)
        try:
            version = control.version()
        except Xvf3800Error as exc:
            transport.close()
            self._set_snapshot(BoardSnapshot(False, "", None, False, 0.0, error=str(exc)))
            return None
        logger.info("XVF3800 control connected: firmware %s", version)
        self._set_snapshot(BoardSnapshot(True, version, None, False, 0.0))
        return control

    def _serve(self, control: Xvf3800Control) -> None:
        version = self.snapshot().version
        next_read = time.monotonic()
        while not self._stop.is_set():
            self._handle_requests(control)
            now = time.monotonic()
            if now >= next_read:
                next_read = now + self._period
                degrees, speech = control.doa()
                mic = self._orientation.to_mic_frame(degrees)
                with self._lock:
                    self._readings.append(DirectionReading(now, mic, 1.0 if speech else 0.0))
                    if speech:
                        self._last_voice = (now, mic)
                        self._voice_log.append((now, float(degrees)))
                        del self._voice_log[:-200]
                    del self._readings[:-100]  # never grow if nobody reads
                    beam = self._snapshot
                    self._snapshot = BoardSnapshot(
                        True, version, degrees, speech, now,
                        beam_locked=beam.beam_locked, beam_degrees=beam.beam_degrees,
                    )
            try:
                request = self._requests.get(timeout=max(0.0, next_read - time.monotonic()))
            except queue.Empty:
                continue
            self._requests.put(request)  # handled at the top of the loop
        if self.snapshot().beam_locked:  # leaving: hand the beam back to the board
            try:
                control.release_beam()
            except Xvf3800Error:
                pass

    def _handle_requests(self, control: Xvf3800Control) -> None:
        wanted: tuple[str, float | None] | None = None
        while True:
            try:
                kind, value = self._requests.get_nowait()
            except queue.Empty:
                break
            if kind == "beam":
                wanted = (kind, value)
        if wanted is None:
            return
        value = wanted[1]
        try:
            if value is None:
                control.release_beam()
                self._set_beam(False, None)
            else:
                control.set_fixed_beam(value)
                self._set_beam(True, value)
        except Xvf3800Error as exc:
            logger.warning("XVF3800 beam request failed: %s", exc)
            self._set_beam(False, None, str(exc))

    def _set_beam(self, locked: bool, degrees: float | None, error: str = "") -> None:
        with self._lock:
            s = self._snapshot
            self._snapshot = BoardSnapshot(
                s.connected, s.version, s.board_degrees, s.speech, s.timestamp,
                beam_locked=locked, beam_degrees=degrees, error=error,
            )

    def _set_snapshot(self, snapshot: BoardSnapshot) -> None:
        with self._lock:
            self._snapshot = snapshot

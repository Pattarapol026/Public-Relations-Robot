"""The ReSpeaker XVF3800 reader and the direction controller, with a scripted board.

The real board was probed by hand on 7 Nov 2026 (reads, fixed-beam write and read-back). These tests
check the logic around it: the retry protocol, angle conversion across 0/360, calibration being
refused when the evidence is poor, and nothing changing while the robot is uncalibrated.
"""
from __future__ import annotations

import struct
import threading
import time

import pytest
from PySide6.QtWidgets import QApplication

from app.direction_controller import (
    CAPTURE_MIN_READINGS,
    DirectionController,
    DirectionError,
    DirectionSettings,
    circular_mean_degrees,
)
from audio.direction import angle_between
from audio.xvf3800 import (
    CMD_DOA,
    CMD_FIXED_BEAMS_AZIMUTH,
    CMD_FIXED_BEAMS_ON,
    CMD_VERSION,
    STATUS_RETRY,
    BoardSnapshot,
    Orientation,
    Xvf3800Control,
    Xvf3800DirectionSource,
    Xvf3800Error,
)
from services.speaker_matching import MountGeometry
from vision.interfaces import PersonObservation


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    return QApplication.instance() or QApplication([])


# --- a scripted board ---


class FakeTransport:
    def __init__(self, doa=(265, 0), busy_first=0):
        self.doa = doa
        self.busy_first = busy_first  # answers "retry" this many times before every read
        self.busy_left = busy_first
        self.beam_on = 0
        self.beam_azimuths = (0.0, 0.0)
        self.writes: list[tuple[tuple[int, int], bytes]] = []
        self.closed = False
        self.dead = False
        self.ignore_writes = False
        self._last_payload = b"\xff" * 16

    def read(self, resid, cmdid, length):
        if self.dead:
            raise Xvf3800Error("USB: device disconnected")
        if self.busy_left > 0:
            self.busy_left -= 1
            return STATUS_RETRY, self._last_payload[:length]  # garbage: the previous answer
        self.busy_left = self.busy_first
        command = (resid, cmdid)
        if command == CMD_VERSION:
            payload = bytes((2, 1, 1))
        elif command == CMD_DOA:
            payload = struct.pack("<HH", *self.doa)
        elif command == CMD_FIXED_BEAMS_ON:
            payload = struct.pack("<i", self.beam_on)
        elif command == CMD_FIXED_BEAMS_AZIMUTH:
            payload = struct.pack("<2f", *self.beam_azimuths)
        else:
            raise AssertionError(f"unexpected read {command}")
        self._last_payload = payload
        return 0, payload

    def write(self, resid, cmdid, payload):
        if self.dead:
            raise Xvf3800Error("USB: device disconnected")
        self.writes.append(((resid, cmdid), payload))
        if self.ignore_writes:
            return
        if (resid, cmdid) == CMD_FIXED_BEAMS_ON:
            (self.beam_on,) = struct.unpack("<i", payload)
        elif (resid, cmdid) == CMD_FIXED_BEAMS_AZIMUTH:
            self.beam_azimuths = struct.unpack("<2f", payload)

    def close(self):
        self.closed = True


# --- the protocol ---


def test_busy_answers_are_retried_and_their_garbage_is_never_used():
    transport = FakeTransport(doa=(123, 1), busy_first=3)
    control = Xvf3800Control(transport, sleep=lambda _s: None)
    assert control.version() == "2.1.1"
    assert control.doa() == (123, True)  # not the version bytes that the busy replies carried


def test_a_board_that_stays_busy_is_an_error_not_a_guess():
    control = Xvf3800Control(FakeTransport(busy_first=99), sleep=lambda _s: None)
    with pytest.raises(Xvf3800Error):
        control.doa()


def test_beam_is_verified_by_reading_it_back():
    transport = FakeTransport()
    control = Xvf3800Control(transport, sleep=lambda _s: None)
    control.set_fixed_beam(90.0)
    assert control.beam_state() == (True, [90.0, 90.0])
    control.release_beam()
    assert control.beam_state()[0] is False
    assert all(command != (48, 9) for command, _ in transport.writes)  # SAVE_CONFIGURATION is never sent


def test_a_board_that_ignores_the_beam_write_is_reported():
    transport = FakeTransport()
    transport.ignore_writes = True
    with pytest.raises(Xvf3800Error):
        Xvf3800Control(transport, sleep=lambda _s: None).set_fixed_beam(90.0)


# --- angles ---


def test_orientation_converts_across_the_0_360_wrap():
    left_handed = Orientation(front_deg=350.0, sign=1)
    assert left_handed.to_mic_frame(10.0) == pytest.approx(20.0)
    assert left_handed.to_mic_frame(340.0) == pytest.approx(-10.0)
    mirrored = Orientation(front_deg=90.0, sign=-1)
    assert mirrored.to_mic_frame(45.0) == pytest.approx(45.0)  # board angle fell: the person is to the right
    for mic in (-170.0, -30.0, 0.0, 45.0, 179.0):
        for orientation in (left_handed, mirrored, Orientation()):
            back = orientation.to_mic_frame(orientation.to_board_frame(mic))
            assert angle_between(back, mic) < 1e-6


def test_circular_mean_handles_values_either_side_of_zero():
    mean, spread = circular_mean_degrees([358.0, 2.0, 359.0, 1.0])
    assert angle_between(mean, 0.0) < 1.0 and spread < 3.0


# --- the source's thread ---


def wait_for(condition, seconds=3.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.01)
    return False


def test_source_reports_readings_in_the_mic_frame_and_survives_unplugging():
    boards: list[FakeTransport] = [FakeTransport(doa=(280, 1))]
    plugged = {"in": True}

    def connect():
        if not plugged["in"]:
            raise Xvf3800Error("ไม่พบบอร์ด")
        return boards[-1]

    source = Xvf3800DirectionSource(connect, Orientation(270.0, 1), hz=200.0, reconnect_s=0.05)
    assert source.available and not source.snapshot().connected
    source.start()
    try:
        assert wait_for(lambda: source.snapshot().connected and source.read())
        snap = source.snapshot()
        assert snap.version == "2.1.1" and snap.board_degrees == 280 and snap.speech
        reading = source.last_voice()
        assert reading is not None and reading[1] == pytest.approx(10.0)
        plugged["in"] = False
        boards[0].dead = True  # unplugged mid-read
        assert wait_for(lambda: not source.snapshot().connected)
        assert wait_for(lambda: boards[0].closed)
        boards.append(FakeTransport(doa=(100, 1)))
        plugged["in"] = True
        assert wait_for(lambda: source.snapshot().connected and source.snapshot().board_degrees == 100)
    finally:
        source.stop()


def test_quiet_room_readings_carry_zero_confidence():
    source = Xvf3800DirectionSource(lambda: FakeTransport(doa=(265, 0)), hz=200.0, reconnect_s=0.05)
    source.start()
    try:
        assert wait_for(lambda: len(source.read()) > 0 or source.snapshot().board_degrees == 265)
        time.sleep(0.05)
        readings = source.read()
        assert readings and all(r.confidence == 0.0 for r in readings)
        assert source.last_voice() is None  # the board's remembered angle is not "a voice now"
    finally:
        source.stop()


def test_beam_requests_are_done_by_the_thread_and_released_on_stop():
    transport = FakeTransport(doa=(200, 0))
    source = Xvf3800DirectionSource(lambda: transport, hz=100.0, reconnect_s=0.05)
    source.start()
    try:
        assert wait_for(lambda: source.snapshot().connected)
        source.request_beam(90.0)
        assert wait_for(lambda: source.snapshot().beam_locked and transport.beam_on == 1)
        assert source.snapshot().beam_degrees == 90.0
    finally:
        source.stop()
    assert transport.beam_on == 0  # handed back to the board


def test_a_rejected_beam_write_does_not_stop_the_readings():
    transport = FakeTransport(doa=(200, 1))
    transport.ignore_writes = True
    source = Xvf3800DirectionSource(lambda: transport, hz=100.0, reconnect_s=0.05)
    source.start()
    try:
        assert wait_for(lambda: source.snapshot().connected)
        source.request_beam(90.0)
        assert wait_for(lambda: bool(source.snapshot().error))
        assert not source.snapshot().beam_locked
        before = source.snapshot().timestamp
        assert wait_for(lambda: source.snapshot().timestamp > before)
    finally:
        source.stop()


# --- the controller ---


class FakeStore:
    def __init__(self):
        self.values: dict[str, str] = {}

    def all(self):
        return dict(self.values)

    def set(self, key, value):
        self.values[key] = value


class FakeBoard:
    """Stands in for Xvf3800DirectionSource: a snapshot the test controls."""

    def __init__(self):
        self.snap = BoardSnapshot(True, "2.1.1", 265, False, 1.0)
        self.voice = None
        self.beam_requests: list[float | None] = []
        self.orientation = Orientation()
        self.log: list[tuple[float, float]] = []  # (time, the board's own angle) while it heard speech

    def snapshot(self):
        return self.snap

    def last_voice(self):
        return self.voice

    def voice_since(self, since):
        return [entry for entry in self.log if entry[0] >= since]

    def set_orientation(self, orientation):
        self.orientation = orientation

    def request_beam(self, degrees):
        self.beam_requests.append(degrees)
        locked = degrees is not None
        s = self.snap
        self.snap = BoardSnapshot(
            s.connected, s.version, s.board_degrees, s.speech, s.timestamp,
            beam_locked=locked, beam_degrees=degrees,
        )

    def speak(self, degrees, speech=True):
        s = self.snap
        self.snap = BoardSnapshot(
            s.connected, s.version, degrees, speech, s.timestamp + 0.1,
            beam_locked=s.beam_locked, beam_degrees=s.beam_degrees,
        )


class FakeVoice:
    speech_active = False


class FakeVision:
    def __init__(self):
        self.people: tuple[PersonObservation, ...] = ()

    def is_at_conversation_distance(self, person):
        return person.z_m < 2.0


def make_controller(store=None):
    board, vision, voice = FakeBoard(), FakeVision(), FakeVoice()
    controller = DirectionController(
        board, store or FakeStore(), voice, vision, MountGeometry(), is_robot_speaking=lambda: False
    )
    controller._timer.stop()  # the tests drive it by hand
    return controller, board, vision, voice


def calibrate(controller, board, front=270, right=315, count=None):
    results = []
    controller.capture_finished.connect(results.append)
    for kind, angle in (("front", front), ("right", right)):
        controller.start_capture(kind)
        for _ in range(count or CAPTURE_MIN_READINGS * 2):
            board.speak(angle)
            controller._tick()
    return results


def test_nothing_is_in_force_until_calibrated_and_switched_on():
    controller, board, _vision, _voice = make_controller()
    board.voice = (time.monotonic(), 120.0)  # a loud voice far off to the side
    assert not controller.in_force and not controller.utterance_veto_enabled()
    assert controller.blocks_speech() is False
    controller._tick()
    assert board.beam_requests == []  # the board is left to turn by itself


def test_calibration_sets_the_front_and_the_turning_direction():
    controller, board, _vision, _voice = make_controller()
    results = calibrate(controller, board, front=270, right=315)
    assert [r.ok for r in results] == [True, True], [r.message for r in results]
    s = controller.settings
    assert s.front_known and s.sign_known and s.front_deg == pytest.approx(270, abs=0.5) and s.sign == 1
    # board angle falling towards the right means the other direction
    controller2, board2, *_ = make_controller()
    calibrate(controller2, board2, front=10, right=325)
    assert controller2.settings.sign == -1


def test_calibration_is_refused_on_poor_evidence():
    controller, board, *_ = make_controller()
    results = []
    controller.capture_finished.connect(results.append)
    controller.start_capture("front")
    for _ in range(3):  # too few readings of speech
        board.speak(270)
        controller._tick()
    controller._capture_started -= 10  # the time is up
    controller._tick()
    assert results and not results[-1].ok and not controller.settings.front_known

    controller.start_capture("front")
    for angle in (0, 90, 180, 270) * 4:  # a room full of voices from everywhere
        board.speak(angle)
        controller._tick()
    assert not results[-1].ok and "แกว่ง" in results[-1].message and not controller.settings.front_known


def test_the_right_side_must_be_far_enough_from_the_front():
    controller, board, *_ = make_controller()
    results = calibrate(controller, board, front=270, right=275)
    assert results[0].ok and not results[1].ok and not controller.settings.sign_known


def test_the_right_side_cannot_be_measured_before_the_front():
    controller, *_ = make_controller()
    with pytest.raises(DirectionError):
        controller.start_capture("right")


def test_a_mode_cannot_be_switched_on_before_calibration():
    controller, *_ = make_controller()
    with pytest.raises(DirectionError):
        controller.save(DirectionSettings(mode="front"))
    with pytest.raises(DirectionError):
        controller.save(DirectionSettings(tolerance_deg=3.0))


def test_front_mode_locks_the_beam_to_the_calibrated_front_once():
    controller, board, *_ = make_controller()
    calibrate(controller, board, front=270, right=315)
    controller.save(replace_settings(controller, mode="front"))
    controller._tick()
    assert board.beam_requests[-1] == pytest.approx(270, abs=0.5)
    asked = len(board.beam_requests)
    for _ in range(5):
        controller._tick()
    assert len(board.beam_requests) == asked  # no nagging while it holds


def test_following_turns_the_beam_only_for_a_real_change_and_never_while_speaking():
    controller, board, vision, voice = make_controller()
    calibrate(controller, board, front=270, right=315)
    controller.save(replace_settings(controller, mode="follow"))
    controller._tick()
    assert board.beam_requests[-1] == pytest.approx(270, abs=0.5)  # nobody seen: straight ahead
    vision.people = (PersonObservation(x_m=0.5, z_m=1.0, y_m=0.0, confidence=0.9),)  # about 27° to the right
    voice.speech_active = True
    controller._tick()
    assert board.beam_requests[-1] == pytest.approx(270, abs=0.5)  # not under a speaking visitor
    voice.speech_active = False
    controller._tick()
    assert board.beam_requests[-1] == pytest.approx(270 + 26.6, abs=1.0)
    vision.people = (PersonObservation(x_m=0.52, z_m=1.0, y_m=0.0, confidence=0.9),)  # 1° more: below the 10° dead band
    asked = len(board.beam_requests)
    controller._tick()
    assert len(board.beam_requests) == asked


def test_switching_off_gives_the_beam_back():
    controller, board, *_ = make_controller()
    calibrate(controller, board)
    controller.save(replace_settings(controller, mode="front"))
    controller._tick()
    controller.save(replace_settings(controller, mode="off"))
    controller._tick()
    assert board.beam_requests[-1] is None


def test_direction_ends_a_turn_only_for_a_voice_clearly_from_elsewhere():
    controller, board, *_ = make_controller()
    calibrate(controller, board, front=270, right=315)
    controller.save(replace_settings(controller, mode="front", end_turn=True, tolerance_deg=30.0))
    now = time.monotonic()
    board.voice = (now, 10.0)
    assert controller.blocks_speech(now) is False  # the visitor, 10° off the front
    board.voice = (now, 100.0)
    assert controller.blocks_speech(now) is True  # someone to the side
    assert controller.blocks_speech(now + 5.0) is False  # a stale verdict says nothing about now
    board.voice = None
    assert controller.blocks_speech(now) is False  # no verdict: listen as always
    controller.save(replace_settings(controller, end_turn=False))
    board.voice = (now, 100.0)
    assert controller.blocks_speech(now) is False  # switched off


def test_an_unplugged_board_puts_nothing_in_force():
    controller, board, *_ = make_controller()
    calibrate(controller, board)
    controller.save(replace_settings(controller, mode="front", end_turn=True))
    board.snap = BoardSnapshot(False, "", None, False, 0.0, error="ไม่พบบอร์ด")
    assert not controller.in_force and controller.blocks_speech() is False


def test_settings_survive_a_restart():
    store = FakeStore()
    controller, board, *_ = make_controller(store)
    calibrate(controller, board, front=270, right=315)
    controller.save(replace_settings(controller, mode="front", tolerance_deg=40.0))
    again, board2, *_ = make_controller(store)
    s = again.settings
    assert (s.mode, s.front_known, s.sign_known, s.tolerance_deg) == ("front", True, True, 40.0)
    assert board2.orientation == Orientation(s.front_deg, 1)


def test_forgetting_the_calibration_returns_to_the_old_behaviour():
    controller, board, *_ = make_controller()
    calibrate(controller, board)
    controller.save(replace_settings(controller, mode="front", end_turn=True))
    controller.forget_calibration()
    controller._tick()
    s = controller.settings
    assert s.mode == "off" and not s.front_known
    assert s.end_turn  # following the voice that started the turn needs no calibration, so it stays on
    assert board.beam_requests[-1] is None and not controller.in_force


def replace_settings(controller, **changes):
    from dataclasses import replace

    return replace(controller.settings, **changes)


def test_a_real_run_of_the_thread_with_the_controller():
    """The threaded source and the controller together: calibrate from a spoken voice, then lock."""
    transport = FakeTransport(doa=(270, 1))
    source = Xvf3800DirectionSource(lambda: transport, hz=100.0, reconnect_s=0.05)
    controller = DirectionController(source, FakeStore(), FakeVoice(), FakeVision(), MountGeometry())
    controller._timer.stop()
    source.start()
    try:
        assert wait_for(lambda: source.snapshot().connected)
        results = []
        controller.capture_finished.connect(results.append)
        controller.start_capture("front")
        deadline = time.monotonic() + 5
        while not results and time.monotonic() < deadline:
            controller._tick()
            time.sleep(0.02)
        assert results and results[0].ok, results
        controller.save(replace_settings(controller, mode="front"))
        deadline = time.monotonic() + 3
        while transport.beam_on != 1 and time.monotonic() < deadline:
            controller._tick()
            time.sleep(0.02)
        assert transport.beam_on == 1
        assert transport.beam_azimuths[0] == pytest.approx(3.1416 * 1.5, abs=0.02)  # 270°
    finally:
        controller.shutdown()
        source.stop()
    assert transport.beam_on == 0


# --- step 3 without calibration: follow the voice that started the turn ---


def say(board, angles, start, step=0.1):
    """Readings of speech at these board angles, one every ``step`` seconds from ``start``."""
    for i, angle in enumerate(angles):
        board.log.append((start + i * step, float(angle)))
    return start + len(angles) * step


def anchored_controller():
    controller, board, *_ = make_controller()
    assert not controller.in_force  # never calibrated, never switched on
    controller.begin_turn()
    return controller, board


def test_the_voice_that_starts_the_turn_is_followed_without_any_calibration():
    controller, board = anchored_controller()
    t0 = time.monotonic()
    say(board, [200, 202, 198, 201, 199, 200], t0)  # the visitor
    now = t0 + 0.6
    assert controller.blocks_speech(now) is False
    # someone else starts talking from the side, a steady stream of readings
    end = say(board, [90, 92, 88, 91], now)
    assert controller.blocks_speech(end) is True


def test_one_stray_reading_or_the_visitors_own_wobble_never_cuts_them_off():
    controller, board = anchored_controller()
    t0 = time.monotonic()
    t = say(board, [200, 203, 197, 201, 199, 200], t0)
    t = say(board, [225, 178, 220, 182, 224, 176, 219], t)  # about +-25 degrees of their own wobble
    assert controller.blocks_speech(t) is False
    t = say(board, [90], t)  # a door, one reading
    assert controller.blocks_speech(t) is False
    t = say(board, [200], t)
    assert controller.blocks_speech(t) is False


def test_a_visitor_who_drifts_slowly_is_followed():
    controller, board = anchored_controller()
    t0 = time.monotonic()
    t = say(board, [200] * 6, t0)
    for angle in range(204, 260, 4):  # four degrees at a time, 56 degrees over the turn
        t = say(board, [angle] * 5, t)
        assert controller.blocks_speech(t) is False


def test_foreign_speech_must_be_recent_and_unbroken():
    controller, board = anchored_controller()
    t0 = time.monotonic()
    t = say(board, [200] * 6, t0)
    t = say(board, [90, 90, 200, 90], t)  # the visitor spoke in between
    assert controller.blocks_speech(t) is False
    t = say(board, [90, 90, 90, 90], t)
    assert controller.blocks_speech(t) is True
    assert controller.blocks_speech(t + 5.0) is False  # old news: listen as always


def test_a_turn_with_no_speech_flag_at_all_behaves_as_before():
    controller, board = anchored_controller()  # the board never raised its flag: nothing in the log
    assert controller.blocks_speech(time.monotonic() + 3.0) is False


def test_disagreeing_first_readings_pick_no_side_and_a_new_turn_forgets_the_old_one():
    controller, board = anchored_controller()
    t0 = time.monotonic()
    t = say(board, [0, 120, 240, 0, 120, 240, 300, 20], t0)  # a room full of voices
    assert controller.blocks_speech(t) is False
    controller.begin_turn()  # the next turn: whoever speaks now is the visitor
    controller._turn_started_at = t  # (the fake readings above lie ahead of the real clock)
    t1 = t + 0.5
    t = say(board, [30] * 6, t1)
    t = say(board, [200] * 4, t)
    assert controller.blocks_speech(t) is True


def test_the_turn_gate_is_off_when_switched_off_or_the_board_is_unplugged():
    controller, board = anchored_controller()
    t = say(board, [200] * 6 + [90] * 4, time.monotonic())
    assert controller.blocks_speech(t) is True
    controller.save(replace_settings(controller, end_turn=False))
    assert controller.blocks_speech(t) is False
    controller.save(replace_settings(controller, end_turn=True))
    board.snap = BoardSnapshot(False, "", None, False, 0.0, error="board unplugged")
    assert controller.blocks_speech(t) is False


def test_ending_a_turn_by_direction_is_on_from_the_start_and_needs_no_calibration():
    controller, *_ = make_controller()
    assert controller.settings.end_turn is True and not controller.settings.front_known
    controller.save(controller.settings)  # saving the defaults is allowed

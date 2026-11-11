from __future__ import annotations

import logging
import threading
import time

import pytest

from robot.command_gateway import CommandGateway, CommandRequest, CommandStatus
from robot.interfaces import (
    ActionOutcome,
    RobotAdapter,
    RobotConnectionError,
    RobotState,
    RobotStatus,
)
from robot.mock_adapter import MockRobotAdapter

ACTIONS = ["greet", "wave"]


class StubAdapter(RobotAdapter):
    """Adapter whose blocking calls are scripted by the test."""

    def __init__(self, behaviour=None, status=None) -> None:
        self.behaviour = behaviour or (lambda target: ActionOutcome(success=True))
        self.status = status or RobotStatus(connected=True, state=RobotState.IDLE)
        self.calls: list[tuple[str, str]] = []
        self.stop_called = threading.Event()

    def connect(self) -> None: ...
    def disconnect(self) -> None: ...

    def get_status(self) -> RobotStatus:
        if isinstance(self.status, Exception):
            raise self.status
        return self.status

    def stop(self) -> None:
        self.stop_called.set()

    def execute_action(self, action_id: str) -> ActionOutcome:
        self.calls.append(("action", action_id))
        return self.behaviour(action_id)


def make_gateway(adapter: RobotAdapter, timeout_s: float = 2.0) -> CommandGateway:
    return CommandGateway(adapter, ACTIONS, timeout_s=timeout_s)


def wait_for_state(adapter: RobotAdapter, state: RobotState, timeout_s: float = 2.0) -> None:
    deadline = time.monotonic() + timeout_s
    while adapter.get_status().state is not state:
        assert time.monotonic() < deadline, f"robot never reached {state}"
        time.sleep(0.01)


def test_allowed_action_reaches_adapter():
    adapter = StubAdapter()
    result = make_gateway(adapter).submit(CommandRequest("action", "greet"))
    assert result.status is CommandStatus.SUCCEEDED
    assert result.ok
    assert adapter.calls == [("action", "greet")]


@pytest.mark.parametrize("target", ["lobby", "reception", "greet"])
def test_walking_is_not_a_command_any_more(target):
    # Whoever asks (a button, a script, model output), the gateway has no such command.
    adapter = StubAdapter()
    result = make_gateway(adapter).submit(CommandRequest("navigate", target))
    assert (result.status, result.code) == (CommandStatus.REJECTED, "unknown_command")
    assert adapter.calls == []


@pytest.mark.parametrize("command_type", ["shell", "", None, 42, ["action"]])
def test_unknown_command_type_is_rejected(command_type):
    adapter = StubAdapter()
    result = make_gateway(adapter).submit(CommandRequest(command_type, "greet"))
    assert (result.status, result.code) == (CommandStatus.REJECTED, "unknown_command")
    assert adapter.calls == []


@pytest.mark.parametrize(
    "target",
    ["", "Greet", "greet; rm -rf /", "../greet", "1.5,2.0", None, 3, {"x": 1.0}, "a" * 65],
)
def test_malformed_target_is_rejected(target):
    adapter = StubAdapter()
    result = make_gateway(adapter).submit(CommandRequest("action", target))
    assert (result.status, result.code) == (CommandStatus.REJECTED, "invalid_parameter")
    assert adapter.calls == []


@pytest.mark.parametrize("target", ["dance", "step_forward", "step_back", "lobby"])
def test_target_outside_allowlist_is_rejected(target):
    adapter = StubAdapter()
    assert make_gateway(adapter).submit(CommandRequest("action", target)).code == "not_allowed"
    assert adapter.calls == []


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (RobotStatus(connected=False, state=RobotState.DISCONNECTED), "robot_disconnected"),
        (RobotStatus(connected=True, state=RobotState.ERROR), "robot_fault"),
        (RobotStatus(connected=True, state=RobotState.EXECUTING), "busy"),
        (RuntimeError("status unavailable"), "robot_disconnected"),
    ],
)
def test_robot_not_ready_is_rejected(status, code):
    adapter = StubAdapter(status=status)
    result = make_gateway(adapter).submit(CommandRequest("action", "greet"))
    assert (result.status, result.code) == (CommandStatus.REJECTED, code)
    assert adapter.calls == []


def test_second_command_is_rejected_while_first_runs_then_stop_cancels():
    adapter = MockRobotAdapter(action_time_s=5.0)
    adapter.connect()
    gateway = make_gateway(adapter, timeout_s=10.0)
    results = []
    first = threading.Thread(
        target=lambda: results.append(gateway.submit(CommandRequest("action", "wave")))
    )
    first.start()
    wait_for_state(adapter, RobotState.EXECUTING)

    second = gateway.submit(CommandRequest("action", "greet"))
    assert (second.status, second.code) == (CommandStatus.REJECTED, "busy")

    assert gateway.stop() is True
    first.join(timeout=2.0)
    assert not first.is_alive()
    assert results[0].status is CommandStatus.CANCELLED
    assert adapter.get_status().state is RobotState.IDLE


def test_timeout_stops_robot_and_keeps_gateway_busy_until_adapter_returns():
    release = threading.Event()

    def hang(_target: str) -> ActionOutcome:
        release.wait(5.0)
        return ActionOutcome(success=False, cancelled=True)

    adapter = StubAdapter(behaviour=hang)
    gateway = make_gateway(adapter, timeout_s=0.2)

    result = gateway.submit(CommandRequest("action", "wave"))
    assert result.status is CommandStatus.TIMEOUT
    assert adapter.stop_called.is_set()

    # The adapter call has not returned yet, so nothing new may start.
    assert gateway.submit(CommandRequest("action", "greet")).code == "busy"

    release.set()
    deadline = time.monotonic() + 2.0
    while gateway.submit(CommandRequest("action", "greet")).code == "busy":
        assert time.monotonic() < deadline, "gateway stayed busy after adapter returned"
        time.sleep(0.01)


def test_adapter_failures_are_reported_not_raised():
    def lose_connection(_target):
        raise RobotConnectionError("link down")

    def crash(_target):
        raise RuntimeError("boom")

    cases = [
        (lose_connection, CommandStatus.FAILED, "connection_lost"),
        (crash, CommandStatus.FAILED, "failed"),
        (lambda _t: ActionOutcome(success=False, detail="blocked"), CommandStatus.FAILED, "failed"),
    ]
    for behaviour, status, code in cases:
        result = make_gateway(StubAdapter(behaviour=behaviour)).submit(
            CommandRequest("action", "wave")
        )
        assert (result.status, result.code) == (status, code)


def test_gateway_recovers_after_a_failed_command():
    adapter = StubAdapter(behaviour=lambda _t: (_ for _ in ()).throw(RuntimeError("boom")))
    gateway = make_gateway(adapter)
    assert gateway.submit(CommandRequest("action", "wave")).code == "failed"
    adapter.behaviour = lambda _t: ActionOutcome(success=True)
    deadline = time.monotonic() + 2.0
    while (result := gateway.submit(CommandRequest("action", "wave"))).code == "busy":
        assert time.monotonic() < deadline
        time.sleep(0.01)
    assert result.ok


def test_stop_works_when_robot_is_disconnected_and_reports_adapter_failure():
    adapter = StubAdapter(status=RobotStatus(connected=False, state=RobotState.DISCONNECTED))
    assert make_gateway(adapter).stop() is True
    assert adapter.stop_called.is_set()

    class BrokenStop(StubAdapter):
        def stop(self) -> None:
            raise RobotConnectionError("link down")

    assert make_gateway(BrokenStop()).stop() is False


def test_decisions_are_logged(caplog):
    gateway = make_gateway(StubAdapter())
    with caplog.at_level(logging.INFO, logger="robot.command_gateway"):
        accepted = gateway.submit(CommandRequest("action", "wave", source="voice"))
        rejected = gateway.submit(CommandRequest("action", "dance"))
        gateway.stop(source="touch")
    text = caplog.text
    assert accepted.command_id in text and "source=voice" in text
    assert rejected.command_id in text and "not_allowed" in text
    assert "STOP requested" in text


def test_submit_after_shutdown_fails_cleanly():
    gateway = make_gateway(StubAdapter())
    gateway.shutdown()
    result = gateway.submit(CommandRequest("action", "wave"))
    assert result.status is CommandStatus.FAILED

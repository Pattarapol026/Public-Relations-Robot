from __future__ import annotations

import pytest

from robot.interfaces import RobotConnectionError, RobotState
from robot.mock_adapter import MockRobotAdapter


def make_adapter() -> MockRobotAdapter:
    adapter = MockRobotAdapter(action_time_s=0.01)
    adapter.connect()
    return adapter


def test_starts_disconnected_until_connect():
    adapter = MockRobotAdapter()
    assert adapter.get_status().connected is False
    assert adapter.get_status().state is RobotState.DISCONNECTED
    with pytest.raises(RobotConnectionError):
        adapter.execute_action("greet")


def test_action_runs_and_returns_to_idle():
    adapter = make_adapter()
    assert adapter.execute_action("greet").success
    assert adapter.get_status().state is RobotState.IDLE


def test_the_robot_has_no_way_to_walk():
    assert not hasattr(make_adapter(), "navigate_to")
    assert not hasattr(RobotState, "NAVIGATING")


def test_fault_blocks_commands_until_cleared():
    adapter = make_adapter()
    adapter.simulate_fault(True)
    assert adapter.get_status().state is RobotState.ERROR
    assert adapter.execute_action("greet").success is False
    adapter.simulate_fault(False)
    assert adapter.execute_action("greet").success


def test_connection_loss_and_recovery():
    adapter = make_adapter()
    adapter.simulate_connection_loss(True)
    assert adapter.get_status().connected is False
    adapter.simulate_connection_loss(False)
    assert adapter.get_status().state is RobotState.IDLE

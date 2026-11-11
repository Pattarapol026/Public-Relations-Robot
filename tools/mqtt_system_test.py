"""End-to-end MQTT test: a real broker process, a real simulator process, real MQTT over TCP.

    .venv313\\Scripts\\python.exe tools\\mqtt_system_test.py            # everything, about 40 s
    .venv313\\Scripts\\python.exe tools\\mqtt_system_test.py --skip-app # without the main-app check

It starts tools/mqtt_broker.py and tools/mqtt_device_simulator.py as separate
processes (logs in logs/mqtt_test/), talks to them with the app's own
MqttService, and prints PASS/FAIL with the reason for each check. A check
passes only on a real reply that refers to the command it sent (``reply_to``
or ``task_id``); waiting runs into a timeout instead of hanging. Both child
processes are stopped at the end, also after a failure or Ctrl+C.

No hardware is involved: the "device" is the simulator.
"""
from __future__ import annotations

import argparse
import logging
import os
import signal
import subprocess
import sys
import threading
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from services.mqtt_service import (  # noqa: E402
    Message,
    MqttConfig,
    MqttError,
    MqttService,
    make_message,
    wait_for,
)
from tools.mqtt_broker import free_port  # noqa: E402

PYTHON = sys.executable
PREFIX = "prrobot/test"
SIM_ID = "prrobot-sim-device"
LOG_DIR = ROOT / "logs" / "mqtt_test"


@dataclass
class Result:
    name: str
    passed: bool
    detail: str
    seconds: float


class Child:
    """A tool run as its own process, in its own console group so Ctrl+Break reaches only it."""

    def __init__(self, name: str, args: list[str], ready_text: str) -> None:
        self.name, self.args, self.ready_text = name, args, ready_text
        self.log_path = LOG_DIR / f"{name}.log"
        self.process: subprocess.Popen | None = None
        self._offset = 0  # where this start's output begins in the (appended) log file

    def start(self, timeout_s: float = 15.0) -> None:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        log = open(self.log_path, "a", encoding="utf-8")
        log.write(f"\n===== start {time.strftime('%H:%M:%S')} =====\n")
        log.flush()
        self._offset = log.tell()
        flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}
        self.process = subprocess.Popen(
            [PYTHON, "-u", *self.args], cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
            creationflags=flags, env=env,
        )
        log.close()
        if not wait_for(lambda: self.ready_text in self.output() or self.process.poll() is not None, timeout_s):
            raise RuntimeError(f"{self.name} did not report ready within {timeout_s:.0f} s")
        if self.process.poll() is not None:
            raise RuntimeError(f"{self.name} exited with code {self.process.returncode}: see {self.log_path}")

    def stop(self, timeout_s: float = 8.0) -> str:
        process, self.process = self.process, None
        if process is None or process.poll() is not None:
            return "not running"
        try:
            process.send_signal(signal.CTRL_BREAK_EVENT if os.name == "nt" else signal.SIGINT)
            process.wait(timeout_s)
            return f"stopped (exit {process.returncode})"
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(5)
            return "did not stop in time: killed"

    def output(self) -> str:
        """What this process wrote since its latest start (not earlier runs in the same file)."""
        try:
            with open(self.log_path, "rb") as log:
                log.seek(self._offset)
                return log.read().decode("utf-8", errors="replace")
        except FileNotFoundError:
            return ""


class StatusLog:
    """Every status message from the simulator, in arrival order."""

    def __init__(self, service: MqttService) -> None:
        self.messages: list[Message] = []
        self._lock = threading.Lock()
        service.on_message.append(self._add)

    def _add(self, message: Message) -> None:
        if message.type == "status" and message.source == SIM_ID:
            with self._lock:
                self.messages.append(message)

    def for_task(self, task_id: str) -> list[dict]:
        with self._lock:
            return [m.payload for m in self.messages if m.payload.get("task_id") == task_id]


def main() -> int:
    parser = argparse.ArgumentParser(description="End-to-end MQTT test with broker and simulator processes")
    parser.add_argument("--port", type=int, default=0, help="broker port (default: a free one)")
    parser.add_argument("--skip-app", action="store_true", help="skip the main application check")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    # The test client's own log (connects, disconnects, status), next to the broker's and simulator's.
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    client_log = logging.FileHandler(LOG_DIR / "client.log", encoding="utf-8")
    client_log.setFormatter(logging.Formatter("%(asctime)s.%(msecs)03d %(name)s %(levelname)s %(message)s", "%H:%M:%S"))
    for name in ("services.mqtt_service", "app.mqtt_controller", "paho"):
        logging.getLogger(name).addHandler(client_log)
        logging.getLogger(name).setLevel(logging.DEBUG if name == "paho" else logging.INFO)
    logging.getLogger("services.mqtt_service").info("===== system test start =====")
    port = args.port or free_port()
    broker = Child("broker", ["tools/mqtt_broker.py", "--port", str(port)], "listening on")
    simulator = Child(
        "simulator",
        ["tools/mqtt_device_simulator.py", "--port", str(port), "--telemetry-s", "0"],
        "ready; listening on",
    )
    results: list[Result] = []
    service = MqttService(MqttConfig(port=port, client_id="prrobot-system-test", response_timeout_s=3.0,
                                     reconnect_min_s=1.0, reconnect_max_s=2.0))
    statuses = StatusLog(service)

    def check(name: str, timeout_s: float, body: Callable[[], str]) -> None:
        """Run one check; it passes only if ``body`` returns (it raises with the reason otherwise)."""
        started = time.monotonic()
        outcome: list = []

        def run() -> None:
            try:
                outcome.append((True, body()))
            except Exception as exc:  # noqa: BLE001 - the reason is the result
                outcome.append((False, f"{type(exc).__name__}: {exc}"))

        worker = threading.Thread(target=run, daemon=True)
        worker.start()
        worker.join(timeout_s)
        seconds = time.monotonic() - started
        passed, detail = outcome[0] if outcome else (False, f"timed out after {timeout_s:.0f} s")
        results.append(Result(name, passed, detail, seconds))
        print(f"{'PASS' if passed else 'FAIL'}  {name:34} {seconds:5.2f} s  {detail}", flush=True)

    print(f"MQTT end-to-end test on 127.0.0.1:{port}; process logs in {LOG_DIR}", flush=True)
    try:
        broker.start()
        print(f"[broker]    process {broker.process.pid} ready", flush=True)
        simulator.start()
        print(f"[simulator] process {simulator.process.pid} ready (simulated device, no hardware)", flush=True)
        service.start()

        def broker_connection() -> str:
            if not wait_for(lambda: service.is_connected, 8.0):
                raise AssertionError(f"not connected: {service.status.status.value} {service.status.detail}")
            return f"connected to {service.status.detail}"

        def ping_pong() -> str:
            reply = service.request("PING")
            assert reply.source == SIM_ID, f"answer came from {reply.source}"
            assert reply.payload.get("pong") is True, f"payload {reply.payload}"
            return f"PONG from {reply.source} (reply_to {reply.reply_to[:8]})"

        speak_id = {}

        def speak_received() -> str:
            reply = service.request("SPEAK", {"text": "สวัสดีครับ ยินดีต้อนรับ", "duration_s": 1.5})
            assert reply.payload.get("ok") and reply.payload.get("accepted"), f"payload {reply.payload}"
            speak_id["id"] = reply.reply_to
            if not wait_for(lambda: f"RECV SPEAK id={reply.reply_to[:8]}" in simulator.output(), 3.0):
                raise AssertionError("the simulator's own log does not show the command")
            return f"accepted; simulator log shows RECV SPEAK id={reply.reply_to[:8]}"

        def busy_then_idle() -> str:
            task_id = speak_id.get("id")
            assert task_id, "SPEAK was not accepted, nothing to watch"
            if not wait_for(lambda: any(p.get("state") == "IDLE" for p in statuses.for_task(task_id)), 6.0):
                raise AssertionError(f"no IDLE for the task; statuses seen: {statuses.for_task(task_id)}")
            states = [p["state"] for p in statuses.for_task(task_id)]
            assert states[0] == "BUSY" and states[-1] == "IDLE", f"order {states}"
            assert statuses.for_task(task_id)[-1].get("result") == "done"
            return " -> ".join(states) + " (result done)"

        def stop_command() -> str:
            reply = service.request("SPEAK", {"text": "ข้อความยาวที่จะถูกหยุดกลางทาง", "duration_s": 10})
            task_id = reply.reply_to
            if not wait_for(lambda: any(p.get("state") == "BUSY" for p in statuses.for_task(task_id)), 3.0):
                raise AssertionError("never BUSY")
            busy = service.request("SPEAK", {"text": "อีกข้อความ"})
            assert busy.payload.get("ok") is False and busy.payload.get("error") == "busy", f"while busy: {busy.payload}"
            stopped = service.request("STOP")
            assert stopped.payload.get("stopped_task") == "SPEAK", f"payload {stopped.payload}"
            if not wait_for(lambda: any(p.get("result") == "stopped" for p in statuses.for_task(task_id)), 3.0):
                raise AssertionError("no 'stopped' status for the task")
            time.sleep(0.5)
            assert not any(p.get("result") == "done" for p in statuses.for_task(task_id)), "finished after STOP"
            states = [p["state"] for p in statuses.for_task(task_id)]
            return f"refused 2nd SPEAK as busy; STOP -> {' -> '.join(states)}; state now {service.device_states[SIM_ID]['state']}"

        def no_reply_times_out() -> str:
            # The same broker, a topic prefix nobody listens on: nothing can answer.
            lonely = MqttService(MqttConfig(port=port, client_id="prrobot-system-test-2",
                                            topic_prefix="prrobot/test-nobody", response_timeout_s=1.0))
            lonely.start()
            try:
                assert wait_for(lambda: lonely.is_connected, 5.0), "second client did not connect"
                started = time.monotonic()
                try:
                    reply = lonely.request("PING")
                except MqttError as exc:
                    return f"no PASS without a reply: MqttError after {time.monotonic() - started:.2f} s ({exc})"
                raise AssertionError(f"got a reply from nowhere: {reply}")
            finally:
                lonely.stop()

        def malformed_payloads() -> str:
            before = simulator.output().count("malformed message")
            service.publish_raw("command", b"{this is not json")
            service.publish_raw("command", b'{"v": 9, "id": "x", "ts": "t", "type": "command", "source": "t"}')
            if not wait_for(lambda: simulator.output().count("malformed message") >= before + 2, 3.0):
                raise AssertionError("the simulator did not report the two malformed messages")
            bad = service.request("SPEAK", {"text": "x" * 10, "duration_s": "fast"})
            assert bad.payload.get("ok") is False, f"bad SPEAK accepted: {bad.payload}"
            assert service.request("PING").payload.get("pong") is True, "simulator stopped answering"
            return f"2 unreadable dropped and logged; SPEAK with bad duration refused ({bad.payload['error']}); still answers"

        def reconnect() -> str:
            stopped = broker.stop()
            if not wait_for(lambda: not service.is_connected, 8.0):
                raise AssertionError("still 'connected' after the broker stopped")
            down_state = service.status.status.value
            try:
                service.request("PING", timeout_s=1.0)
                raise AssertionError("a request succeeded with the broker down")
            except MqttError:
                pass
            broker.start()
            if not wait_for(lambda: service.is_connected, 15.0):
                raise AssertionError(f"did not reconnect: {service.status.detail}")
            if not wait_for(lambda: _answers(service), 15.0):
                raise AssertionError("connected again, but the simulator does not answer")
            return f"broker {stopped}; client went {down_state}; broker restarted; both reconnected and PING works"

        check("1 broker connection", 10, broker_connection)
        check("2 PING -> PONG", 6, ping_pong)
        check("3 SPEAK received by simulator", 6, speak_received)
        check("4 status BUSY -> IDLE", 8, busy_then_idle)
        check("5 STOP", 12, stop_command)
        check("6 timeout without a reply", 8, no_reply_times_out)
        check("7 malformed payloads", 10, malformed_payloads)
        check("8 disconnect and reconnect", 45, reconnect)
        if not args.skip_app:
            check("9 main Python app over MQTT", 60, lambda: main_app_check(port))

        def offline_on_exit() -> str:
            how = simulator.stop()
            if not wait_for(lambda: service.device_states.get(SIM_ID, {}).get("online") is False, 5.0):
                raise AssertionError(f"simulator {how}, but no 'offline' status arrived")
            return f"simulator {how}; its status went offline"

        check("10 simulator says offline on exit", 12, offline_on_exit)
    except KeyboardInterrupt:
        print("interrupted", flush=True)
    except Exception as exc:  # a child did not start: report and clean up
        results.append(Result("setup", False, f"{exc}", 0.0))
        print(f"FAIL  setup: {exc}", flush=True)
        traceback.print_exc()
    finally:
        service.stop()
        print(f"[simulator] {simulator.stop()}", flush=True)
        print(f"[broker]    {broker.stop()}", flush=True)

    passed = sum(r.passed for r in results)
    print(f"\nSUMMARY: {passed}/{len(results)} PASS" + ("" if passed == len(results) else
          " — FAILED: " + ", ".join(r.name for r in results if not r.passed)), flush=True)
    print("No real hardware was tested: the device is tools/mqtt_device_simulator.py.", flush=True)
    return 0 if results and passed == len(results) else 1


def _answers(service: MqttService) -> bool:
    try:
        return service.request("PING", timeout_s=1.0).payload.get("pong") is True
    except MqttError:
        return False


def main_app_check(port: int) -> str:
    """The real Application, MQTT enabled, against the same broker and simulator (offscreen)."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    import tempfile

    from PySide6.QtWidgets import QApplication

    from app.application import Application
    from config.settings import load_settings
    from ui.log_bridge import QtLogHandler

    qt = QApplication.instance() or QApplication([])
    data = Path(tempfile.mkdtemp(prefix="mqtt-app-"))  # a scratch database: the real one is not touched
    settings = load_settings(environ={
        "DATA_DIR": str(data / "data"), "LOG_DIR": str(data / "logs"), "MEDIA_DIR": str(data / "media"),
        "STT_ENABLED": "false", "TTS_ENABLED": "false", "VISION_ENABLED": "false",
        "MQTT_ENABLED": "true", "MQTT_PORT": str(port), "MQTT_CLIENT_ID": "prrobot-app-check",
    })
    application = Application(settings, QtLogHandler())
    try:
        card = application.window.admin_screen._dashboard._mqtt_card

        def pump_until(condition, seconds):
            deadline = time.monotonic() + seconds
            while not condition() and time.monotonic() < deadline:
                qt.processEvents()
                time.sleep(0.02)
            return condition()

        if not pump_until(lambda: card.value.text() == "เชื่อมต่อ broker แล้ว", 10):
            raise AssertionError(f"dashboard shows {card.value.text()!r}")
        panel = application.window.admin_screen._dashboard.mqtt_panel
        if not pump_until(lambda: panel.device_tag.text() == "อุปกรณ์: Online (SIMULATOR)", 5):
            raise AssertionError(f"panel device tag: {panel.device_tag.text()!r}")

        # The Admin's buttons, pressed as a person would (the STOP confirmation answered yes).
        panel.ping_button.click()
        if panel.result_label.text() != "รอคำตอบ...":
            raise AssertionError(f"showed {panel.result_label.text()!r} before any reply")
        if not pump_until(lambda: panel.result_label.text().startswith("สำเร็จ"), 5):
            raise AssertionError(f"PING: {panel.result_label.text()!r}")
        panel.speak_text.setText("ทดสอบจาก Dashboard / test from the dashboard")
        panel.speak_button.click()
        if not pump_until(lambda: panel.work_tag.text() == "งาน: BUSY", 5):
            raise AssertionError(f"SPEAK never BUSY: {panel.result_label.text()!r}")
        if not pump_until(lambda: panel.work_tag.text() == "งาน: IDLE", 8):
            raise AssertionError("SPEAK never back to IDLE")
        panel.speak_text.setText("ข้อความยาวสำหรับทดสอบ STOP " * 10)
        panel.speak_button.click()
        pump_until(lambda: panel.work_tag.text() == "งาน: BUSY", 5)
        pump_until(lambda: not application.mqtt.busy, 5)
        panel.confirm_stop = lambda: True
        panel.stop_button.click()
        if not pump_until(lambda: "'stopped_task': 'SPEAK'" in panel.result_label.text(), 5):
            raise AssertionError(f"STOP: {panel.result_label.text()!r}")
        robot = "simulated" if application.robot.simulated else "real"
        return ("app connected; Dashboard buttons: PING answered by SIMULATOR, SPEAK went BUSY -> IDLE, "
                f"STOP stopped SPEAK; broker and device shown separately; robot adapter stays {robot}")
    finally:
        application.shutdown()
        application.window.deleteLater()


if __name__ == "__main__":
    raise SystemExit(main())

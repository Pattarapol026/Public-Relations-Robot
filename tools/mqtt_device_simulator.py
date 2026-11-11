"""A simulated MQTT device (robot controller board) for communication tests.

It is NOT a robot: it drives no motor, wheel, arm or speaker. "Speaking" is a
timer; every message it sends says ``"simulated": true``.

    .venv313\\Scripts\\python.exe tools\\mqtt_device_simulator.py               # broker at 127.0.0.1:1883
    .venv313\\Scripts\\python.exe tools\\mqtt_device_simulator.py --port 1884   # Ctrl+C to stop

Commands on ``<prefix>/command`` (schema of ``services/mqtt_service.py``), each
answered on ``<prefix>/response`` with ``reply_to`` = the command's id:

* PING      -> {"ok": true, "pong": true}
* STATUS    -> {"ok": true, "state": ..., "task": ...}
* TEST_ECHO -> {"ok": true, "echo": <payload>}
* SPEAK {"text": "...", "duration_s": optional} -> {"ok": true, "accepted": true};
  status BUSY now, then IDLE (result "done") when the simulated speech ends.
  While BUSY a new SPEAK is refused (ok false, error "busy").
* STOP      -> {"ok": true, "stopped_task": "SPEAK" | null}; status STOPPED, then IDLE
  (result "stopped").

Anything else, or a malformed message, is refused with ok false and a reason
(an unreadable message cannot be answered: it is logged and dropped).
Its status on ``<prefix>/status/<client id>`` is retained: online + state, and "offline"
via the MQTT last will if the simulator dies without saying goodbye.
"""
from __future__ import annotations

import argparse
import logging
import signal
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.mqtt_service import (  # noqa: E402
    MAX_SPEAK_CHARS,
    SAFE_COMMANDS,
    MessageError,
    make_message,
    parse_message,
)

logger = logging.getLogger("simulator")

IDLE, BUSY, STOPPED = "IDLE", "BUSY", "STOPPED"
MAX_SPEAK_SECONDS = 30.0


class DeviceSimulator:
    def __init__(
        self, host: str = "127.0.0.1", port: int = 1883, prefix: str = "prrobot/test",
        client_id: str = "prrobot-sim-device", telemetry_s: float = 0.0,
    ) -> None:
        self.host, self.port, self.prefix, self.client_id = host, port, prefix.rstrip("/"), client_id
        self.telemetry_s = telemetry_s
        self.handled: list[str] = []  # commands answered with ok true, in order
        self.refused: list[str] = []
        self.connected = threading.Event()
        self.state = IDLE
        self._task: dict | None = None  # {"command", "id", "timer"}
        self._state_lock = threading.Lock()
        self._client = None
        self._stop = threading.Event()
        self._started = time.monotonic()
        self._telemetry_thread: threading.Thread | None = None

    def topic(self, name: str) -> str:
        return f"{self.prefix}/{name}"

    # --- lifecycle ---

    def start(self) -> None:
        import paho.mqtt.client as mqtt

        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=self.client_id)
        client.reconnect_delay_set(1, 5)
        client.will_set(self.topic(f"status/{self.client_id}"), self._status_message(online=False).to_json(), qos=1, retain=True)
        client.on_connect = self._on_connect
        # Ready only once the broker has confirmed the subscription: a command
        # sent before that would be lost.
        client.on_subscribe = self._on_subscribed
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message
        self._client = client
        logger.info("connecting to %s:%d (topics %s/...)", self.host, self.port, self.prefix)
        client.connect_async(self.host, self.port, keepalive=30)
        client.loop_start()
        if self.telemetry_s > 0:
            self._telemetry_thread = threading.Thread(target=self._telemetry_loop, daemon=True)
            self._telemetry_thread.start()

    def stop(self) -> None:
        """Say "offline", disconnect and stop. Safe to call twice."""
        self._stop.set()
        self._cancel_task()
        client, self._client = self._client, None
        if client is None:
            return
        try:
            if self.connected.is_set():
                client.publish(
                    self.topic(f"status/{self.client_id}"), self._status_message(online=False).to_json(), qos=1, retain=True
                ).wait_for_publish(2.0)
            client.disconnect()
        finally:
            client.loop_stop()
        logger.info("stopped")

    def publish(self, name: str, raw: bytes) -> None:
        """Send raw bytes (tests use it for malformed and duplicate messages)."""
        self._client.publish(self.topic(name), raw, qos=1).wait_for_publish(2.0)

    def send_telemetry(self) -> None:
        reading = {"simulated": True, "uptime_s": round(time.monotonic() - self._started, 1),
                   "state": self.state, "battery_pct": None}
        self.publish("telemetry", make_message("telemetry", self.client_id, payload=reading).to_json())

    # --- MQTT callbacks ---

    def _on_connect(self, client, _userdata, _flags, reason_code, _properties) -> None:
        if reason_code.is_failure:
            logger.warning("broker refused the connection: %s", reason_code)
            return
        client.subscribe(self.topic("command"), qos=1)
        self._publish_status()
        logger.info("connected; subscribing to %s", self.topic("command"))

    def _on_subscribed(self, *_args) -> None:
        # Only now can commands reach it; tools/mqtt_system_test.py waits for this line.
        self.connected.set()
        logger.info("ready; listening on %s", self.topic("command"))

    def _on_disconnect(self, _client, _userdata, _flags, reason_code, _properties) -> None:
        self.connected.clear()
        if not self._stop.is_set():
            logger.warning("connection lost (%s); reconnecting", reason_code)

    def _on_message(self, client, _userdata, mqtt_message) -> None:
        try:
            command = parse_message(mqtt_message.payload)
        except MessageError as exc:
            logger.warning("RECV malformed message on %s dropped: %s", mqtt_message.topic, exc)
            return
        if command.type != "command":
            return  # e.g. a retained status the test broker hands out
        name = command.command.upper()
        logger.info("RECV %s id=%s from %s payload=%s", name, command.id[:8], command.source, command.payload)
        if name not in SAFE_COMMANDS:
            payload = self._refuse(name, f"command {name} is not supported by the simulator")
        else:
            payload = getattr(self, f"_do_{name.lower()}")(command)
        payload["simulated"] = True
        reply = make_message("response", self.client_id, name, payload, reply_to=command.id)
        client.publish(self.topic("response"), reply.to_json(), qos=1)
        logger.info("SEND response to %s id=%s: %s", name, command.id[:8], payload)

    # --- commands ---

    def _do_ping(self, _command) -> dict:
        self.handled.append("PING")
        return {"ok": True, "pong": True}

    def _do_status(self, _command) -> dict:
        self.handled.append("STATUS")
        with self._state_lock:
            task = self._task["command"] if self._task else None
            return {"ok": True, "state": self.state, "task": task, "device": "mqtt-simulator",
                    "uptime_s": round(time.monotonic() - self._started, 1), "actuators": "none"}

    def _do_test_echo(self, command) -> dict:
        self.handled.append("TEST_ECHO")
        return {"ok": True, "echo": command.payload}

    def _do_speak(self, command) -> dict:
        text = command.payload.get("text")
        if not isinstance(text, str) or not text.strip() or len(text) > MAX_SPEAK_CHARS:
            return self._refuse("SPEAK", f"payload.text must be 1–{MAX_SPEAK_CHARS} characters")
        duration = command.payload.get("duration_s", min(10.0, max(0.5, 0.06 * len(text))))
        if not isinstance(duration, (int, float)) or isinstance(duration, bool) or not 0 < duration <= MAX_SPEAK_SECONDS:
            return self._refuse("SPEAK", f"payload.duration_s must be a number in (0, {MAX_SPEAK_SECONDS:.0f}]")
        with self._state_lock:
            if self.state == BUSY:
                return self._refuse("SPEAK", "busy", task=self._task["command"])
            timer = threading.Timer(float(duration), self._finish_task, args=(command.id,))
            timer.daemon = True
            self._task = {"command": "SPEAK", "id": command.id, "timer": timer}
            self.state = BUSY
        self.handled.append("SPEAK")
        # Simulated: nothing is played on any speaker.
        logger.info("SIMULATED SPEAK for %.1f s (no audio is played): %r", duration, text)
        self._publish_status(task="SPEAK", task_id=command.id)
        timer.start()
        return {"ok": True, "accepted": True, "state": BUSY, "duration_s": duration}

    def _do_stop(self, command) -> dict:
        with self._state_lock:
            task = self._task
            self._task = None
            if task is not None:
                task["timer"].cancel()
            self.state = STOPPED
        self.handled.append("STOP")
        self._publish_status(task=task["command"] if task else None,
                             task_id=task["id"] if task else None, result="stopped")
        with self._state_lock:
            self.state = IDLE
        self._publish_status(result="stopped")
        return {"ok": True, "stopped_task": task["command"] if task else None, "state": IDLE}

    def _finish_task(self, task_id: str) -> None:
        with self._state_lock:
            if self._task is None or self._task["id"] != task_id:
                return  # stopped meanwhile
            command = self._task["command"]
            self._task = None
            self.state = IDLE
        logger.info("SIMULATED %s finished", command)
        self._publish_status(task=command, task_id=task_id, result="done")

    def _cancel_task(self) -> None:
        with self._state_lock:
            if self._task is not None:
                self._task["timer"].cancel()
                self._task = None
            self.state = IDLE

    def _refuse(self, name: str, reason: str, **extra) -> dict:
        self.refused.append(name)
        logger.warning("REFUSED %s: %s", name, reason)
        return {"ok": False, "error": reason, **extra}

    # --- status ---

    def _status_message(self, online: bool = True, task=None, task_id=None, result=None):
        payload = {"online": online, "state": self.state if online else "OFFLINE", "task": task,
                   "task_id": task_id, "result": result, "simulated": True}
        return make_message("status", self.client_id, payload=payload)

    def _publish_status(self, **fields) -> None:
        client = self._client
        if client is None:
            return
        message = self._status_message(**fields)
        client.publish(self.topic(f"status/{self.client_id}"), message.to_json(), qos=1, retain=True)
        logger.info("SEND status %s", {k: v for k, v in message.payload.items() if v is not None})

    def _telemetry_loop(self) -> None:
        while not self._stop.wait(self.telemetry_s):
            if self.connected.is_set():
                try:
                    self.send_telemetry()
                except Exception:
                    logger.exception("telemetry failed")


def main() -> int:
    parser = argparse.ArgumentParser(description="Simulated MQTT device (no hardware is driven)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=1883)
    parser.add_argument("--prefix", default="prrobot/test")
    parser.add_argument("--client-id", default="prrobot-sim-device")
    parser.add_argument("--telemetry-s", type=float, default=5.0, help="0 = no telemetry")
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s.%(msecs)03d %(name)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S", stream=sys.stdout,
    )
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    simulator = DeviceSimulator(args.host, args.port, args.prefix, args.client_id, args.telemetry_s)
    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_args: stop.set())
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, lambda *_args: stop.set())  # Ctrl+Break / console close
    simulator.start()
    print(f"Simulated device {args.client_id} -> {args.host}:{args.port} {args.prefix}/... "
          "(no hardware is driven). Ctrl+C to stop.", flush=True)
    while not stop.wait(0.2):
        pass
    simulator.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

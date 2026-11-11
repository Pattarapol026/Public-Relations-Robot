"""Check the MQTT link with the app's own MQTT service: connect, PING / STATUS / TEST_ECHO, telemetry.

    .venv313\\Scripts\\python.exe tools\\mqtt_check.py --self-test     # local broker + simulated device, one command
    .venv313\\Scripts\\python.exe tools\\mqtt_check.py                 # against the broker in .env (MQTT_HOST/PORT)

Only the safe test commands are sent. A response from the simulated device
says "simulated": true; until a real device answers, nothing here shows that
hardware works.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.mqtt_controller import mqtt_config  # noqa: E402
from config.settings import load_settings  # noqa: E402
from services.mqtt_service import MqttConfig, MqttError, MqttService, wait_for  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--self-test", action="store_true",
                        help="start a local broker and the simulated device for this run")
    parser.add_argument("--listen-s", type=float, default=3.0, help="how long to wait for telemetry")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    broker = simulator = None
    if args.self_test:
        from tools.mqtt_broker import LocalBroker, free_port
        from tools.mqtt_device_simulator import DeviceSimulator

        broker = LocalBroker(free_port())
        broker.start()
        print(f"[broker] local test broker on 127.0.0.1:{broker.port}")
        config = MqttConfig(port=broker.port, client_id="prrobot-app-check")
        simulator = DeviceSimulator(port=broker.port, prefix=config.topic_prefix, telemetry_s=1.0)
        simulator.start()
        simulator.connected.wait(5.0)
        print("[device] simulated device connected (not real hardware)")
    else:
        config = mqtt_config(load_settings())

    service = MqttService(config)
    service.on_status.append(lambda s: print(f"[status] {s.since.astimezone():%H:%M:%S} {s.status.value} {s.detail}"))
    telemetry = []
    service.on_message.append(lambda m: telemetry.append(m) if m.type == "telemetry" else None)
    failures = 0
    try:
        service.start()
        if not wait_for(lambda: service.is_connected, 10.0):
            print(f"FAIL could not connect to {config.host}:{config.port}: {service.status.detail}")
            return 1
        for command, payload in (("PING", None), ("STATUS", None), ("TEST_ECHO", {"text": "ทดสอบ MQTT", "n": 42})):
            started = time.monotonic()
            try:
                reply = service.request(command, payload)
            except MqttError as exc:
                failures += 1
                print(f"FAIL {command}: {exc}")
                continue
            ms = (time.monotonic() - started) * 1000
            print(f"OK   {command:9} {ms:6.1f} ms from {reply.source}: {reply.payload}")
        if args.self_test or args.listen_s:
            wait_for(lambda: telemetry, args.listen_s)
            if telemetry:
                print(f"OK   telemetry from {telemetry[-1].source}: {telemetry[-1].payload}")
            else:
                print("--   no telemetry within the wait (a device may simply not send any)")
        print(f"counters: {service.counters}")
    finally:
        service.stop()
        if simulator is not None:
            simulator.stop()
        if broker is not None:
            broker.stop()
            print("[broker] stopped")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

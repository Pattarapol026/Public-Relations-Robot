"""Checks the link to the robot without sending it anything: the network, the MQTT broker, and what the robot says.

    .venv313\\Scripts\\python.exe -m tools.check_robot_link

It only listens (subscribes to everything for a few seconds) and prints the topics it hears, so the arm's real topic and
messages can be read off the robot instead of guessed. It never publishes.
"""
from __future__ import annotations

import socket
import subprocess
import sys
import time

HOST = "192.168.10.1"
PORT = 1883
LISTEN_S = 12.0


def main() -> int:
    ping = subprocess.run(["ping", "-n", "1", "-w", "1500", HOST], capture_output=True)
    print(f"ping {HOST}: {'answers' if ping.returncode == 0 else 'no answer'}")
    try:
        socket.create_connection((HOST, PORT), timeout=3).close()
        print(f"port {PORT}: open")
    except OSError as exc:
        print(f"port {PORT}: cannot connect ({exc})")
        print("-> run tools\\robot_network.bat (as administrator), check the cable and that the robot is on")
        return 1
    import paho.mqtt.client as mqtt

    heard: dict[str, str] = {}

    def on_message(_client, _userdata, message) -> None:
        heard[message.topic] = f"{message.payload[:80]!r}{' (retained)' if message.retain else ''}"

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="check-robot-link-readonly")
    client.on_connect = lambda c, *_: c.subscribe("#")
    client.on_message = on_message
    client.connect(HOST, PORT, 20)
    client.loop_start()
    time.sleep(LISTEN_S)
    client.loop_stop()
    print(f"broker: connected; heard {len(heard)} topic(s) in {LISTEN_S:.0f} s")
    for topic, payload in sorted(heard.items()):
        print(f"  {topic}  {payload}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""A local MQTT broker for communication tests (amqtt), reachable from this computer only.

    .venv313\\Scripts\\python.exe tools\\mqtt_broker.py              # 127.0.0.1:1883, Ctrl+C to stop
    .venv313\\Scripts\\python.exe tools\\mqtt_broker.py --port 18830

It binds to 127.0.0.1, so nothing outside this computer can connect, and it
allows anonymous clients because of that. A broker for real devices on the
network needs its own setup (user names, TLS) and is not this tool.

Known amqtt 0.12 quirk (found by tests/test_mqtt.py): a new subscriber may also
receive retained messages of topics it did not subscribe to. The service and
the simulator check every message, so they are not affected; Mosquitto does
not do this.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import socket
import sys
import threading

LOCALHOST = "127.0.0.1"


def _config(port: int) -> dict:
    return {
        "listeners": {"default": {"type": "tcp", "bind": f"{LOCALHOST}:{port}"}},
        "plugins": {"amqtt.plugins.authentication.AnonymousAuthPlugin": {"allow_anonymous": True}},
    }


class LocalBroker:
    """Runs the broker on its own thread and event loop; ``start``/``stop`` block until done."""

    def __init__(self, port: int = 1883) -> None:
        self.port = port
        self._loop: asyncio.AbstractEventLoop | None = None
        self._broker = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._error: BaseException | None = None

    def start(self, timeout_s: float = 10.0) -> None:
        if self._thread is not None:
            return
        self._ready.clear()
        self._error = None
        self._thread = threading.Thread(target=self._run, name=f"mqtt-broker-{self.port}", daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout_s):
            raise RuntimeError("the MQTT broker did not start in time")
        if self._error is not None:
            self._thread = None
            raise RuntimeError(f"the MQTT broker could not start: {self._error}") from self._error
        # Broker.start() returns once the listener is open, so it is reachable now.

    def stop(self, timeout_s: float = 10.0) -> None:
        thread, loop = self._thread, self._loop
        if thread is None or loop is None:
            return
        asyncio.run_coroutine_threadsafe(self._shutdown(), loop).result(timeout_s)
        thread.join(timeout_s)
        self._thread = None

    def _run(self) -> None:
        from amqtt.broker import Broker

        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        try:
            self._broker = Broker(_config(self.port), loop=loop)
            loop.run_until_complete(self._broker.start())
        except BaseException as exc:  # noqa: BLE001 - reported to start()
            self._error = exc
            self._ready.set()
            loop.close()
            return
        self._ready.set()
        loop.run_forever()
        loop.close()

    async def _shutdown(self) -> None:
        try:
            await self._broker.shutdown()
        finally:
            asyncio.get_running_loop().call_soon(asyncio.get_running_loop().stop)


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind((LOCALHOST, 0))
        return probe.getsockname()[1]


def main() -> int:
    parser = argparse.ArgumentParser(description="Local MQTT test broker (127.0.0.1 only)")
    parser.add_argument("--port", type=int, default=1883)
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    broker = LocalBroker(args.port)
    try:
        broker.start()
    except RuntimeError as exc:
        print(exc, file=sys.stderr)
        return 1
    print(f"MQTT test broker listening on {LOCALHOST}:{args.port} (this computer only). Ctrl+C to stop.",
          flush=True)
    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_args: stop.set())
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, lambda *_args: stop.set())  # Ctrl+Break, sent by tools/mqtt_system_test.py
    while not stop.wait(0.2):
        pass
    broker.stop()
    print("MQTT test broker stopped.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

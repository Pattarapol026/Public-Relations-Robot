"""Admin dashboard section: send MQTT test commands (PING, SPEAK, STOP) and see the real answers.

Every result shown comes from the device's own reply to that command; until it
arrives the panel says it is waiting, and after the timeout it says so. Broker,
device and work state are shown separately, and a reply from the simulator is
marked SIMULATOR.
"""
from __future__ import annotations

from datetime import datetime

from PySide6.QtWidgets import QHBoxLayout, QLineEdit, QMessageBox, QWidget

from app.mqtt_controller import CommandRecord, MqttController, Outcome
from services.mqtt_service import MAX_SPEAK_CHARS, MqttStatus
from ui.widgets import make_button, make_label, make_section, set_style_property

DEFAULT_SPEAK_TEXT = "สวัสดีครับ ยินดีต้อนรับ / Hello, welcome"
_BROKER = {
    MqttStatus.CONNECTED: ("Broker: Connected", "safe"),
    MqttStatus.CONNECTING: ("Broker: Connecting", "wait"),
    MqttStatus.DISCONNECTED: ("Broker: Disconnected", "danger"),
    MqttStatus.ERROR: ("Broker: Error", "danger"),
}
_OUTCOME = {
    Outcome.PENDING: ("รอคำตอบ...", "info"),
    Outcome.OK: ("สำเร็จ", "info"),
    Outcome.REFUSED: ("อุปกรณ์ปฏิเสธ", "error"),
    Outcome.TIMEOUT: ("Timeout — ไม่มีอุปกรณ์ตอบ", "error"),
    Outcome.FAILED: ("ส่งไม่ได้", "error"),
}


def _clock(moment: datetime | None) -> str:
    return moment.astimezone().strftime("%H:%M:%S.%f")[:-3] if moment else "-"


class MqttControlPanel(QWidget):
    def __init__(self, mqtt: MqttController | None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._mqtt = mqtt
        section, layout = make_section(
            "ควบคุม MQTT (ทดสอบกับ SIMULATOR)",
            "ส่งคำสั่งทดสอบไปยังอุปกรณ์ปลายทางผ่าน MQTT broker ในเครื่องนี้ (127.0.0.1) "
            "SPEAK เป็นการจำลองบนอุปกรณ์ทดสอบ ไม่ใช่เสียงจากหุ่นยนต์จริง และไม่มีคำสั่งใดสั่งมอเตอร์หรือการเคลื่อนที่",
        )
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(section)

        states = QHBoxLayout()
        self.broker_tag = make_label("", "tag")
        self.device_tag = make_label("", "tag")
        self.work_tag = make_label("", "tag")
        for tag in (self.broker_tag, self.device_tag, self.work_tag):
            states.addWidget(tag)
        states.addStretch(1)
        layout.addLayout(states)

        row = QHBoxLayout()
        self.ping_button = make_button("PING", "secondary", self.send_ping)
        self.speak_text = QLineEdit(DEFAULT_SPEAK_TEXT)
        self.speak_text.setMaxLength(MAX_SPEAK_CHARS)
        self.speak_text.setPlaceholderText("ข้อความทดสอบ ไทยหรืออังกฤษ")
        self.speak_text.textChanged.connect(self._show_count)
        self.speak_button = make_button("SPEAK", "primary", self.send_speak)
        self.stop_button = make_button("STOP", "danger", self.send_stop)
        row.addWidget(self.ping_button)
        row.addWidget(self.speak_text, 1)
        row.addWidget(self.speak_button)
        row.addWidget(self.stop_button)
        layout.addLayout(row)
        self.count_label = make_label("", "hint")
        layout.addWidget(self.count_label)

        self.sent_label = make_label("ยังไม่ได้ส่งคำสั่ง", "hint", word_wrap=True)
        self.result_label = make_label("", "message", word_wrap=True)
        layout.addWidget(self.sent_label)
        layout.addWidget(self.result_label)

        # Asked before STOP is sent; tests replace it.
        self.confirm_stop = self._ask_stop
        if mqtt is not None:
            mqtt.status_changed.connect(self.refresh)
            mqtt.message_received.connect(self.refresh)
            mqtt.command_changed.connect(self._show_command)
        self._show_count()
        self.refresh()

    # --- actions ---

    def send_ping(self) -> None:
        self._send("PING")

    def send_speak(self) -> None:
        text = self.speak_text.text().strip()
        if not text:
            self._show_problem("ใส่ข้อความที่จะให้ SIMULATOR จำลองการพูดก่อน")
            return
        self._send("SPEAK", {"text": text})

    def send_stop(self) -> None:
        if self.confirm_stop():
            self._send("STOP")

    def _send(self, command: str, payload: dict | None = None) -> None:
        if self._mqtt is None:
            self._show_problem("MQTT ปิดใช้งาน (ตั้ง MQTT_ENABLED=true ในไฟล์ .env)")
            return
        self._mqtt.send(command, payload)

    def _ask_stop(self) -> bool:
        answer = QMessageBox.question(
            self, "ยืนยัน STOP",
            "ส่งคำสั่ง STOP ให้อุปกรณ์จำลอง (SIMULATOR) หยุดงานที่ทำอยู่ใช่หรือไม่\n"
            "คำสั่งนี้ไม่ได้ส่งถึงหุ่นยนต์จริง",
        )
        return answer == QMessageBox.StandardButton.Yes

    # --- display ---

    def refresh(self, *_args) -> None:
        mqtt = self._mqtt
        if mqtt is None:
            self._tag(self.broker_tag, "Broker: ปิดใช้งาน", "")
            self._tag(self.device_tag, "อุปกรณ์: Unknown", "")
            self._tag(self.work_tag, "งาน: -", "")
            self._enable("MQTT ปิดใช้งาน (ตั้ง MQTT_ENABLED=true ในไฟล์ .env)")
            return
        text, level = _BROKER[mqtt.status.status]
        self._tag(self.broker_tag, text, level)
        presence, state = mqtt.device_status()
        if state is None:
            self._tag(self.device_tag, "อุปกรณ์: Unknown", "wait")
            self._tag(self.work_tag, "งาน: -", "")
        else:
            kind = " (SIMULATOR)" if state.get("simulated") else ""
            online = presence == "online"
            self._tag(self.device_tag, f"อุปกรณ์: {'Online' if online else 'Offline'}{kind}",
                      "safe" if online else "danger")
            work = state.get("state") or "-"
            self._tag(self.work_tag, f"งาน: {work}", {"IDLE": "safe", "BUSY": "wait"}.get(work, "danger"))
        self._enable(mqtt.can_send())

    def _show_command(self, record: CommandRecord) -> None:
        shown = f" \"{record.payload['text']}\"" if record.command == "SPEAK" else ""
        self.sent_label.setText(f"คำสั่งล่าสุด: {record.command}{shown} · ส่งเมื่อ {_clock(record.sent_at)}")
        text, level = _OUTCOME[record.outcome]
        if record.reply is not None:
            source = "SIMULATOR" if record.from_simulator else "อุปกรณ์"
            body = {k: v for k, v in record.reply.payload.items() if k != "simulated"}
            text = (f"{text} · ได้รับ {_clock(record.received_at)} ({record.round_trip_ms:.0f} ms) "
                    f"จาก {source} {record.reply.source}: {body}")
        elif record.detail:
            text = f"{text}: {record.detail}"
        self.result_label.setText(text)
        set_style_property(self.result_label, "level", level)
        self.refresh()

    def _show_problem(self, text: str) -> None:
        self.result_label.setText(text)
        set_style_property(self.result_label, "level", "error")

    def _show_count(self) -> None:
        self.count_label.setText(f"{len(self.speak_text.text())}/{MAX_SPEAK_CHARS} ตัวอักษร")

    def _enable(self, reason: str) -> None:
        for button in (self.ping_button, self.speak_button, self.stop_button):
            button.setEnabled(not reason)
            button.setToolTip(reason)

    @staticmethod
    def _tag(label, text: str, level: str) -> None:
        label.setText(text)
        set_style_property(label, "level", level)

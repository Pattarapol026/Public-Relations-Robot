"""Admin page: control the real robot over MQTT — turn by an angle, move by a distance, STOP.

One topic bar at the top serves everything (one robot): turning (left/right,
degrees) and moving (forward/backward, centimetres) in cards of their own, the
typed messages, and the visitor screen's buttons. The safety confirmation and
STOP are shared and sit above the cards. What is shown about a command keeps three things
apart: it was sent, the broker acknowledged it, and the hardware answered. Only
an answer on the reply topic counts as the hardware having received it.
"""
from __future__ import annotations

from datetime import datetime, timezone

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QLineEdit,
    QPlainTextEdit,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app.hardware_controller import GROUP_MOVE, GROUP_TURN, HardwareController
from services.hardware_link import (
    KIND_ARM,
    KIND_STOP,
    KIND_TEXT,
    MAX_TEXT_CHARS,
    MOVES,
    TURNS,
    ReceivedMessage,
    SentCommand,
    format_payload,
    unit,
    value_range,
)
from services.mqtt_service import MqttStatus
from ui.widgets import make_button, make_label, make_section, set_style_property

HISTORY_SIZE = 10
RECEIVED_SIZE = 50  # messages kept in the "received" box
_BROKER = {
    MqttStatus.CONNECTED: ("Broker: เชื่อมต่อแล้ว", "safe"),
    MqttStatus.CONNECTING: ("Broker: กำลังเชื่อมต่อ", "wait"),
    MqttStatus.DISCONNECTED: ("Broker: ไม่ได้เชื่อมต่อ", "danger"),
    MqttStatus.ERROR: ("Broker: ผิดพลาด", "danger"),
}
# Only arrows the Sarabun font has (↺ ↻ show as empty boxes).
_DIRECTION_LABELS = {"left": "◀  หันซ้าย", "right": "หันขวา  ▶", "forward": "▲  เดินหน้า", "backward": "▼  ถอยหลัง"}
TURN_PRESETS = (15, 30, 45, 90)  # degrees
MOVE_PRESETS = (10, 25, 50, 100)  # centimetres
SETUP_HINT = (
    "ยังไม่ได้ตั้งค่าการเชื่อมต่อฮาร์ดแวร์ ใส่บรรทัดเหล่านี้ในไฟล์ .env แล้วเปิดโปรแกรมใหม่\n"
    "MQTT_HW_HOST=192.168.10.1\n"
    "MQTT_HW_DIRECTION_TOPIC=robot/direction\n"
    "MQTT_HW_STOP_PAYLOAD=stop        (ข้อความหยุดที่ฮาร์ดแวร์เข้าใจ ถามทีมฮาร์ดแวร์)\n"
    "MQTT_HW_REPLY_TOPIC=             (topic ที่ฮาร์ดแวร์ตอบกลับ ถ้ามี)"
)


def wrapped_check(layout, text: str) -> QCheckBox:
    """A check box whose long text wraps. (A QCheckBox with a long text never wraps: it widens the whole page, and
    the right side is cut off, as in the arm card on 10 Nov 2026.) Clicking the text toggles the box."""
    row = QHBoxLayout()
    row.setSpacing(8)
    box = QCheckBox()
    label = make_label(text, "message", word_wrap=True)
    label.mousePressEvent = lambda _event: box.toggle() if box.isEnabled() else None
    row.addWidget(box, 0, Qt.AlignmentFlag.AlignTop)
    row.addWidget(label, 1)
    layout.addLayout(row)
    box.label = label  # type: ignore[attr-defined]
    return box


def _clock(moment: datetime | None) -> str:
    return moment.astimezone().strftime("%H:%M:%S") if moment else "-"


class _ControlCard(QWidget):
    """One kind of movement: its topic, how far (quick picks or typed), and its two buttons."""

    def __init__(self, tab: HardwareTab, group: str, title: str, hint: str, directions: tuple[str, ...],
                 presets: tuple[int, ...], default: int) -> None:
        super().__init__()
        self._tab = tab
        self._hardware = tab.hardware
        self.group = group
        self.directions = directions
        section, layout = make_section(title, hint)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(section)

        low, high = value_range(self._hardware.config, directions[0]) if self._hardware else (1, 100)
        amount = QHBoxLayout()
        amount.setSpacing(6)
        self.preset_buttons = []
        for value in presets:
            if low <= value <= high:
                button = make_button(f"{value}", "secondary", lambda v=value: self.value_spin.setValue(v))
                self.preset_buttons.append(button)
                amount.addWidget(button)
        amount.addStretch(1)
        self.value_spin = QSpinBox()
        self.value_spin.setRange(low, high)
        # The same amount the visitor screen's buttons send (kept by the controller).
        self.value_spin.setValue(self._hardware.amount(group) if self._hardware else min(max(default, low), high))
        self.value_spin.setSuffix(f" {unit(directions[0])}")
        self.value_spin.valueChanged.connect(self._on_value)
        amount.addWidget(self.value_spin)
        layout.addLayout(amount)
        layout.addWidget(make_label(f"ได้ตั้งแต่ {low} ถึง {high} {unit(directions[0])}", "hint"))

        buttons = QGridLayout()
        buttons.setSpacing(10)
        self.buttons = {
            direction: make_button(_DIRECTION_LABELS[direction], "primary",
                                   lambda d=direction: self.send(d))
            for direction in directions
        }
        for column, direction in enumerate(directions):
            buttons.addWidget(self.buttons[direction], 0, column)
        layout.addLayout(buttons)
        self.sample_label = make_label("", "hint", word_wrap=True)
        layout.addWidget(self.sample_label)

    # --- actions ---

    def _on_value(self, value: int) -> None:
        if self._hardware is not None:
            self._hardware.set_amount(self.group, value)
        self._tab.refresh()

    def send(self, direction: str) -> None:
        if self._hardware is None or not self._tab.apply_typed_topic():
            return  # a typed topic that is not allowed: nothing is sent
        reason = self._hardware.send_direction(direction, self.value_spin.value())
        self._tab.refresh()
        if reason:
            self._tab.show_problem(reason)

    # --- display ---

    def refresh(self) -> str:
        """Enable what may be sent now; returns why not, if nothing may."""
        hardware = self._hardware
        if hardware is None:
            for widget in (*self.buttons.values(), *self.preset_buttons, self.value_spin):
                widget.setEnabled(False)
            return ""
        if self.value_spin.value() != hardware.amount(self.group):  # changed on the visitor screen
            self.value_spin.blockSignals(True)
            self.value_spin.setValue(hardware.amount(self.group))
            self.value_spin.blockSignals(False)
        value = self.value_spin.value()
        reason = ""
        for direction, button in self.buttons.items():
            why = hardware.direction_refusal(direction, value)
            button.setEnabled(not why)
            button.setToolTip(why)
            reason = reason or why
        samples = " / ".join(f"\"{format_payload(hardware.config, d, value)}\"" for d in self.directions)
        # Why the buttons are grey, right where they are, rather than a silent press.
        if reason:
            self.sample_label.setText(f"ยังกดไม่ได้: {reason}")
        else:
            self.sample_label.setText(f"จะส่ง {samples} → {hardware.topic}")
        set_style_property(self.sample_label, "level", "error" if reason else "")
        return reason


class _MessageCard(QWidget):
    """Type a message and send it as it is; listen to topics and see what arrives."""

    def __init__(self, tab: HardwareTab) -> None:
        super().__init__()
        self._tab = tab
        self._hardware = tab.hardware
        section, layout = make_section(
            "พิมพ์ส่ง / รับข้อความ",
            "ส่งข้อความตามที่พิมพ์ไปยัง topic ด้านบน ฮาร์ดแวร์เป็นผู้ตีความเอง "
            "หุ่นยนต์อาจเคลื่อนที่ตามข้อความ จึงใช้กติกาความปลอดภัยเดียวกับปุ่มทิศทาง",
        )
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(section)

        send_row = QHBoxLayout()
        self.text_input = QLineEdit()
        self.text_input.setMaxLength(MAX_TEXT_CHARS)
        self.text_input.setPlaceholderText("พิมพ์ข้อความ เช่น ไปห้องกิจกรรมหน่อย หรือ goto activity_room")
        self.text_input.returnPressed.connect(self.send)
        self.text_input.textChanged.connect(lambda _text: tab.refresh())
        send_row.addWidget(self.text_input, 1)
        self.send_button = make_button("ส่งข้อความ", "primary", self.send)
        send_row.addWidget(self.send_button)
        layout.addLayout(send_row)
        self.sample_label = make_label("", "hint", word_wrap=True)
        layout.addWidget(self.sample_label)

        layout.addWidget(make_label("รับข้อความ", "eyebrow"))
        listen_row = QHBoxLayout()
        self.listen_input = QLineEdit()
        self.listen_input.setPlaceholderText("topic ที่จะฟัง เช่น robot/# หรือ robot/r1/reply")
        self.listen_input.returnPressed.connect(self.listen)
        listen_row.addWidget(self.listen_input, 1)
        self.listen_button = make_button("ฟัง", "secondary", self.listen)
        listen_row.addWidget(self.listen_button)
        self.listening_box = QComboBox()
        listen_row.addWidget(self.listening_box, 1)
        self.unlisten_button = make_button("เลิกฟัง", "secondary", self.unlisten)
        listen_row.addWidget(self.unlisten_button)
        layout.addLayout(listen_row)
        self.listening_label = make_label("", "hint", word_wrap=True)
        layout.addWidget(self.listening_label)
        self.received_box = QPlainTextEdit()
        self.received_box.setReadOnly(True)
        self.received_box.setMaximumBlockCount(RECEIVED_SIZE)
        self.received_box.setPlaceholderText("ยังไม่มีข้อความเข้ามา")
        self.received_box.setMinimumHeight(140)
        layout.addWidget(self.received_box)

    # --- actions ---

    def send(self) -> None:
        if self._hardware is None or not self._tab.apply_typed_topic():
            return  # a typed topic that is not allowed: nothing is sent
        reason = self._hardware.send_text(self.text_input.text())
        if not reason:
            self.text_input.clear()
        self._tab.refresh()
        if reason:
            self._tab.show_problem(reason)

    def listen(self) -> None:
        if self._hardware is None:
            return
        reason = self._hardware.listen(self.listen_input.text())
        if reason:
            self._tab.show_problem(reason)
        else:
            self.listen_input.clear()
        self.refresh()

    def unlisten(self) -> None:
        if self._hardware is not None and self.listening_box.currentText():
            self._hardware.unlisten(self.listening_box.currentText())
        self.refresh()

    def add_received(self, message: ReceivedMessage) -> None:
        old = "  (ข้อความเก่าที่ broker เก็บไว้)" if message.retained else ""
        self.received_box.appendPlainText(
            f"{_clock(message.received_at)}  {message.topic}  ←  {message.text}{old}"
        )

    # --- display ---

    def refresh(self) -> None:
        hardware = self._hardware
        if hardware is None:
            for widget in (self.text_input, self.send_button,
                           self.listen_input, self.listen_button, self.listening_box, self.unlisten_button):
                widget.setEnabled(False)
            return
        text = self.text_input.text()
        reason = hardware.text_refusal(text)
        self.send_button.setEnabled(not reason)
        self.send_button.setToolTip(reason)
        if reason and not (text.strip() == "" and reason.startswith("พิมพ์")):
            self.sample_label.setText(f"ยังส่งไม่ได้: {reason}")
            set_style_property(self.sample_label, "level", "error")
        else:
            self.sample_label.setText(f"จะส่ง \"{text.strip() or '...'}\" → {hardware.topic}")
            set_style_property(self.sample_label, "level", "")
        current = self.listening_box.currentText()
        self.listening_box.blockSignals(True)
        self.listening_box.clear()
        self.listening_box.addItems(list(hardware.listening))
        if current in hardware.listening:
            self.listening_box.setCurrentText(current)
        self.listening_box.blockSignals(False)
        self.unlisten_button.setEnabled(bool(hardware.listening))
        config = hardware.config
        always = [topic for topic in (config.status_topic, config.reply_topic) if topic]
        self.listening_label.setText(
            "กำลังฟัง: " + ", ".join([*always, *hardware.listening] or ["-"])
            + ("  (status/ตอบกลับ ฟังเสมอ)" if always else "")
        )


class HardwareTab(QWidget):
    def __init__(self, hardware: HardwareController | None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.hardware = hardware
        self._history: list[SentCommand] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 8, 0)
        layout.setSpacing(12)

        link, link_layout = make_section(
            "การเชื่อมต่อ",
            "ส่งข้อความธรรมดาไปยังหุ่นยนต์จริงผ่าน MQTT ผู้ใช้พูดหรือพิมพ์สั่งได้ เช่น \"หันซ้าย 70 องศา\" "
            "\"เดินหน้า 20 เซน\" (ไม่บอกระยะ หัน 50 องศา เดิน 30 เซนติเมตร) และ \"กลับมาที่เดิม\" "
            "ซึ่งจะส่งคำสั่งย้อนกลับตามที่เคยสั่งไป (เมื่อเปิดสิทธิ์ด้านล่าง)",
        )
        self.broker_tag = make_label("", "tag")
        self.robot_tag = make_label("", "tag")
        tags = QHBoxLayout()
        tags.addWidget(self.broker_tag)
        tags.addWidget(self.robot_tag)
        tags.addStretch(1)
        link_layout.addLayout(tags)
        self.link_info = make_label("", "hint", word_wrap=True)
        link_layout.addWidget(self.link_info)
        layout.addWidget(link)

        # One robot, one topic: everything below and the visitor screen's buttons use it.
        topic, topic_layout = make_section(
            "Topic ของหุ่นยนต์",
            "ใช้กับทุกคำสั่งในหน้านี้ (หันตัว เดินหน้า/ถอยหลัง พิมพ์ส่งข้อความ) "
            "และปุ่ม ◀ ▲ ▼ ▶ บนหน้าจอผู้ใช้ เลือกจากรายการ หรือพิมพ์ topic ใหม่แล้วกด ใช้",
        )
        topic_row = QHBoxLayout()
        self.topic_box = QComboBox()
        self.topic_box.setEditable(True)
        self.topic_box.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)  # added through select_topic only
        self.topic_box.lineEdit().setPlaceholderText("เลือก หรือพิมพ์ topic ใหม่")
        self.topic_box.activated.connect(lambda _index: self.apply_topic())
        self.topic_box.lineEdit().returnPressed.connect(self.apply_topic)
        topic_row.addWidget(self.topic_box, 1)
        self.apply_topic_button = make_button("ใช้", "secondary", self.apply_topic)
        self.remove_topic_button = make_button("ลบ", "secondary", self.remove_topic)
        topic_row.addWidget(self.apply_topic_button)
        topic_row.addWidget(self.remove_topic_button)
        topic_layout.addLayout(topic_row)
        self.topic_info = make_label("", "hint", word_wrap=True)
        topic_layout.addWidget(self.topic_info)
        layout.addWidget(topic)

        safety, safety_layout = make_section(
            "ความปลอดภัย",
            "หุ่นยนต์อาจขยับทันทีที่กด ปุ่มหยุดในโปรแกรมเป็นคำสั่งซอฟต์แวร์ ไม่ทดแทนสวิตช์หรือ E-stop ที่ตัวหุ่นยนต์",
        )
        self.confirm_box = wrapped_check(safety_layout, "ยืนยันว่าบริเวณรอบหุ่นยนต์ไม่มีคน และมีคนพร้อมกดหยุดที่ตัวหุ่นยนต์")
        self.confirm_box.toggled.connect(self._on_confirm)
        self.stop_button = make_button("■  หยุด (STOP)", "danger", self.send_stop)
        safety_layout.addWidget(self.stop_button)
        self.home_here_button = make_button("ตั้งตำแหน่งตอนนี้เป็นจุดเริ่มต้น", "secondary", self._forget_position)
        safety_layout.addWidget(self.home_here_button)
        self.home_here_label = make_label("", "hint", word_wrap=True)
        safety_layout.addWidget(self.home_here_label)
        # Stays as set (unlike the confirmation above): the visitor screen is used without an Admin.
        self.visitor_box = wrapped_check(
            safety_layout,
            "เปิดให้ผู้ใช้สั่งหุ่นยนต์ด้วยเสียงหรือการพิมพ์ (หัน เดิน ถอยหลัง กลับมาที่เดิม) "
            "ใช้ topic เดียวกับหน้านี้ ปุ่มหยุดใช้ได้เสมอ",
        )
        self.visitor_box.setChecked(bool(hardware and hardware.visitor_enabled))
        self.visitor_box.toggled.connect(self._on_visitor)
        self.reason_label = make_label("", "hint", word_wrap=True)
        safety_layout.addWidget(self.reason_label)
        layout.addWidget(safety)

        cards = QHBoxLayout()
        cards.setSpacing(12)
        self.turn_card = _ControlCard(
            self, GROUP_TURN, "หันตัว", "หันอยู่กับที่ตามมุมที่เลือก", TURNS, TURN_PRESETS, 30,
        )
        self.move_card = _ControlCard(
            self, GROUP_MOVE, "เดินหน้า / ถอยหลัง", "เคลื่อนที่ตามระยะที่เลือก แล้วหยุดเอง", MOVES, MOVE_PRESETS, 25,
        )
        cards.addWidget(self.turn_card, 1)
        cards.addWidget(self.move_card, 1)
        layout.addLayout(cards)
        # The four buttons by direction, for anyone who wants them all.
        self.direction_buttons = {**self.turn_card.buttons, **self.move_card.buttons}
        self.message_card = _MessageCard(self)
        layout.addWidget(self.message_card)

        result, result_layout = make_section("ผลล่าสุด")
        self.sent_label = make_label("ยังไม่ได้ส่งคำสั่ง", "message", word_wrap=True)
        self.broker_label = make_label("", "hint", word_wrap=True)
        self.hardware_label = make_label("", "hint", word_wrap=True)
        for label in (self.sent_label, self.broker_label, self.hardware_label):
            result_layout.addWidget(label)
        result_layout.addWidget(make_label(f"ประวัติ {HISTORY_SIZE} คำสั่งล่าสุด", "eyebrow"))
        self.history_label = make_label("-", "hint", word_wrap=True)
        result_layout.addWidget(self.history_label)
        layout.addWidget(result)
        layout.addStretch(1)

        if hardware is not None:
            hardware.status_changed.connect(self.refresh)
            hardware.command_changed.connect(self._on_command)
            hardware.state_changed.connect(self.refresh)
            hardware.robot_status_received.connect(self.refresh)
            hardware.message_received.connect(self.message_card.add_received)
        self.fill_topics()
        self.refresh()

    # --- actions ---

    def send_stop(self) -> None:
        if self.hardware is None:
            return
        reason = self.hardware.send_stop()
        self.refresh()
        if reason:
            self.show_problem(reason)

    def apply_topic(self) -> bool:
        """Use the topic shown in the box (typed or picked); False if it was refused."""
        if self.hardware is None:
            return False
        reason = self.hardware.select_topic(self.topic_box.currentText())
        self.fill_topics()
        self.refresh()
        if reason:
            self.show_problem(reason)
        return not reason

    def apply_typed_topic(self) -> bool:
        """Before sending: a topic typed but not applied yet is applied first; False if refused."""
        if self.hardware is None:
            return False
        if self.topic_box.currentText().strip() == self.hardware.topic:
            return True
        return self.apply_topic()

    def remove_topic(self) -> None:
        if self.hardware is None:
            return
        reason = self.hardware.remove_topic(self.topic_box.currentText().strip())
        self.fill_topics()
        self.refresh()
        if reason:
            self.show_problem(reason)

    def reset(self) -> None:
        """The Admin left: the safety confirmation is asked again next time."""
        self.confirm_box.setChecked(False)
        if self.hardware is not None:
            self.hardware.reset()

    def _forget_position(self) -> None:
        """The robot is where it should start from: "กลับมาที่เดิม" means this place from now on."""
        if self.hardware is not None:
            self.hardware.forget_position()
        self.refresh()

    def _on_visitor(self, checked: bool) -> None:
        if self.hardware is not None:
            self.hardware.set_visitor_enabled(checked)
        self.refresh()

    def _on_confirm(self, checked: bool) -> None:
        if self.hardware is not None:
            self.hardware.confirm_area(checked)
        self.refresh()

    # --- display ---

    def fill_topics(self) -> None:
        hardware = self.hardware
        self.topic_box.blockSignals(True)
        self.topic_box.clear()
        if hardware is not None:
            self.topic_box.addItems(list(hardware.topics))
            self.topic_box.setCurrentText(hardware.topic)
            custom = not hardware.is_preset(hardware.topic)
            self.remove_topic_button.setEnabled(custom)
            self.remove_topic_button.setToolTip(
                "ลบ topic ที่พิมพ์เพิ่มไว้" if custom else "topic จาก .env ลบไม่ได้"
            )
        self.topic_box.blockSignals(False)

    def refresh(self, *_args) -> None:
        if not hasattr(self, "message_card"):
            return  # still being built
        hardware = self.hardware
        reasons = [card.refresh() for card in (self.turn_card, self.move_card)]
        self.message_card.refresh()
        if hardware is None:
            self._tag("Broker: ยังไม่ได้ตั้งค่า", "")
            self.robot_tag.setVisible(False)
            self.link_info.setText(SETUP_HINT)
            for widget in (self.stop_button, self.home_here_button, self.confirm_box, self.visitor_box, self.topic_box,
                           self.apply_topic_button, self.remove_topic_button):
                widget.setEnabled(False)
            self.topic_info.setText("")
            self.reason_label.setText("")
            return
        config = hardware.config
        self._tag(*_BROKER[hardware.status.status])
        status = hardware.robot_status
        if status is None:
            robot, level = f"หุ่นยนต์: ยังไม่มีข้อความบน {config.status_topic or '-'}", "wait"
        elif status.retained:
            robot, level = f"หุ่นยนต์: \"{status.text}\" (ข้อความเก่าที่ broker เก็บไว้)", "wait"
        else:
            robot, level = f"หุ่นยนต์: \"{status.text}\" {_clock(status.received_at)}", "safe"
        self.robot_tag.setText(robot)
        set_style_property(self.robot_tag, "level", level)
        reply = config.reply_topic or "ไม่ได้ตั้ง (ยืนยันว่าฮาร์ดแวร์ได้รับไม่ได้)"
        stop = f'"{config.stop_payload}"' if config.stop_payload else "ยังไม่ได้ตั้ง"
        camera = "ตรวจระยะคนด้วยกล้องก่อนส่ง" if hardware.uses_camera else "ไม่ได้ตรวจระยะคนด้วยกล้อง"
        stop_topics = " และ ".join(dict.fromkeys((hardware.topic, config.direction_topic)))
        self.topic_info.setText(f"ใช้อยู่: {hardware.topic} · คำสั่งหยุด {stop} ส่งไป {stop_topics}")
        self.link_info.setText(
            f"{config.host}:{config.port} · topic ตอบกลับ: {reply} · {camera}"
            + (f"\n{hardware.status.detail}" if hardware.status.status is not MqttStatus.CONNECTED else "")
        )
        self.confirm_box.setEnabled(True)
        moves = hardware.journal
        self.home_here_label.setText(
            f"จุดเริ่มต้นตอนนี้: หุ่นยนต์ถูกสั่งไป {len(moves)} ครั้งนับจากจุดเริ่มต้น "
            "(\"กลับมาที่เดิม\" จะย้อนกลับตามนี้ เวลาที่ใช้ต่อท่อนเป็นค่าประมาณ)" if moves
            else "จุดเริ่มต้นตอนนี้: ยังไม่ได้สั่งให้ขยับ (ถ้าเลื่อนหุ่นยนต์ด้วยมือ ให้กดปุ่มด้านบนเพื่อตั้งใหม่)"
        )
        if self.visitor_box.isChecked() != hardware.visitor_enabled:
            self.visitor_box.blockSignals(True)
            self.visitor_box.setChecked(hardware.visitor_enabled)
            self.visitor_box.blockSignals(False)
        stop_reason = hardware.stop_refusal()
        self.stop_button.setEnabled(not stop_reason)
        self.stop_button.setToolTip(stop_reason)
        # One reason is enough: the same one usually holds both cards back (no broker, not confirmed).
        reason = next((r for r in reasons if r), "")
        self.reason_label.setText(f"สั่งงานไม่ได้ตอนนี้: {reason}" if reason else "")
        self._show_last(hardware.last_command)

    def _on_command(self, record: SentCommand) -> None:
        if self._history and self._history[0].sent_at == record.sent_at and self._history[0].payload == record.payload:
            self._history[0] = record
        else:
            self._history.insert(0, record)
            del self._history[HISTORY_SIZE:]
        self.refresh()

    def _show_last(self, record: SentCommand | None) -> None:
        if record is None:
            return
        what = {KIND_STOP: "หยุด", KIND_TEXT: "ข้อความ", KIND_ARM: "คำสั่งแขน"}.get(record.kind, "คำสั่ง")
        self.sent_label.setText(f"{_clock(record.sent_at)} ส่ง{what} \"{record.payload}\" → {record.topic}")
        set_style_property(self.sent_label, "level", "info")
        self.broker_label.setText(
            f"Broker: รับแล้ว {_clock(record.broker_ack_at)}" if record.acked else "Broker: รอยืนยัน..."
        )
        config = self.hardware.config
        if record.reply:
            text = f"ฮาร์ดแวร์ยืนยัน {_clock(record.reply_at)}: {record.reply}"
        elif not config.reply_topic:
            text = "ฮาร์ดแวร์: ไม่รู้ว่าได้รับหรือไม่ (ยังไม่ได้ตั้ง MQTT_HW_REPLY_TOPIC) ดูที่ตัวหุ่นยนต์"
        elif (datetime.now(timezone.utc) - record.sent_at).total_seconds() < config.reply_window_s:
            text = "ฮาร์ดแวร์: รอคำตอบ..."
        else:
            text = f"ฮาร์ดแวร์: ไม่มีคำตอบภายใน {config.reply_window_s:g} วินาที"
        self.hardware_label.setText(text)
        self.history_label.setText("\n".join(
            f"{_clock(item.sent_at)}  {item.payload} → {item.topic}  · broker {'รับแล้ว' if item.acked else 'ยังไม่ยืนยัน'}"
            f"  · {'ตอบ: ' + item.reply if item.reply else 'ไม่มีคำตอบ'}"
            for item in self._history
        ) or "-")

    def show_problem(self, text: str) -> None:
        self.sent_label.setText(f"ไม่ได้ส่ง: {text}")
        set_style_property(self.sent_label, "level", "error")

    def _tag(self, text: str, level: str) -> None:
        self.broker_tag.setText(text)
        set_style_property(self.broker_tag, "level", level)

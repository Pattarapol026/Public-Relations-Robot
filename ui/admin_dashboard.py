"""Admin landing page: is every part working, how busy was today, what do people ask, what went wrong."""
from __future__ import annotations

import time
from datetime import datetime
from typing import Callable

from PySide6.QtCore import Qt, QThreadPool, QTimer, Slot
from PySide6.QtWidgets import (
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from app import __version__
from app.hardware_controller import HardwareController
from app.mqtt_controller import MqttController
from app.speaker_controller import SpeakerController
from app.robot_controller import RobotController
from app.speech_controller import SpeechController, SpeechStatus
from app.voice_controller import VoiceController, VoiceStatus
from app.vision_controller import VisionController
from app.workers import FunctionWorker
from config.settings import Settings
from robot.command_gateway import CommandStatus
from robot.interfaces import RobotState, RobotStatus
from services.analytics_service import AnalyticsService
from services.conversation_service import ConversationService
from services.mqtt_service import MqttStatus
from ui.mqtt_panel import MqttControlPanel
from ui.log_bridge import CATEGORY_LABELS, LogProblem, QtLogHandler
from ui.widgets import make_button, make_label, make_section, robot_state_text, set_style_property, ThaiLabel
from vision.interfaces import SourceStatus

REFRESH_MS = 30_000
TOP_QUESTION_DAYS = 7
TOP_QUESTION_COUNT = 5
PROBLEMS_SHOWN = 6

# Colour of each state on the cards (see theme.qss: safe / wait / danger / info).
ROBOT_LEVEL = {
    RobotState.DISCONNECTED: "danger",
    RobotState.IDLE: "safe",
    RobotState.EXECUTING: "wait",
    RobotState.ERROR: "danger",
}
_SOURCE = {
    SourceStatus.RUNNING: ("ทำงานอยู่", "safe"),
    SourceStatus.STARTING: ("กำลังเชื่อมต่อ", "wait"),
    SourceStatus.DISABLED: ("ปิดใช้งาน", "info"),
    SourceStatus.UNAVAILABLE: ("ใช้งานไม่ได้", "danger"),
}
_MODEL = {
    "ready": ("พร้อม", "safe"),
    "loading": ("กำลังโหลดโมเดล", "wait"),
    "disabled": ("ปิดใช้งาน", "info"),
    "unavailable": ("ใช้งานไม่ได้", "danger"),
}
_AI_ERROR_TEXT = {
    "auth": "API key ไม่ถูกต้องหรือไม่มีสิทธิ์ใช้โมเดลนี้",
    "quota": "ใช้งานเกินโควตาหรือถี่เกินไป",
    "network": "เชื่อมต่ออินเทอร์เน็ตไม่ได้",
    "timeout": "AI ตอบช้าเกินเวลาที่กำหนด",
    "server": "ระบบของ Google ขัดข้องชั่วคราว",
    "request": "คำขอถูกปฏิเสธ (ตรวจชื่อโมเดลใน GEMINI_MODEL)",
    "empty": "AI ไม่ส่งข้อความกลับมา",
    "unavailable": "ยังไม่ได้ติดตั้ง google-genai",
}
_MQTT = {
    MqttStatus.CONNECTED: ("เชื่อมต่อ broker แล้ว", "safe"),
    MqttStatus.CONNECTING: ("กำลังเชื่อมต่อ", "wait"),
    MqttStatus.DISCONNECTED: ("ไม่ได้เชื่อมต่อ", "info"),
    MqttStatus.ERROR: ("เชื่อมต่อไม่ได้", "danger"),
}
# The real robot on the overview: its MQTT link, in plain words.
_ROBOT_LINK = {
    MqttStatus.CONNECTED: ("เชื่อมต่อแล้ว", "safe"),
    MqttStatus.CONNECTING: ("กำลังเชื่อมต่อ", "wait"),
    MqttStatus.DISCONNECTED: ("ไม่ได้เชื่อมต่อ", "danger"),
    MqttStatus.ERROR: ("ไม่ได้เชื่อมต่อ", "danger"),
}
_PROBLEM_LEVEL = {"WARNING": "wait", "ERROR": "danger", "CRITICAL": "danger"}
# What a finished command's status means, in the Admin's words.
COMMAND_STATUS_TEXT = {
    CommandStatus.SUCCEEDED: "สำเร็จ",
    CommandStatus.REJECTED: "ถูกปฏิเสธ",
    CommandStatus.FAILED: "ล้มเหลว",
    CommandStatus.TIMEOUT: "หมดเวลา",
    CommandStatus.CANCELLED: "ยกเลิก",
}
_COMMAND_LEVEL = {
    CommandStatus.SUCCEEDED: "safe",
    CommandStatus.REJECTED: "wait",
    CommandStatus.FAILED: "danger",
    CommandStatus.TIMEOUT: "danger",
    CommandStatus.CANCELLED: "wait",
}


class _Card(QFrame):
    """A caption, one coloured value and a small note — like .metric in the design reference."""

    def __init__(self, title: str) -> None:
        super().__init__()
        self.setProperty("role", "metric")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 12)
        layout.setSpacing(2)
        self.value = make_label("-", "metric-value", word_wrap=True)
        self.note = make_label("", "card-note", word_wrap=True)
        layout.addWidget(make_label(title, "card-title"))
        layout.addWidget(self.value)
        layout.addWidget(self.note)
        layout.addStretch(1)

    def set(self, value: str, level: str = "", note: str = "") -> None:
        self.value.setText(value)
        set_style_property(self.value, "level", level)
        set_style_property(self, "level", level)  # the card's coloured edge
        self.note.setText(note)


class DashboardTab(QWidget):
    def __init__(
        self,
        settings: Settings,
        robot: RobotController,
        vision: VisionController,
        voice: VoiceController,
        speech: SpeechController,
        conversation_service: ConversationService,
        analytics: AnalyticsService,
        log_handler: QtLogHandler,
        mic_name: Callable[[], str | None],
        speaker_name: Callable[[], str | None],
        knowledge_count: Callable[[], tuple[int, int]] | None = None,
        mqtt: MqttController | None = None,
        speaker: SpeakerController | None = None,
        hardware: HardwareController | None = None,
    ) -> None:
        super().__init__()
        self._mqtt = mqtt
        self._hardware = hardware
        self._speaker = speaker
        self._settings = settings
        self._robot = robot
        self._knowledge_count = knowledge_count  # () -> (enabled, total)
        self._vision = vision
        self._voice = voice
        self._speech = speech
        self._conversation_service = conversation_service
        self._analytics = analytics
        self._log_handler = log_handler
        self._mic_name = mic_name
        self._speaker_name = speaker_name

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 8, 0)
        layout.setSpacing(10)

        # What a person running the robot needs at a glance, nothing it does not have:
        # the real robot (MQTT) replaces the simulator's cards once it is set up, and the
        # test link and voice direction show only when they are switched on / supported.
        real_robot = hardware is not None
        layout.addWidget(make_label("สถานะตอนนี้", "section-title"))
        devices = QGridLayout()
        devices.setSpacing(12)
        self._robot_card = _Card("หุ่นยนต์")
        self._camera_card = _Card("กล้อง")
        self._mic_card = _Card("ไมโครโฟน / ถอดเสียง")
        self._speaker_card = _Card("ลำโพง / เสียงพูด")
        self._ai_card = _Card("AI ตอบคำถาม")
        self._knowledge_card = _Card("ฐานความรู้ (หมวดที่ใช้)")
        self._knowledge_card.setVisible(knowledge_count is not None)
        for index, card in enumerate(
            (self._robot_card, self._camera_card, self._mic_card,
             self._speaker_card, self._ai_card, self._knowledge_card)
        ):
            devices.addWidget(card, index // 3, index % 3)
        for column in range(3):
            devices.setColumnStretch(column, 1)
        layout.addLayout(devices)

        # Kept for the simulator and the test link; on this robot most stay hidden.
        self._detection_card = _Card("การตรวจจับคน")
        self._gateway_card = _Card("ระบบสั่งงาน (Command Gateway)")
        self._safety_card = _Card("กฎระยะปลอดภัย")
        self._mqtt_card = _Card("MQTT Broker (ทดสอบการสื่อสาร)")
        self._direction_card = _Card("ทิศทางเสียง (DOA)")
        self._match_card = _Card("จับคู่ผู้พูด")
        self._hardware_card = _Card("ฮาร์ดแวร์ (MQTT)")
        extras = (
            (self._gateway_card, not real_robot),
            (self._safety_card, not real_robot),
            (self._mqtt_card, mqtt is not None),
            (self._direction_card, speaker is not None and speaker.available),
            (self._match_card, speaker is not None and speaker.available),
            (self._hardware_card, False),  # its state is the robot card's on a real robot
        )
        shown = [card for card, wanted in extras if wanted]
        if shown:
            layout.addSpacing(6)
            layout.addWidget(make_label("ระบบทดสอบ", "section-title"))
            more = QGridLayout()
            more.setSpacing(12)
            for index, card in enumerate(shown):
                more.addWidget(card, index // 3, index % 3)
            for column in range(3):
                more.setColumnStretch(column, 1)
            layout.addLayout(more)

        layout.addSpacing(6)
        layout.addWidget(make_label("วันนี้", "section-title"))
        today = QGridLayout()
        today.setSpacing(12)
        self._visits_card = _Card("ผู้มาใช้บริการ")
        self._questions_card = _Card("คำถาม")
        self._answered_card = _Card("ตอบได้")
        self._satisfaction_card = _Card("พอใจคำตอบ")
        for column, card in enumerate(
            (self._visits_card, self._questions_card, self._answered_card, self._satisfaction_card)
        ):
            today.addWidget(card, 0, column)
            today.setColumnStretch(column, 1)
        layout.addLayout(today)

        layout.addSpacing(6)
        lists = QHBoxLayout()
        lists.setSpacing(12)
        top, top_layout = make_section(
            "คำถามยอดนิยม", f"{TOP_QUESTION_DAYS} วันล่าสุด ใช้ดูว่าควรเพิ่มอะไรในฐานความรู้"
        )
        self._top_list = QVBoxLayout()
        self._top_list.setSpacing(8)
        top_layout.addLayout(self._top_list)
        top_layout.addStretch(1)
        problems, problems_layout = make_section(
            "การแจ้งเตือนล่าสุด", "คำเตือนและข้อผิดพลาดตั้งแต่เปิดโปรแกรม ดูทั้งหมดได้ในหน้า Log"
        )
        self._problem_list = QVBoxLayout()
        self._problem_list.setSpacing(8)
        problems_layout.addLayout(self._problem_list)
        problems_layout.addStretch(1)
        lists.addWidget(top, 1)
        lists.addWidget(problems, 1)
        layout.addLayout(lists)

        # The real robot is driven from its own page (อุปกรณ์ → ควบคุมหุ่นยนต์); the simulator
        # panel shows only when the local test link is on (or nothing is set up, to say how).
        self.mqtt_panel = MqttControlPanel(mqtt)
        self.mqtt_panel.setVisible(mqtt is not None or hardware is None)
        layout.addWidget(self.mqtt_panel)
        layout.addWidget(self._build_ai_section())
        layout.addWidget(self._build_info_section())
        layout.addStretch(1)

        self._timer = QTimer(self)
        self._timer.setInterval(REFRESH_MS)
        self._timer.timeout.connect(self.refresh)

        robot.status_changed.connect(self._show_robot)
        robot.status_changed.connect(self._show_subsystems)
        robot.command_submitted.connect(self._show_subsystems)
        robot.command_finished.connect(self._show_subsystems)
        vision.changed.connect(self._show_subsystems)
        vision.changed.connect(self._show_devices)
        voice.status_changed.connect(self._show_devices)
        speech.status_changed.connect(self._show_devices)
        log_handler.emitter.problem.connect(self._show_problems)
        if mqtt is not None:
            mqtt.status_changed.connect(self._show_mqtt)
            mqtt.message_received.connect(self._show_mqtt)
        self._show_mqtt()
        if hardware is not None:
            hardware.status_changed.connect(self._show_hardware)
            hardware.command_changed.connect(self._show_hardware)
        self._show_hardware()
        if speaker is not None:
            speaker.changed.connect(self._show_speaker)
        vision.changed.connect(self._show_speaker)
        self._show_speaker()
        self._show_robot(robot.status)
        self.refresh()

    # --- refreshing (only while the page is on screen) ---

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.refresh()
        self._timer.start()

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self._timer.stop()

    def refresh(self) -> None:
        self._show_ai_mode()
        self._show_devices()
        self._show_subsystems()
        self._show_knowledge()
        self._show_today()
        self._show_top_questions()
        self._show_problems()

    # --- sections ---

    def _build_ai_section(self) -> QWidget:
        section, layout = make_section("ระบบ AI")
        row = QHBoxLayout()
        self._ai_text = make_label("", "", word_wrap=True)
        self._ai_test_button = make_button("ทดสอบการเชื่อมต่อ AI", "secondary", self._test_ai)
        row.addWidget(self._ai_text, 1)
        row.addWidget(self._ai_test_button)
        layout.addLayout(row)
        self._ai_result = make_label("", "message", word_wrap=True)
        layout.addWidget(self._ai_result)
        return section

    def _show_ai_mode(self) -> None:
        if self._conversation_service.uses_gemini:
            text = f"Gemini ({self._settings.gemini_model}) ตอบจากฐานความรู้ของเรา"
        elif not self._settings.ai_enabled:
            text = "ปิดอยู่ (AI_ENABLED=false) — ตอบจากฐานความรู้อย่างเดียว"
        else:
            text = "โหมดสาธิต — ยังไม่ได้ตั้งค่า API key (ตั้งได้ที่หน้ารหัสผ่านและ API key) ตอบจากฐานความรู้อย่างเดียว"
        self._ai_text.setText(text)
        self._ai_test_button.setVisible(self._conversation_service.uses_gemini)

    def _build_info_section(self) -> QWidget:
        section, layout = make_section("ข้อมูลการติดตั้ง")
        form = QFormLayout()
        form.setVerticalSpacing(8)
        form.setHorizontalSpacing(18)
        for title, value in (
            ("เวอร์ชัน", __version__),
            *((("Robot adapter", self._settings.robot_adapter),) if self._hardware is None else ()),
            ("โฟลเดอร์ข้อมูล", str(self._settings.data_dir)),
            ("โฟลเดอร์สื่อ", str(self._settings.media_dir)),
            ("โฟลเดอร์ log", str(self._settings.log_dir)),
        ):
            value_label = ThaiLabel(value)
            value_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            form.addRow(make_label(title, "field-label"), value_label)
        layout.addLayout(form)
        return section

    # --- showing ---

    def _show_robot(self, status: RobotStatus) -> None:
        if self._hardware is not None:
            self._show_hardware()
            return
        state = status.state if status.connected else RobotState.DISCONNECTED
        notes = []
        if status.battery_percent is not None:
            notes.append(f"แบตเตอรี่ {status.battery_percent:.0f}%")
        level = ROBOT_LEVEL[state]
        if self._robot.simulated:
            # The simulator is always "ready": say plainly that nothing physical is connected.
            notes.insert(0, "หุ่นยนต์จำลอง ยังไม่มีอุปกรณ์จริง")
            if level == "safe":
                level = "wait"
        self._robot_card.set(robot_state_text(status), level, " · ".join(notes))

    def _show_subsystems(self, *_args) -> None:
        vision = self._vision
        settings = self._settings

        # Person detection: separate from "the camera process runs".
        if vision.status is not SourceStatus.RUNNING:
            self._detection_card.set("ไม่ทำงาน", _SOURCE[vision.status][1], "ต้องให้กล้องทำงานก่อน")
        elif not vision.fresh:
            self._detection_card.set("ไม่ได้รับผล", "danger", "กล้องไม่ส่งผลตรวจจับมาเกิน 1.5 วินาที")
        else:
            seen = len(vision.people)
            notes = []
            if vision.nearest_m is not None:
                notes.append(f"ใกล้สุด {vision.nearest_m:.1f} ม.")
            if vision.unmeasured_count:
                notes.append(f"วัดระยะไม่ได้ {vision.unmeasured_count} คน")
            if vision.engaged:
                notes.append("มีผู้ใช้อยู่ในระยะสนทนา")
            elif vision.present:
                notes.append("มีผู้ใช้อยู่ในบริเวณให้บริการ")
            self._detection_card.set(
                f"เห็น {seen} คน" if seen else "ไม่เห็นใคร", "safe", " · ".join(notes)
            )

        # Command gateway: accepted/running vs. the last result.
        robot = self._robot
        pending = robot.pending
        last = robot.last_command
        last_note = ""
        if last is not None:
            request, result = last
            last_note = (
                f"ล่าสุด: {request.target} → {COMMAND_STATUS_TEXT[result.status]} ({result.code})"
            )
        status = robot.status
        if pending:
            request = pending[0]
            self._gateway_card.set("กำลังทำงาน", "wait", f"{request.type} → {request.target} ({request.source})")
        elif not status.connected or status.state is RobotState.DISCONNECTED:
            self._gateway_card.set("หุ่นยนต์ไม่เชื่อมต่อ", "danger", last_note or "คำสั่งจะถูกปฏิเสธ")
        elif status.state is RobotState.ERROR:
            self._gateway_card.set("หุ่นยนต์ขัดข้อง", "danger", last_note or "คำสั่งจะถูกปฏิเสธ")
        else:
            level = _COMMAND_LEVEL[last[1].status] if last is not None else "safe"
            self._gateway_card.set("รับคำสั่งได้", level, last_note or "ยังไม่มีคำสั่งตั้งแต่เปิดโปรแกรม")

        # Safety rule: what it would decide right now.
        rule = f"หันตัว > {settings.base_clearance_m:.1f} ม."
        if vision.status is SourceStatus.DISABLED:
            self._safety_card.set("ไม่มีการตรวจ", "danger", "ปิดกล้องอยู่ จึงไม่ตรวจระยะคนก่อนขยับ")
        elif not vision.fresh:
            self._safety_card.set("ปฏิเสธการขยับ", "danger", "กล้องไม่พร้อม: ไม่รู้ = ไม่ปลอดภัย")
        elif vision.unmeasured_nearby and vision.nearest_m is None:
            self._safety_card.set("ปฏิเสธการขยับ", "wait", "เห็นคนแต่วัดระยะไม่ได้")
        elif vision.nearest_m is not None and vision.nearest_m < settings.base_clearance_m:
            self._safety_card.set("มีคนใกล้เกินไป", "wait", f"ใกล้สุด {vision.nearest_m:.1f} ม. · {rule}")
        else:
            self._safety_card.set("พร้อมตรวจ", "safe", rule)

    def _show_knowledge(self) -> None:
        """Read from the database: on refresh only, not on every camera update."""
        if self._knowledge_count is not None:
            enabled, total = self._knowledge_count()
            self._knowledge_card.set(
                f"{enabled} รายการ", "safe" if enabled else "wait",
                f"เปิดใช้ {enabled} จากทั้งหมด {total}" if total else "ยังไม่มีคำถาม-คำตอบ",
            )

    def _show_speaker(self, *_args) -> None:
        speaker = self._speaker
        if speaker is None or not speaker.available:
            reason = speaker.source_text if speaker is not None else "ไม่ได้เปิดใช้"
            self._direction_card.set("ไม่มีอุปกรณ์รองรับ", "info", reason)
            self._match_card.set(
                "ยังจับคู่ไม่ได้", "info",
                f"ต้องมีค่าทิศทางเสียงก่อน · กล้องเห็น {self._vision.people_count} คน",
            )
            return
        estimate = speaker.estimate
        board = speaker.board
        if board is not None and not board.snapshot().connected:
            self._direction_card.set("ไม่พบบอร์ด", "wait", speaker.source_text)
        elif estimate is None and board is not None and not board.settings.front_known:
            self._direction_card.set(
                "ยังไม่ปรับเทียบ", "wait",
                "ไปที่เมนู อุปกรณ์ › เสียง › ทิศทางเสียง เพื่อวัดทิศหน้าของไมค์ (ระบบยังฟังแบบเดิม)",
            )
        elif estimate is None:
            self._direction_card.set("รอเสียงพูด", "wait", speaker.source_text)
        else:
            side = "ด้านหน้า" if abs(estimate.azimuth_deg) <= 10 else (
                "ทางขวา" if estimate.azimuth_deg > 0 else "ทางซ้าย"
            )
            heard = time.monotonic() - estimate.timestamp
            self._direction_card.set(
                f"{estimate.azimuth_deg:+.0f}° ({side})",
                "safe" if estimate.stable else "wait",
                f"ความมั่นใจ {estimate.confidence:.0%} · กระจาย ±{estimate.spread_deg:.0f}° · "
                f"{estimate.readings} ค่า · {heard:.0f} วินาทีก่อน",
            )
        result = speaker.match()
        level = {"matched": "safe", "no_person": "wait"}.get(result.status.value, "info")
        if result.person is not None:
            person = result.person
            y = f"{person.y_m:+.2f}" if person.y_m is not None else "?"
            where = (
                f"X {person.x_m:+.2f} · Y {y} · Z {person.z_m:.2f} ม. (พิกัดกล้อง) · "
                f"คลาดจากทิศเสียง {result.error_deg:.0f}°"
            )
            self._match_card.set(result.reason, level, f"{where} · กล้องเห็น {result.people_seen} คน")
        else:
            self._match_card.set("ยังระบุผู้พูดไม่ได้", level, f"{result.reason} · กล้องเห็น {result.people_seen} คน")

    def _show_mqtt(self, *_args) -> None:
        if self._mqtt is None:
            self._mqtt_card.set("ปิดใช้งาน", "info", "เปิดได้ด้วย MQTT_ENABLED=true ในไฟล์ .env")
            return
        snapshot = self._mqtt.status
        text, level = _MQTT[snapshot.status]
        since = snapshot.since.astimezone().strftime("%H:%M:%S")
        notes = [f"{snapshot.detail} · ตั้งแต่ {since}" if snapshot.detail else f"ตั้งแต่ {since}"]
        device = self._mqtt.last_device_message
        if device is None:
            notes.append("ยังไม่มีข้อความจากอุปกรณ์")
        else:
            heard = datetime.fromisoformat(device.ts).astimezone().strftime("%H:%M:%S")
            kind = "อุปกรณ์จำลอง" if device.payload.get("simulated") else "อุปกรณ์"
            notes.append(f"ล่าสุดจาก{kind} {device.source} ({device.type}) {heard}")
        notes.append("ไม่ได้หมายความว่าหุ่นยนต์เชื่อมต่อแล้ว")
        self._mqtt_card.set(text, level, " · ".join(notes))

    def _show_hardware(self, *_args) -> None:
        if self._hardware is None:
            self._hardware_card.set("ยังไม่ได้ตั้งค่า", "info", "ตั้ง MQTT_HW_HOST ในไฟล์ .env")
            return
        snapshot = self._hardware.status
        text, level = _MQTT[snapshot.status]
        config = self._hardware.config
        notes = [f"{config.host}:{config.port} · ตั้งแต่ {snapshot.since.astimezone():%H:%M:%S}"]
        last = self._hardware.last_command
        if last is not None:
            answer = f"ตอบ: {last.reply}" if last.reply else "ไม่มีคำตอบจากฮาร์ดแวร์"
            notes.append(f"ล่าสุด \"{last.payload}\" {last.sent_at.astimezone():%H:%M:%S} · {answer}")
        notes.append("เชื่อมต่อ broker ได้ ไม่ได้หมายความว่าฮาร์ดแวร์ได้รับคำสั่ง")
        self._hardware_card.set(text, level, " · ".join(notes))
        # On the robot card, in plain words: is the robot reachable, and what did it last get.
        robot_text, robot_level = _ROBOT_LINK[snapshot.status]
        robot_notes = [f"MQTT {config.host}:{config.port}"]
        if last is not None:
            robot_notes.append(f"สั่งล่าสุด \"{last.payload}\" เวลา {last.sent_at.astimezone():%H:%M}")
        self._robot_card.set(robot_text, robot_level, " · ".join(robot_notes))

    def _show_devices(self) -> None:
        text, level = _SOURCE[self._vision.status]
        if self._vision.status is SourceStatus.RUNNING:
            note = f"เห็น {self._vision.people_count} คนด้านหน้า"
        else:
            note = self._vision.detail
        self._camera_card.set(text, level, note)

        text, level = _MODEL[self._voice.status.value]
        if self._voice.status is VoiceStatus.UNAVAILABLE:
            note = self._voice.status_text
        else:
            # The microphone the check actually opened, else what is saved.
            note = self._voice.status_text.partition(" — ")[2] or self._mic_name() or "อัตโนมัติ"
        self._mic_card.set(text, level, note)

        text, level = _MODEL[self._speech.status.value]
        if self._speech.status is SpeechStatus.UNAVAILABLE:
            note = self._speech.status_text
        else:
            note = self._speaker_name() or "ค่าเริ่มต้นของระบบ"
        self._speaker_card.set(text, level, note)

    def _show_ai(self, failures: int) -> None:
        if self._conversation_service.uses_gemini:
            text, level = "Gemini", "safe"
            note = self._settings.gemini_model
        else:
            text, level = "ฐานความรู้", "info"
            note = "ไม่ได้ใช้ AI ภายนอก"
        if failures:
            level, note = "wait", f"AI ใช้ไม่ได้วันนี้ {failures} ครั้ง (ตอบจากฐานความรู้แทน)"
        self._ai_card.set(text, level, note)

    def _show_today(self) -> None:
        summary = self._analytics.summary(1)
        self._show_ai(summary.ai_failures)
        self._visits_card.set(
            str(summary.visits), "info",
            f"เข้ามาสนทนา {summary.engaged_visits} คน" if summary.visits else "กล้องนับเมื่อมีคนเข้ามาในบริเวณ",
        )
        self._questions_card.set(
            str(summary.questions), "info",
            f"ตอบเฉลี่ย {summary.average_response_ms / 1000:.1f} วินาที" if summary.questions else "",
        )
        rate = summary.answer_rate
        self._answered_card.set(
            "–" if rate is None else f"{rate * 100:.0f}%",
            "" if rate is None else "safe" if rate >= 0.8 else "wait" if rate >= 0.5 else "danger",
            f"{summary.answered} จาก {summary.questions} ข้อ" if summary.questions else "",
        )
        satisfaction = summary.satisfaction
        rated = summary.feedback_up + summary.feedback_down
        self._satisfaction_card.set(
            "–" if satisfaction is None else f"{satisfaction * 100:.0f}%",
            "" if satisfaction is None else "safe" if satisfaction >= 0.7 else "wait",
            f"จากผู้ให้คะแนน {rated} ครั้ง" if rated else "ยังไม่มีผู้ให้คะแนน",
        )

    def _show_top_questions(self) -> None:
        _clear(self._top_list)
        if not self._analytics.logs_questions:
            self._top_list.addWidget(
                make_label("ปิดการเก็บข้อความคำถามอยู่ (LOG_QUESTIONS=false)", "hint", word_wrap=True)
            )
            return
        top = self._analytics.top_questions(TOP_QUESTION_DAYS, TOP_QUESTION_COUNT)
        if not top:
            self._top_list.addWidget(make_label("ยังไม่มีคำถามในช่วงนี้", "hint"))
            return
        for rank, (question, count) in enumerate(top, start=1):
            row = QHBoxLayout()
            row.setSpacing(10)
            number = make_label(str(rank), "rank")
            number.setFixedWidth(28)
            number.setAlignment(Qt.AlignmentFlag.AlignCenter)
            row.addWidget(number)
            row.addWidget(make_label(question, "list-text", word_wrap=True), 1)
            row.addWidget(make_label(f"{count} ครั้ง", "count"))
            self._top_list.addLayout(row)

    @Slot()
    def _show_problems(self, _problem: LogProblem | None = None) -> None:
        _clear(self._problem_list)
        problems = self._log_handler.recent_problems()[:PROBLEMS_SHOWN]
        if not problems:
            self._problem_list.addWidget(make_label("ไม่มีคำเตือนหรือข้อผิดพลาด", "hint"))
            return
        for problem in problems:
            row = QHBoxLayout()
            row.setSpacing(10)
            dot = QLabel()
            dot.setFixedSize(10, 10)
            dot.setProperty("role", "dot")
            dot.setProperty("level", _PROBLEM_LEVEL.get(problem.level, "wait"))
            row.addWidget(dot, 0, Qt.AlignmentFlag.AlignVCenter)
            text = QVBoxLayout()
            text.setSpacing(0)
            heading = QHBoxLayout()
            heading.setSpacing(8)
            heading.addWidget(make_label(f"{problem.time:%H:%M:%S}  ·  {problem.source}", "hint"))
            tag = make_label(CATEGORY_LABELS.get(problem.category, problem.category), "category")
            tag.setProperty("category", problem.category)
            heading.addWidget(tag)
            heading.addStretch(1)
            text.addLayout(heading)
            text.addWidget(make_label(problem.message, "list-text", word_wrap=True))
            row.addLayout(text, 1)
            self._problem_list.addLayout(row)

    # --- AI test ---

    def _test_ai(self) -> None:
        """One real request to Gemini, off the GUI thread, to tell the Admin whether it works."""
        self._ai_result.setText("กำลังทดสอบ...")
        set_style_property(self._ai_result, "level", "info")
        worker = FunctionWorker(None, self._conversation_service.check_ai)
        worker.signals.finished.connect(self._on_ai_test_ok)
        worker.signals.failed.connect(self._on_ai_test_failed)
        QThreadPool.globalInstance().start(worker)

    @Slot(object, object)
    def _on_ai_test_ok(self, _token: object, reply: object) -> None:
        self._ai_result.setText(f"ใช้งานได้ — AI ตอบว่า: {reply}")
        set_style_property(self._ai_result, "level", "info")

    @Slot(object, str)
    def _on_ai_test_failed(self, _token: object, error: str) -> None:
        kind = error.split(":", 1)[0]
        self._ai_result.setText(
            f"ใช้งานไม่ได้ — {_AI_ERROR_TEXT.get(kind, 'เกิดข้อผิดพลาด ดูรายละเอียดใน Log')}"
        )
        set_style_property(self._ai_result, "level", "error")


def _clear(layout) -> None:
    """Remove and delete everything in a layout (rows are rebuilt on each refresh)."""
    while layout.count():
        item = layout.takeAt(0)
        if item.widget() is not None:
            item.widget().hide()  # deletion happens later; do not leave it drawn meanwhile
            item.widget().deleteLater()
        elif item.layout() is not None:
            _clear(item.layout())
            item.layout().deleteLater()

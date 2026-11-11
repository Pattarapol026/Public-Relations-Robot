"""Admin screen: content management, system status, robot test panel, logs, security."""
from __future__ import annotations

import html
from collections import deque
from pathlib import Path

from PySide6.QtCore import Qt, QTime, QTimer, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from admin.auth import AdminAuth
from app import __version__
from app.content_controller import ACTOR_ADMIN, ContentController
from app.hardware_controller import HardwareController
from app.mqtt_controller import MqttController
from app.speaker_controller import SpeakerController
from app.robot_controller import RobotController
from app.speech_controller import SpeechController
from app.ai_key import AiKeyController
from app.tuning import TuningController
from app.vision_controller import VisionController
from app.voice_controller import VoiceController
from config.settings import Settings
from robot.command_gateway import CommandRequest, CommandResult, CommandType
from robot.interfaces import RobotState, RobotStatus
from robot.mock_adapter import MockRobotAdapter
from services.analytics_service import AnalyticsService
from services.conversation_service import ConversationService
from services.mqtt_service import MqttStatus
from ui.admin_ai import AiTab
from ui.admin_dashboard import COMMAND_STATUS_TEXT, ROBOT_LEVEL, DashboardTab
from ui.admin_api_key import build_api_key_section
from ui.admin_backup import BackupTab
from ui.admin_hardware import HardwareTab
from ui.admin_history import HistoryTab
from ui.admin_persona import PersonaTab
from ui.admin_audio import AudioTab
from ui.admin_direction import DirectionPanel
from ui.admin_content import AnnouncementTab, DisplaySettingsTab, KnowledgeTab
from ui.admin_schedule import ScheduleTab
from ui.admin_stats import StatsTab
from ui.admin_triggers import TriggerTab
from ui.admin_vision import VisionTab
from ui.segmented_page import SegmentedPage
from ui.log_bridge import (
    CATEGORY_COMMAND,
    CATEGORY_CONNECTION,
    CATEGORY_ERROR,
    CATEGORY_INFO,
    CATEGORY_LABELS,
    CATEGORY_SAFETY,
    CATEGORY_WARNING,
    LogEntry,
    QtLogHandler,
)
from vision.interfaces import SourceStatus
from ui.widgets import make_button, make_label, make_section, robot_state_text, set_style_property

ADMIN_STYLE_PATH = Path(__file__).resolve().parent / "styles" / "admin.qss"


def load_admin_style_sheet() -> str:
    """The Admin theme, Rose Charcoal (the visitor screen keeps theme.qss)."""
    return ADMIN_STYLE_PATH.read_text(encoding="utf-8").replace(
        "url(@styles/", f"url({ADMIN_STYLE_PATH.parent.as_posix()}/"
    )


MAX_LOG_LINES = 1000
ACTIVITY_ROWS = 200
SIDEBAR_WIDTH = 236


class AdminScreen(QWidget):
    logout_requested = Signal()
    quit_requested = Signal()
    show_on_showcase = Signal(int)
    run_schedule_requested = Signal(object)  # Schedule

    def __init__(
        self,
        settings: Settings,
        robot: RobotController,
        auth: AdminAuth,
        content: ContentController,
        voice: VoiceController,
        vision: VisionController,
        speech: SpeechController,
        conversation_service: ConversationService,
        analytics: AnalyticsService,
        tuning: TuningController,
        ai_key: AiKeyController,
        log_handler: QtLogHandler,
        mock_adapter: MockRobotAdapter | None = None,
        parent: QWidget | None = None,
        mqtt: MqttController | None = None,
        speaker: SpeakerController | None = None,
        hardware: HardwareController | None = None,
    ) -> None:
        super().__init__(parent)
        self._conversation_service = conversation_service
        self._settings = settings
        self._robot = robot
        self._auth = auth
        self._content = content
        self._vision = vision
        self._mock = mock_adapter
        self._ai_key = ai_key

        self.setObjectName("adminScreen")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(load_admin_style_sheet())

        self._hardware = hardware
        self._display_tab = DisplaySettingsTab(content)
        self._audio_tab = AudioTab(settings, content, voice, speech, tuning)
        self._vision_tab = VisionTab(settings, vision, tuning)
        self._persona_tab = PersonaTab(content, speech)
        self._history_tab = HistoryTab(analytics, content)
        self._backup_tab = BackupTab(settings, content, tuning)
        self._backup_tab.config_imported.connect(self._show_saved_values)
        schedule_tab = ScheduleTab(content)
        schedule_tab.run_requested.connect(self.run_schedule_requested)
        self._stats_tab = StatsTab(analytics, content)
        self._dashboard = DashboardTab(
            settings, robot, vision, voice, speech, conversation_service, analytics, log_handler,
            mic_name=content.mic_device_name, speaker_name=content.audio_output_name,
            knowledge_count=lambda: (
                len(content.active_knowledge()), len(content.knowledge(venue=content.venue_id()))
            ),
            mqtt=mqtt,
            speaker=speaker,
            hardware=hardware,
        )
        self._hardware_tab = HardwareTab(hardware)

        # Sidebar groups, in the order they appear. The first page is the landing page.
        self._pages = QStackedWidget()
        self._page_info: list[tuple[str, str, str]] = []  # (group, title, description)
        self._nav = QButtonGroup(self)
        self._nav.setExclusive(True)
        # Pages that hold edits until "save": leaving them asks first.
        self._editable_pages: dict[int, QWidget] = {}
        self._segmented: dict[int, SegmentedPage] = {}  # pages made of sub-pages, by page index
        sidebar_items = QVBoxLayout()
        sidebar_items.setContentsMargins(10, 12, 10, 12)
        sidebar_items.setSpacing(2)

        def add_group(name: str) -> None:
            if self._page_info:
                sidebar_items.addSpacing(10)
            label = make_label(name, "eyebrow")
            label.setContentsMargins(10, 4, 0, 4)
            sidebar_items.addWidget(label)

        def add_page(group: str, title: str, description: str, page: QWidget) -> int:
            index = self._pages.addWidget(page)
            self._page_info.append((group, title, description))
            button = QPushButton(title)
            button.setProperty("role", "side-nav")
            button.setCheckable(True)
            self._nav.addButton(button, index)
            sidebar_items.addWidget(button)
            return index

        # Thai group names, and only the pages that do something on this robot: the
        # visitor screen has no home page any more (so no media playlist), and with the
        # real robot on MQTT the simulated robot's test page is left out.
        overview, data, devices, system = "ภาพรวม", "ข้อมูลที่หุ่นยนต์ใช้ตอบ", "อุปกรณ์", "ระบบ"

        def segmented() -> SegmentedPage:
            return SegmentedPage(self._ask_discard)

        add_group(overview)
        add_page(overview, "ภาพรวมระบบ",
                 "อุปกรณ์ทำงานครบไหม วันนี้มีคนถามอะไร และมีอะไรผิดปกติบ้าง",
                 _scrollable(self._dashboard))
        usage = segmented()
        usage.add("สถิติการใช้งาน", _scrollable(self._stats_tab),
                  "จำนวนผู้มาใช้บริการ คำถาม ความพึงพอใจ และคำถามที่ยังตอบไม่ได้",
                  on_show=self._stats_tab.refresh)
        usage.add("ประวัติบทสนทนา", _scrollable(self._history_tab),
                  "ค้นหาบทสนทนาย้อนหลัง ทำเครื่องหมายคำตอบที่ผิด และเพิ่มคำตอบในฐานความรู้")
        self._stats_index = add_page(overview, "สถิติและประวัติ", usage.current_description, usage)
        self._segmented[self._stats_index] = usage

        add_group(data)
        knowledge = segmented()
        knowledge.add("คำถาม-คำตอบ", KnowledgeTab(content),
                      "คำถาม-คำตอบที่หุ่นยนต์ใช้ตอบ แยกตามหมวด (สิ่งประดิษฐ์ / ศูนย์อนุรักษ์พันธุ์เต่า)")
        knowledge.add("ทดสอบการตอบ", _scrollable(AiTab(conversation_service)),
                      "พิมพ์คำถามเพื่อดูว่าหุ่นยนต์เลือกข้อมูลอะไรและตอบอย่างไร")
        self._segmented[add_page(data, "ฐานความรู้", knowledge.current_description, knowledge)] = knowledge
        news = segmented()
        news.add("ประกาศ", AnnouncementTab(content),
                 "ข่าวหรือประกาศของแต่ละหมวด ที่หุ่นยนต์ใช้ตอบเมื่อมีคนถาม")
        news.add("กำหนดการ", schedule_tab, "ให้หุ่นยนต์พูดประกาศเองตามวันและเวลาที่ตั้งไว้")
        self._segmented[add_page(data, "ประกาศและกำหนดการ", news.current_description, news)] = news
        persona = segmented()
        persona.add("บุคลิกและคำพูด", _scrollable(self._persona_tab),
                    "ชื่อหุ่นยนต์ บุคลิก ความยาวคำตอบ คำทักทาย คำลา และข้อความเมื่อตอบไม่ได้",
                    editable=self._persona_tab)
        persona.add("ความเป็นส่วนตัว", _scrollable(self._display_tab),
                    "ข้อความแจ้งความเป็นส่วนตัว (PDPA) ที่ด้านล่างของหน้าจอผู้ใช้",
                    editable=self._display_tab)
        persona_index = add_page(data, "บุคลิกและบทสนทนา", persona.current_description, persona)
        self._segmented[persona_index] = persona
        self._editable_pages[persona_index] = persona
        add_page(data, "การ์ด / ท่าทาง",
                 "ให้หุ่นยนต์ตอบเมื่อมีคนชูการ์ดหรือทำท่าทางมือหน้ากล้อง", TriggerTab(content))

        add_group(devices)
        self._robot_page = _scrollable(self._build_robot_tab())
        if hardware is None:  # no real robot set up: the simulator's test page, as before
            add_page(devices, "หุ่นยนต์จำลอง", "ทดสอบคำสั่งกับหุ่นยนต์จำลอง (ยังไม่มีหุ่นยนต์จริง)",
                     self._robot_page)
        add_page(devices, "ควบคุมหุ่นยนต์",
                 "หัน เดินหน้า ถอยหลัง หยุด และส่ง/รับข้อความกับหุ่นยนต์จริงผ่าน MQTT "
                 "หุ่นยนต์อาจขยับจริงทันทีที่กด",
                 _scrollable(self._hardware_tab))
        self._editable_pages[add_page(
            devices, "กล้อง", "สถานะกล้อง สิ่งที่เห็นอยู่ตอนนี้ และระยะที่หุ่นยนต์ทักทายและเริ่มฟัง",
            _scrollable(self._vision_tab),
        )] = self._vision_tab
        sound = segmented()
        sound.add("เสียงและการฟัง", _scrollable(self._audio_tab),
                  "ไมโครโฟน ลำโพง ความเร็วในการพูด และความไวในการฟัง", editable=self._audio_tab)
        board = speaker.board if speaker is not None else None
        if board is not None:  # a calibratable microphone array (ReSpeaker XVF3800)
            self._direction_panel = DirectionPanel(board)
            sound.add("ทิศทางเสียง (ไมค์อาเรย์)", _scrollable(self._direction_panel),
                      "ดูทิศที่ไมค์ได้ยินเสียง ปรับเทียบกับกล้อง และเลือกให้ไมค์ฟังเฉพาะทิศที่ผู้ใช้ยืน",
                      editable=self._direction_panel)
        sound_index = add_page(devices, "เสียง", sound.current_description, sound)
        self._segmented[sound_index] = sound
        self._editable_pages[sound_index] = sound

        add_group(system)
        settings_page = segmented()
        settings_page.add("สำรองและย้ายข้อมูล", _scrollable(self._backup_tab),
                          "สำรองฐานข้อมูล และส่งออก/นำเข้าการตั้งค่าเพื่อติดตั้งหุ่นยนต์ตัวอื่นให้เหมือนกัน")
        settings_page.add("รหัสผ่านและ API key", _scrollable(self._build_security_tab()),
                          "เปลี่ยนรหัสผ่านผู้ดูแล เปลี่ยน API key ของ AI และปิดโปรแกรม")
        self._segmented[add_page(system, "ตั้งค่าระบบ", settings_page.current_description,
                                 settings_page)] = settings_page
        records = segmented()
        records.add("ประวัติการทำงาน", self._build_activity_tab(),
                    "การกระทำของผู้ดูแล ผู้ใช้ และหุ่นยนต์ล่าสุด", on_show=self._refresh_activity)
        records.add("Log", self._build_log_tab(log_handler), "ข้อความบันทึกการทำงานของโปรแกรมแบบสด")
        self._activity_index = add_page(system, "บันทึกการทำงาน", records.current_description, records)
        self._segmented[self._activity_index] = records
        sidebar_items.addStretch(1)
        version = make_label(f"เวอร์ชัน {__version__}", "hint")
        version.setContentsMargins(10, 0, 0, 0)
        sidebar_items.addWidget(version)

        sidebar_content = QWidget()
        sidebar_content.setLayout(sidebar_items)
        sidebar = QFrame()
        sidebar.setProperty("role", "sidebar")
        sidebar.setFixedWidth(SIDEBAR_WIDTH)
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(0, 0, 0, 0)
        self._sidebar_scroll = _scrollable(sidebar_content)
        sidebar_layout.addWidget(self._sidebar_scroll)

        self._page_group = make_label("", "eyebrow")
        self._page_title = make_label("", "page-title")
        self._page_description = make_label("", "hint", word_wrap=True)
        divider = QFrame()
        divider.setProperty("role", "divider")
        panel = QFrame()
        panel.setProperty("role", "panel")
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(22, 14, 22, 14)
        panel_layout.setSpacing(2)
        self._page_group.setVisible(False)  # the sidebar already shows the group
        panel_layout.addWidget(self._page_group)
        panel_layout.addWidget(self._page_title)
        panel_layout.addWidget(self._page_description)
        panel_layout.addSpacing(8)
        panel_layout.addWidget(divider)
        panel_layout.addSpacing(10)
        panel_layout.addWidget(self._pages, 1)

        body = QHBoxLayout()
        body.setSpacing(14)
        body.addWidget(sidebar)
        body.addWidget(panel, 1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 12, 18, 14)
        layout.setSpacing(12)
        layout.addLayout(self._build_header())
        layout.addLayout(body, 1)

        # A drop-down sized to its longest entry (a long device name) made a whole page wider
        # than the window; it takes the room it is given and shows the rest in its list.
        for form in self.findChildren(QFormLayout):  # labels level with their fields
            form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            form.setHorizontalSpacing(16)
        for combo in self.findChildren(QComboBox):
            combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            combo.setMinimumContentsLength(12)
        for sub in self._segmented.values():
            sub.sub_page_changed.connect(
                lambda _index, page=sub: self._page_description.setText(page.current_description)
            )
        self._nav.idClicked.connect(self._on_nav_clicked)
        self._pages.currentChanged.connect(self._on_page_changed)
        self.select_page(0)

        self._clock_timer = QTimer(self)  # runs only while the Admin screen is on display
        self._clock_timer.setInterval(1000)
        self._clock_timer.timeout.connect(self._tick_clock)
        self._tick_clock()

        ai_key.changed.connect(self._show_ai_tag)
        ai_key.changed.connect(self._dashboard.refresh)
        robot.status_changed.connect(self._on_robot_status)
        robot.command_finished.connect(self._on_command_finished)
        if hardware is not None:  # the chip follows the real robot's link, not the simulator
            hardware.status_changed.connect(lambda *_: self._on_robot_status(robot.status))
        self._on_robot_status(robot.status)

    def reset(self) -> None:
        """Clear sensitive input and unsaved edits when the admin leaves."""
        for field in (self._current_password, self._new_password, self._confirm_password):
            field.clear()
        self._password_result.setText("")
        self._api_key_section.key_field.clear()
        self._api_key_section.key_status.setText("")
        self._audio_tab.stop_test()
        self._hardware_tab.reset()
        self._show_saved_values()
        for sub in self._segmented.values():
            sub.show_sub_page(0)
        self.select_page(0)

    def _show_saved_values(self) -> None:
        """Settings changed underneath the pages (a configuration file was imported)."""
        for page in self._editable_pages.values():
            page.discard_changes()

    def select_page(self, index: int) -> None:
        self._nav.button(index).setChecked(True)
        group, title, description = self._page_info[index]
        self._page_group.setText(group)
        self._page_title.setText(title)
        sub = self._segmented.get(index)
        self._page_description.setText(sub.current_description if sub is not None else description)
        self._pages.setCurrentIndex(index)
        self._sidebar_scroll.ensureWidgetVisible(self._nav.button(index))

    def _confirm_leaving_page(self) -> bool:
        """False if the Admin wants to stay on the page to save their edits."""
        return self._ask_discard(self._editable_pages.get(self._pages.currentIndex()))

    def _ask_discard(self, page: QWidget | None) -> bool:
        """True if ``page`` has nothing unsaved, or the Admin chose to throw the edits away."""
        if page is None or not getattr(page, "has_unsaved_changes", lambda: False)():
            return True
        box = QMessageBox(
            QMessageBox.Icon.Question, "ยังไม่ได้บันทึก",
            "หน้านี้มีการแก้ไขที่ยังไม่ได้บันทึก" + chr(10) + "ต้องการบันทึกก่อนไปต่อหรือไม่",
            parent=self,
        )
        box.setStandardButtons(QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard)
        box.setDefaultButton(QMessageBox.StandardButton.Save)
        box.setEscapeButton(QMessageBox.StandardButton.Save)  # closing the box never throws edits away
        answer = box.exec()
        if answer == QMessageBox.StandardButton.Save:
            return bool(getattr(page, "save_changes", lambda: False)())  # False: stay, the message says why
        page.discard_changes()
        return True

    def _on_nav_clicked(self, index: int) -> None:
        if index == self._pages.currentIndex():
            return
        if self._confirm_leaving_page():
            self.select_page(index)
        else:
            self._nav.button(self._pages.currentIndex()).setChecked(True)

    def _on_logout_clicked(self) -> None:
        if self._confirm_leaving_page():
            self.logout_requested.emit()

    # --- layout pieces ---

    def _build_header(self) -> QHBoxLayout:
        badge = make_label("AI", "badge")
        badge.setFixedSize(48, 48)
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        titles = QVBoxLayout()
        titles.setSpacing(0)
        titles.addWidget(make_label("ผู้ดูแลระบบ", "title"))

        self._clock = make_label("", "clock")
        self._robot_tag = make_label("", "tag")
        self._ai_tag = make_label("", "tag")
        self._show_ai_tag()

        header = QHBoxLayout()
        header.setSpacing(10)
        header.addWidget(badge)
        header.addSpacing(4)
        header.addLayout(titles)
        header.addStretch(1)
        header.addWidget(self._clock)
        self._venue_tag = make_label("", "tag")
        self._venue_tag.setToolTip("หมวดประชาสัมพันธ์ที่หุ่นยนต์ใช้ตอบ (เปลี่ยนได้ที่หน้าฐานความรู้)")
        self._show_venue_tag()
        self._content.content_changed.connect(self._show_venue_tag)
        header.addWidget(self._venue_tag)
        header.addWidget(self._robot_tag)
        header.addWidget(self._ai_tag)
        header.addSpacing(6)
        header.addWidget(make_button("ออกจากโหมดผู้ดูแล", "logout", self._on_logout_clicked))
        return header

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._tick_clock()
        self._clock_timer.start()

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self._clock_timer.stop()

    def _show_ai_tag(self) -> None:
        if self._conversation_service.uses_gemini:
            text, level = "AI: GEMINI", "safe"
        elif not self._settings.ai_enabled:
            text, level = "AI: ปิด", "wait"
        else:
            text, level = "AI: โหมดสาธิต", "wait"
        self._ai_tag.setText(text)
        set_style_property(self._ai_tag, "level", level)

    def _show_venue_tag(self) -> None:
        self._venue_tag.setText(f"หมวด: {self._content.venue().label}")

    def _tick_clock(self) -> None:
        self._clock.setText(QTime.currentTime().toString("HH:mm:ss"))

    # --- pages ---

    def _build_robot_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 0, 8, 0)
        layout.setSpacing(12)

        commands, commands_layout = make_section(
            "คำสั่งทดสอบ",
            "ปุ่มหยุดในโปรแกรมเป็นคำสั่งซอฟต์แวร์ ไม่ทดแทนปุ่ม Emergency Stop ของฮาร์ดแวร์",
        )
        if self._robot.simulated:
            notice = make_label(
                "ใช้หุ่นยนต์จำลอง (ROBOT_ADAPTER=mock): คำสั่งผ่านการตรวจทุกขั้นเหมือนของจริง "
                "แต่ยังไม่มีอุปกรณ์ที่ขยับจริง ผล \"สำเร็จ\" หมายถึงหุ่นยนต์จำลองทำครบเท่านั้น",
                "notice", word_wrap=True,
            )
            commands_layout.addWidget(notice)
        grid = QGridLayout()
        grid.setSpacing(8)
        for index, target in enumerate(self._settings.actions):
            button = make_button(
                target.label, "shortcut",
                lambda t=target.id: self._robot.send(CommandType.ACTION.value, t, source="admin"),
            )
            grid.addWidget(button, index // 3, index % 3)
        commands_layout.addLayout(grid)
        commands_layout.addWidget(
            make_button("หยุดหุ่นยนต์", "danger", lambda: self._robot.stop(source="admin"))
        )
        self._command_result = make_label("", "message", word_wrap=True)
        commands_layout.addWidget(self._command_result)
        layout.addWidget(commands)
        self._robot.command_submitted.connect(self._on_command_submitted)

        safety, safety_layout = make_section(
            "ระยะปลอดภัยจากคน",
            "กล้องมองเห็นเฉพาะด้านหน้า จึงไม่รู้ว่ามีคนอยู่ด้านข้างหรือด้านหลัง "
            "กฎนี้เป็นการป้องกันชั้นซอฟต์แวร์เท่านั้น",
        )
        self._safety_label = make_label("", "message", word_wrap=True)
        safety_layout.addWidget(self._safety_label)
        layout.addWidget(safety)
        self._vision.changed.connect(self._show_safety)
        self._vision.frame_changed.connect(self._show_safety)
        self._show_safety()

        if self._mock is not None:
            mock, mock_layout = make_section("จำลองสถานการณ์ (Mock)")
            lost = QCheckBox("การเชื่อมต่อหลุด")
            lost.toggled.connect(self._mock.simulate_connection_loss)
            fault = QCheckBox("หุ่นยนต์ขัดข้อง")
            fault.toggled.connect(self._mock.simulate_fault)
            mock_layout.addWidget(lost)
            mock_layout.addWidget(fault)
            layout.addWidget(mock)

        layout.addStretch(1)
        return tab

    def _build_activity_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 0, 0, 0)
        self._activity = QTableWidget(0, 4)
        self._activity.setHorizontalHeaderLabels(["เวลา (UTC)", "ผู้ทำ", "การกระทำ", "รายละเอียด"])
        self._activity.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._activity.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self._activity.verticalHeader().setVisible(False)
        header = self._activity.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self._activity, 1)
        row = QHBoxLayout()
        row.addWidget(make_label(f"แสดง {ACTIVITY_ROWS} รายการล่าสุด", "hint"), 1)
        row.addWidget(make_button("โหลดใหม่", "secondary", self._refresh_activity))
        layout.addLayout(row)
        return tab

    def _build_log_tab(self, log_handler: QtLogHandler) -> QWidget:
        """The live log, colour-coded by category, with a filter per category.

        Lines are the same text as in the log file; the colour and the filter
        only make a kind of problem (safety, connection, command...) quick to find.
        """
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        self._log_entries: deque[LogEntry] = deque(maxlen=MAX_LOG_LINES)
        self._log_counts: dict[str, int] = {category: 0 for category in CATEGORY_LABELS}
        self._log_filter: str | None = None  # None = every category
        filters = QHBoxLayout()
        filters.setSpacing(6)
        self._log_filter_group = QButtonGroup(self)
        self._log_filter_buttons: dict[str | None, QPushButton] = {}
        for category, label in [(None, "ทั้งหมด"), *CATEGORY_LABELS.items()]:
            button = QPushButton(label)
            button.setProperty("role", "filter-chip")
            button.setProperty("category", category or "all")
            button.setCheckable(True)
            button.clicked.connect(lambda _checked=False, c=category: self._set_log_filter(c))
            self._log_filter_group.addButton(button)
            self._log_filter_buttons[category] = button
            filters.addWidget(button)
        self._log_filter_buttons[None].setChecked(True)
        filters.addStretch(1)
        layout.addLayout(filters)

        self._log_view = QPlainTextEdit()
        self._log_view.setReadOnly(True)
        self._log_view.setMaximumBlockCount(MAX_LOG_LINES)
        self._log_view.setProperty("role", "log")
        log_handler.emitter.entry.connect(self._on_log_entry)
        layout.addWidget(self._log_view, 1)
        layout.addWidget(make_label(f"ไฟล์ log: {self._settings.log_dir / 'app.log'}", "hint"))
        return tab

    def _on_log_entry(self, entry: LogEntry) -> None:
        self._log_entries.append(entry)
        self._log_counts[entry.category] += 1
        self._show_log_counts()
        if self._log_filter in (None, entry.category):
            self._log_view.appendHtml(_log_html(entry))

    def _set_log_filter(self, category: str | None) -> None:
        self._log_filter = category
        self._log_view.clear()
        for entry in self._log_entries:
            if category in (None, entry.category):
                self._log_view.appendHtml(_log_html(entry))

    def _show_log_counts(self) -> None:
        for category, button in self._log_filter_buttons.items():
            name = "ทั้งหมด" if category is None else CATEGORY_LABELS[category]
            count = len(self._log_entries) if category is None else self._log_counts[category]
            text = f"{name}  {count}" if count else name
            if button.text() != text:
                button.setText(text)

    def _build_security_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 0, 8, 0)
        layout.setSpacing(12)

        password, password_layout = make_section("เปลี่ยนรหัสผ่านผู้ดูแล")
        self._current_password = self._password_field("รหัสผ่านปัจจุบัน")
        self._new_password = self._password_field("รหัสผ่านใหม่")
        self._confirm_password = self._password_field("ยืนยันรหัสผ่านใหม่")
        for field in (self._current_password, self._new_password, self._confirm_password):
            password_layout.addWidget(field)
        save_row = QHBoxLayout()
        save_row.addWidget(make_button("บันทึกรหัสผ่านใหม่", "primary", self._change_password))
        save_row.addStretch(1)
        password_layout.addLayout(save_row)
        self._password_result = make_label("", "message", word_wrap=True)
        password_layout.addWidget(self._password_result)
        layout.addWidget(password)

        self._api_key_section = build_api_key_section(self._ai_key)
        layout.addWidget(self._api_key_section)

        shutdown, shutdown_layout = make_section(
            "ปิดโปรแกรม", "หุ่นยนต์จะหยุดให้บริการจนกว่าจะเปิดโปรแกรมใหม่"
        )
        quit_row = QHBoxLayout()
        quit_row.addWidget(make_button("ปิดโปรแกรม", "danger", self.quit_requested.emit))
        quit_row.addStretch(1)
        shutdown_layout.addLayout(quit_row)
        layout.addWidget(shutdown)
        layout.addStretch(1)
        return tab

    def _password_field(self, placeholder: str) -> QLineEdit:
        field = QLineEdit()
        field.setEchoMode(QLineEdit.EchoMode.Password)
        field.setPlaceholderText(placeholder)
        return field

    # --- handlers ---

    def _on_page_changed(self, index: int) -> None:
        self._audio_tab.stop_test()
        sub = self._segmented.get(index)
        if sub is not None:
            sub.refresh_current()  # the sub-page now showing (statistics, activity) brings itself up to date

    def _refresh_activity(self) -> None:
        entries = self._content.recent_activity(ACTIVITY_ROWS)
        self._activity.setRowCount(len(entries))
        for row, entry in enumerate(entries):
            cells = (entry.created_at.replace("T", " ")[:19], entry.actor, entry.action, entry.detail)
            for column, text in enumerate(cells):
                self._activity.setItem(row, column, QTableWidgetItem(text))

    def _change_password(self) -> None:
        new_password = self._new_password.text()
        if new_password != self._confirm_password.text():
            self._show_password_result("รหัสผ่านใหม่ทั้งสองช่องไม่ตรงกัน", "error")
            return
        if not self._auth.verify(self._current_password.text()):
            self._show_password_result("รหัสผ่านปัจจุบันไม่ถูกต้อง", "error")
            return
        try:
            self._auth.set_password(new_password)
        except ValueError as exc:
            self._show_password_result(str(exc), "error")
            return
        for field in (self._current_password, self._new_password, self._confirm_password):
            field.clear()
        self._content.log_activity(ACTOR_ADMIN, "admin.password_change")
        self._show_password_result("เปลี่ยนรหัสผ่านเรียบร้อยแล้ว", "info")

    def _show_password_result(self, text: str, level: str) -> None:
        self._password_result.setText(text)
        set_style_property(self._password_result, "level", level)

    def _show_safety(self) -> None:
        settings = self._settings
        rule = f"หันตัว: คนต้องอยู่ห่างเกิน {settings.base_clearance_m:.1f} ม."
        if self._vision.status is SourceStatus.DISABLED:
            text, level = "ปิดกล้องอยู่ จึงไม่มีการตรวจระยะคนก่อนขยับ", "error"
        elif not self._vision.fresh:
            text, level = f"{rule}\nกล้องยังไม่พร้อม: คำสั่งที่ทำให้หุ่นยนต์ขยับจะถูกปฏิเสธ", "error"
        elif self._vision.unmeasured_nearby and self._vision.nearest_m is None:
            text, level = (
                f"{rule}\nเห็นคนแต่กล้องวัดระยะไม่ได้ (อาจอยู่ใกล้มาก): "
                "คำสั่งที่ทำให้หุ่นยนต์ขยับจะถูกปฏิเสธจนกว่าจะวัดระยะได้", "error",
            )
        elif self._vision.nearest_m is None:
            text, level = f"{rule}\nตอนนี้ไม่เห็นใครอยู่ด้านหน้า", "info"
        else:
            text, level = f"{rule}\nคนที่ใกล้ที่สุดตอนนี้: {self._vision.nearest_m:.1f} ม.", "info"
        if text != self._safety_label.text():
            self._safety_label.setText(text)
            set_style_property(self._safety_label, "level", level)

    def _on_command_submitted(self, request: CommandRequest) -> None:
        if request.source != "admin":
            return  # this panel reports the Admin's own test commands
        self._command_result.setText(
            f"ส่งคำสั่ง {request.type} → {request.target} แล้ว กำลังตรวจและรอผล...  [id {request.command_id[:8]}]"
        )
        set_style_property(self._command_result, "level", "info")

    def _on_command_finished(self, result: CommandResult) -> None:
        status = COMMAND_STATUS_TEXT.get(result.status, result.status.value)
        self._command_result.setText(
            f"{result.message}  [{status} · {result.status.value} / {result.code} · id {result.command_id[:8]}]"
        )
        set_style_property(self._command_result, "level", "info" if result.ok else "error")

    def _on_robot_status(self, status: RobotStatus) -> None:
        if self._hardware is not None:
            text, level = _HARDWARE_TAG[self._hardware.status.status]
            self._robot_tag.setText(f"หุ่นยนต์: {text}")
            set_style_property(self._robot_tag, "level", level)
            return
        state = status.state if status.connected else RobotState.DISCONNECTED
        simulated = " (จำลอง)" if self._robot.simulated else ""
        self._robot_tag.setText(f"หุ่นยนต์: {robot_state_text(status)}{simulated}")
        set_style_property(self._robot_tag, "level", ROBOT_LEVEL[state])


# The header chip for the real robot (its MQTT link).
_HARDWARE_TAG = {
    MqttStatus.CONNECTED: ("เชื่อมต่อแล้ว", "safe"),
    MqttStatus.CONNECTING: ("กำลังเชื่อมต่อ", "wait"),
    MqttStatus.DISCONNECTED: ("ไม่ได้เชื่อมต่อ", "danger"),
    MqttStatus.ERROR: ("ไม่ได้เชื่อมต่อ", "danger"),
}

_LOG_COLORS = {
    CATEGORY_ERROR: "#ff9a88",
    CATEGORY_WARNING: "#fcd34d",
    CATEGORY_SAFETY: "#ffb784",
    CATEGORY_CONNECTION: "#c4a8ff",
    CATEGORY_COMMAND: "#ff5c8a",
    CATEGORY_INFO: "#e2daf0",
}


def _log_html(entry: LogEntry) -> str:
    """One log line, escaped (log text is never markup), coloured by its category."""
    tag = "" if entry.category == CATEGORY_INFO else f"[{CATEGORY_LABELS[entry.category]}] "
    text = html.escape(tag + entry.text).replace("\n", "<br>")
    return f'<span style="color:{_LOG_COLORS[entry.category]}; white-space:pre-wrap">{text}</span>'


def _scrollable(content: QWidget) -> QScrollArea:
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    scroll.setViewportMargins(0, 0, 8, 0)  # a gap between the content and the scroll bar
    scroll.setWidget(content)
    return scroll

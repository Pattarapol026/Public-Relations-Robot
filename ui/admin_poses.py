"""Admin page "จัดการท่า": the list of poses the arm can make, each with the topic and message it is sent as.

One row per pose: menu number, name, topic, message, how long it takes, on/off and what it is tied to. The Admin can add,
change and delete poses, try one (the same safety rules as every other move of the real robot), and choose which pose is used
for the introduction to the committee, for the rest pose, and for copying a visitor's "6 7". A visitor can also ask for a pose
by saying one of its phrases.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.hardware_controller import DEFAULT_EVENTS, EVENT_NAMES, ArmPose, HardwareController
from ui.admin_hardware import wrapped_check
from ui.form_dialog import CHECK, LINE, TEXT, Field, FormDialog
from ui.widgets import make_button, make_label, make_section, set_style_property
from vision.signs import GESTURE_LABELS

# The hand gestures the camera sees that the arm can answer (the L sign has its own place above, the 67 as well).
CAMERA_GESTURES = ("Wave_Visitor_Right", "Wave_Visitor_Left", "Victory", "Thumb_Up", "Thumb_Down", "Open_Palm",
                   "Pointing_Up", "Closed_Fist", "ILoveYou")
NO_POSE = "(ไม่ทำท่า)"
COLUMNS = ("เมนู", "ชื่อท่า", "topic", "ข้อความที่ส่ง", "เวลา (วิ)", "เปิดใช้", "ใช้อยู่กับ")


class _NoWheelCombo(QComboBox):
    """A list that the mouse wheel does not change: scrolling the page over it once ran through eight poses."""

    def wheelEvent(self, event) -> None:
        event.ignore()


class PoseError(Exception):
    """A pose was not saved; the message is safe to show."""


class PosesTab(QWidget):
    def __init__(self, hardware: HardwareController | None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.hardware = hardware
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 8, 0)
        layout.setSpacing(12)

        table_section, table_layout = make_section(
            "ท่าของแขน",
            "แต่ละท่าส่งข้อความไปที่ topic ของตัวเอง (เว้น topic ว่าง = ใช้ topic เริ่มต้นของแขนด้านล่าง) "
            "ยังไม่แน่ใจชื่อ topic ลองใส่แล้วกด \"ทดสอบส่ง\" ดูว่าหุ่นยนต์ขยับไหม แก้ภายหลังได้ ท่าที่ขยับแขน ส่งท่าพักให้เอง "
            "เมื่อครบเวลา ผู้เข้าชมพูดคำที่ตั้งไว้ในช่อง \"คำพูดที่เรียกท่านี้\" ก็ทำท่านั้นได้",
        )
        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(list(COLUMNS))
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setMinimumHeight(300)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(6, QHeaderView.ResizeMode.Stretch)
        self.table.itemSelectionChanged.connect(self.refresh)
        self.table.itemDoubleClicked.connect(lambda _item: self.edit_selected())
        table_layout.addWidget(self.table)
        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.test_button = make_button("▶  ทดสอบส่ง", "primary", self.test_selected)
        self.add_button = make_button("เพิ่มท่า", "secondary", self.add_pose)
        self.edit_button = make_button("แก้ไข", "secondary", self.edit_selected)
        self.delete_button = make_button("ลบ", "secondary", self.delete_selected)
        self.rest_button = make_button("ส่งท่าพักทันที", "danger", self.send_rest)
        for button in (self.test_button, self.add_button, self.edit_button, self.delete_button):
            buttons.addWidget(button)
        buttons.addStretch(1)
        buttons.addWidget(self.rest_button)
        table_layout.addLayout(buttons)
        self.note_label = make_label("", "hint", word_wrap=True)
        table_layout.addWidget(self.note_label)
        layout.addWidget(table_section)

        events_section, events_layout = make_section(
            "ใช้ท่าไหนกับเหตุการณ์ไหน", "เลือกท่าจากรายการด้านบนให้แต่ละเหตุการณ์ (ท่าที่ถูกใช้อยู่ลบไม่ได้)")
        self.event_boxes: dict[str, QComboBox] = {}
        for event in DEFAULT_EVENTS:
            row = QHBoxLayout()
            row.addWidget(make_label(EVENT_NAMES[event], "hint", word_wrap=True), 3)
            box = _NoWheelCombo()
            box.activated.connect(lambda _index, name=event: self._on_event(name))
            self.event_boxes[event] = box
            row.addWidget(box, 2)
            events_layout.addLayout(row)
        layout.addWidget(events_section)

        camera_section, camera_layout = make_section(
            "ท่ามือที่กล้องเห็น → ท่าแขน",
            "เมื่อกล้องเห็นผู้เข้าชมทำท่ามือนี้ แขนทำท่าที่เลือก (พร้อมคำตอบและสีหน้าที่ตั้งไว้ในหน้า การ์ด/ท่าทาง) "
            "โบกมือบ๊ายบาย: แบมือแกว่งซ้ายขวา 3 ครั้ง หุ่นยนต์ตอบทันที มือขวาของผู้เข้าชมอยู่ซ้ายของภาพ "
            "ตั้งตั้งต้นให้หุ่นยนต์โบกมือตรงข้าม (เหมือนส่องกระจก) ต้องเปิดสวิตช์ \"ให้แขนทำท่าตามคำพูดและท่ามือ\" ด้านล่างด้วย",
        )
        self.gesture_boxes: dict[str, QComboBox] = {}
        for code in CAMERA_GESTURES:
            row = QHBoxLayout()
            row.addWidget(make_label(GESTURE_LABELS.get(code, code), "hint", word_wrap=True), 3)
            box = _NoWheelCombo()
            box.activated.connect(lambda _index, name=code: self._on_gesture_pose(name))
            self.gesture_boxes[code] = box
            row.addWidget(box, 2)
            camera_layout.addLayout(row)
        layout.addWidget(camera_section)

        settings_section, settings_layout = make_section(
            "การตั้งค่าแขน", "ค่ากลางของแขนและสิทธิ์ให้แขนขยับเอง (ผู้ดูแลเป็นผู้เปิด/ปิด)")
        topic_row = QHBoxLayout()
        topic_row.addWidget(make_label("topic เริ่มต้นของแขน", "hint"))
        self.topic_input = QLineEdit()
        self.topic_input.setPlaceholderText("ใช้กับท่าที่ไม่ได้ตั้ง topic ของตัวเอง เช่น robot/arm")
        self.topic_input.returnPressed.connect(self.apply_settings)
        topic_row.addWidget(self.topic_input, 1)
        self.topic_button = make_button("ใช้ค่านี้", "secondary", self.apply_settings)
        topic_row.addWidget(self.topic_button)
        settings_layout.addLayout(topic_row)
        self.auto_return_box = wrapped_check(settings_layout, "ให้แขนกลับไปอยู่ท่าพักเอง เมื่อทำท่าครบเวลา")
        self.auto_return_box.toggled.connect(lambda _checked: self.apply_settings())
        self.confirm_box = wrapped_check(
            settings_layout, "ยืนยันว่าบริเวณรอบหุ่นยนต์ไม่มีคน และมีคนพร้อมกดหยุดที่ตัวหุ่นยนต์ (จำเป็นสำหรับการทดสอบส่งในหน้านี้)")
        self.confirm_box.toggled.connect(self._on_confirm)
        self.intro_box = wrapped_check(
            settings_layout,
            "ให้แขนโบกมือตอนหุ่นยนต์ทักทายกรรมการ (ท่ามือตัว L) และกลับท่าพักเมื่อพูดจบ "
            "แขนจะขยับขณะมีคนยืนใกล้หุ่นยนต์ และไม่ผ่านการยืนยันด้านบนหรือการตรวจระยะด้วยกล้อง",
        )
        self.intro_box.toggled.connect(self._on_intro)
        self.voice_box = wrapped_check(
            settings_layout,
            "ให้แขนทำท่าตามคำพูดและท่ามือของผู้เข้าชม (พูดคำในช่อง \"คำพูดที่เรียกท่านี้\" หรือทำท่า 67 หน้ากล้อง) "
            "แขนจะขยับขณะมีคนยืนใกล้หุ่นยนต์ และไม่ผ่านการยืนยันด้านบน",
        )
        self.voice_box.toggled.connect(self._on_voice)
        self.reason_label = make_label("", "hint", word_wrap=True)
        settings_layout.addWidget(self.reason_label)
        layout.addWidget(settings_section)
        layout.addStretch(1)

        if hardware is not None:
            hardware.state_changed.connect(self.refresh)
            hardware.status_changed.connect(self.refresh)
            hardware.command_changed.connect(self.refresh)
        self.refresh()

    # --- selection ---

    def selected_id(self) -> str | None:
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if not rows:
            return None
        item = self.table.item(rows[0].row(), 1)
        return item.data(Qt.ItemDataRole.UserRole) if item is not None else None

    def select(self, pose_id: str) -> None:
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 1)
            if item is not None and item.data(Qt.ItemDataRole.UserRole) == pose_id:
                self.table.selectRow(row)
                return

    # --- actions ---

    def test_selected(self) -> None:
        pose_id = self.selected_id()
        if self.hardware is None or pose_id is None:
            return
        reason = self.hardware.send_arm(pose_id)
        self._say(f"ไม่ได้ส่ง: {reason}" if reason else f"ส่งท่า {self.hardware.pose(pose_id).title} แล้ว", bool(reason))
        self.refresh()

    def send_rest(self) -> None:
        if self.hardware is None:
            return
        reason = self.hardware.send_arm(self.hardware.events["rest"])
        self._say(f"ไม่ได้ส่ง: {reason}" if reason else "ส่งท่าพักแล้ว", bool(reason))
        self.refresh()

    def add_pose(self) -> None:
        if self.hardware is None:
            return
        menu = max((pose.menu for pose in self.hardware.poses), default=0) + 1
        self._open_form("เพิ่มท่า", ArmPose(id="", title="", description="", seconds=4.0, menu=menu), None)

    def edit_selected(self) -> None:
        pose_id = self.selected_id()
        pose = self.hardware.pose(pose_id) if self.hardware is not None and pose_id else None
        if pose is not None:
            self._open_form(f"แก้ไขท่า: {pose.title}", pose, pose.id)

    def delete_selected(self) -> None:
        pose_id = self.selected_id()
        if self.hardware is None or pose_id is None:
            return
        reason = self.hardware.delete_pose(pose_id)
        self._say(reason or "ลบท่าแล้ว", bool(reason))
        self.refresh()

    def _open_form(self, title: str, pose: ArmPose, original_id: str | None) -> None:
        fields = [
            Field("menu", "ลำดับเมนู (ตัวเลข)", LINE, str(pose.menu or ""), 4),
            Field("title", "ชื่อท่า", LINE, pose.title, 40),
            Field("id", "รหัสท่า (a-z ตัวเลข _ ใช้ภายใน เช่น wave_left)", LINE, pose.id, 30),
            Field("topic", "topic ที่ส่ง (ว่าง = topic เริ่มต้นของแขน)", LINE, pose.topic, 100),
            Field("payload", "ข้อความที่ส่ง (ว่าง = ส่งรหัสท่า)", LINE, pose.payload, 100),
            Field("seconds", "เวลาทำท่า (วินาที) 0 = ไม่ต้องส่งท่าพักตามหลัง", LINE, f"{pose.seconds:g}", 6),
            Field("phrases", "คำพูดที่เรียกท่านี้ (คั่นด้วย , เช่น โบกมือซ้าย,โบกซ้าย)", LINE, ", ".join(pose.phrases), 300),
            Field("reply", "ข้อความที่หุ่นยนต์พูดตอนทำท่า (ว่าง = รับทราบสั้น ๆ)", LINE, pose.reply, 100),
            Field("description", "คำอธิบายท่า", TEXT, pose.description),
            Field("enabled", "เปิดใช้งานท่านี้", CHECK, pose.enabled),
        ]

        def submit(values: dict) -> None:
            try:
                menu = int(str(values["menu"]).strip() or 0)
                seconds = float(str(values["seconds"]).strip() or 0)
            except ValueError:
                raise PoseError("ลำดับเมนูและเวลาต้องเป็นตัวเลข") from None
            new = ArmPose(
                id=values["id"].strip(), title=values["title"], description=values["description"].strip(),
                seconds=seconds, topic=values["topic"], payload=values["payload"],
                phrases=tuple(part for part in (p.strip() for p in values["phrases"].split(",")) if part),
                reply=values["reply"].strip(), enabled=bool(values["enabled"]), menu=menu,
            )
            reason = self.hardware.save_pose(new, original_id)
            if reason:
                raise PoseError(reason)
            self._saved = new.id

        self._saved = ""
        if FormDialog(title, fields, submit, PoseError, self).exec():
            self.refresh()
            self.select(self._saved)

    def apply_settings(self) -> None:
        if self.hardware is None:
            return
        reason = self.hardware.set_arm(self.topic_input.text(), auto_return=self.auto_return_box.isChecked())
        self._say(reason, bool(reason)) if reason else None
        self.refresh()

    def _on_event(self, event: str) -> None:
        if self.hardware is not None:
            reason = self.hardware.set_event(event, self.event_boxes[event].currentData())
            self._say(reason, bool(reason)) if reason else None
        self.refresh()

    def _on_gesture_pose(self, code: str) -> None:
        if self.hardware is not None:
            reason = self.hardware.set_gesture_pose(code, self.gesture_boxes[code].currentData() or "")
            self._say(reason, bool(reason)) if reason else None
        self.refresh()

    def _on_confirm(self, checked: bool) -> None:
        if self.hardware is not None:
            self.hardware.confirm_area(checked)

    def _on_intro(self, checked: bool) -> None:
        if self.hardware is not None:
            self.hardware.set_arm_with_intro(checked)

    def _on_voice(self, checked: bool) -> None:
        if self.hardware is not None:
            self.hardware.set_arm_by_voice(checked)

    def reset(self) -> None:
        """The Admin left: the safety confirmation is asked again next time."""
        self.confirm_box.setChecked(False)

    def _say(self, text: str, problem: bool) -> None:
        self.note_label.setText(text)
        set_style_property(self.note_label, "level", "error" if problem else "")

    # --- display ---

    def refresh(self, *_args) -> None:
        if not hasattr(self, "reason_label"):
            return  # still being built
        hardware = self.hardware
        if hardware is None:
            for widget in (self.table, self.test_button, self.add_button, self.edit_button, self.delete_button,
                           self.rest_button, self.topic_input, self.topic_button, self.auto_return_box,
                           self.confirm_box, self.intro_box, self.voice_box, *self.event_boxes.values(), *self.gesture_boxes.values()):
                widget.setEnabled(False)
            self.reason_label.setText("ยังไม่ได้ตั้งค่าการเชื่อมต่อฮาร์ดแวร์ (ดูหน้า ควบคุมหุ่นยนต์)")
            return
        keep = self.selected_id()
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        for pose in hardware.poses:
            row = self.table.rowCount()
            self.table.insertRow(row)
            cells = (
                str(pose.menu or "-"), pose.title, hardware.arm_topic_for(pose.id), pose.message,
                f"{pose.seconds:g}" if pose.seconds else "-", "✓" if pose.enabled else "ปิด", " / ".join(hardware.uses_of(pose.id)),
            )
            for column, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if column == 1:
                    item.setData(Qt.ItemDataRole.UserRole, pose.id)
                if not pose.enabled:
                    item.setForeground(Qt.GlobalColor.gray)
                self.table.setItem(row, column, item)
        if keep is not None:
            self.select(keep)
        self.table.blockSignals(False)
        chosen = self.selected_id()
        for widget, needs_row in ((self.test_button, True), (self.edit_button, True), (self.delete_button, True)):
            widget.setEnabled(chosen is not None or not needs_row)
        reason = hardware.arm_refusal(chosen) if chosen else ""
        self.test_button.setEnabled(chosen is not None and not reason)
        self.test_button.setToolTip(reason)
        rest_reason = hardware.arm_refusal(hardware.events["rest"])
        self.rest_button.setEnabled(not rest_reason)
        self.rest_button.setToolTip(rest_reason)
        self.reason_label.setText(f"ยังทดสอบส่งไม่ได้: {reason or rest_reason}" if (chosen and reason) or rest_reason else "")
        for event, box in self.event_boxes.items():
            box.blockSignals(True)
            box.clear()
            for pose in hardware.poses:
                box.addItem(f"{pose.menu or '-'}  {pose.title}", pose.id)
            box.setCurrentIndex(max(0, box.findData(hardware.events[event])))
            box.blockSignals(False)
        for code, box in self.gesture_boxes.items():
            box.blockSignals(True)
            box.clear()
            box.addItem(NO_POSE, "")
            for pose in hardware.poses:
                box.addItem(f"{pose.menu or '-'}  {pose.title}", pose.id)
            box.setCurrentIndex(max(0, box.findData(hardware.gesture_poses.get(code, ""))))
            box.blockSignals(False)
        for box, value in ((self.auto_return_box, hardware.arm_auto_return), (self.intro_box, hardware.arm_with_intro),
                           (self.voice_box, hardware.arm_by_voice), (self.confirm_box, hardware.area_confirmed)):
            if box.isChecked() != value:
                box.blockSignals(True)
                box.setChecked(value)
                box.blockSignals(False)
        if not self.topic_input.hasFocus():
            self.topic_input.setText(hardware.arm["topic"])

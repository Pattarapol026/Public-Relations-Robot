"""The Admin's robot commands over MQTT (turns and moves), with the checks around them.

Turns (left/right, an angle), moves (forward/backward, a distance), typed
messages and the visitor screen's buttons all go to one topic (one robot), chosen
on the Admin page and remembered. On top of the link's
own rules (connected, STOP configured, allowed direction and value, one command a
second) a command needs the Admin to have
confirmed, this visit to the Admin, that nobody is near the robot and someone can
stop it by hand; leaving the Admin forgets that. With ``require_camera`` the
camera must also see nobody within the robot's clearance. STOP needs none of it.
Every command sent or refused is written to the activity log.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, replace
from typing import Callable

from PySide6.QtCore import QObject, QTimer, Signal

from app.content_controller import ACTOR_ADMIN
from config.settings import Settings
from database.repositories import SettingsRepository
from services.hardware_link import (
    HardwareLink,
    HardwareLinkConfig,
    HardwareLinkError,
    TURNS,
    SentCommand,
    StatusMessage,
    value_range,
)
from services.mqtt_service import StatusSnapshot

logger = logging.getLogger(__name__)

GROUP_TURN = "turn"
GROUP_MOVE = "move"
KEY_TOPICS = "hardware.topics"  # topics the Admin added (JSON list)
KEY_TOPIC = "hardware.topic.all"  # where every command goes (JSON string)
KEY_SELECTED = "hardware.topic"  # earlier choices: alone, then + ".turn" / ".move" (read once, to carry over)
KEY_LISTEN = "hardware.listen"  # topics the Admin listens to (JSON list)
KEY_AMOUNT = "hardware.amount"  # + ".turn" / ".move": degrees / centimetres a press sends (JSON int)
# true: the visitor screen's ◀ ▲ ▼ ▶ buttons may move the real robot. Off until the Admin turns it on.
KEY_VISITOR = "hardware.visitor_enabled"
KEY_ARM = "hardware.arm"  # {"topic", "auto_return"} and, before the pose list existed, "payloads", "seconds", "topics" (JSON)
KEY_POSES = "hardware.poses"  # the Admin's list of poses (JSON list of objects)
KEY_GESTURE_POSES = "hardware.gesture_poses"  # {camera gesture: pose id or ""} (JSON object)
KEY_EVENTS = "hardware.pose_events"  # which pose each event uses (JSON object)
# true: the arm waves while the robot introduces itself to the committee (the L sign) and goes back to rest after.
# It moves with a person standing close, so it has its own switch and none of the checks of the Admin's buttons.
KEY_ARM_INTRO = "hardware.arm_with_intro"
# true: a gesture asked for by voice or text ("67") moves the arm. Same reasoning as the intro: a person stands close.
KEY_ARM_VOICE = "hardware.arm_by_voice"
DEFAULT_AMOUNTS = {GROUP_TURN: 30, GROUP_MOVE: 25}
ACTOR_TOUCH = "touch"  # the visitor screen, as the command gateway calls it
_SAFETY_REASONS = {
    "person_too_close": "กล้องเห็นคนอยู่ใกล้หุ่นยนต์เกินระยะปลอดภัย",
    "distance_unknown": "กล้องเห็นคนแต่วัดระยะไม่ได้",
    "sensor_unavailable": "กล้องยังไม่พร้อม จึงตรวจระยะคนไม่ได้",
}


def hardware_config(settings: Settings) -> HardwareLinkConfig:
    return HardwareLinkConfig(
        host=settings.hw_mqtt_host,
        port=settings.hw_mqtt_port,
        username=settings.hw_mqtt_username,
        password=settings.hw_mqtt_password,
        direction_topic=settings.hw_direction_topic,
        stop_payload=settings.hw_stop_payload,
        arm_topic=settings.hw_arm_topic,
        arm_wave_payload=settings.hw_arm_wave_payload,
        arm_home_payload=settings.hw_arm_home_payload,
        reply_topic=settings.hw_reply_topic,
        status_topic=settings.hw_status_topic,
        payload_format=settings.hw_payload_format,
        turn_max_deg=settings.hw_turn_max_deg,
        move_max_cm=settings.hw_move_max_cm,
    )


def group_of(direction: str) -> str:
    return GROUP_TURN if direction in TURNS else GROUP_MOVE


@dataclass(frozen=True)
class ArmPose:
    """One pose or movement the arm can be told to make. The Admin keeps the list (page "จัดการท่า"); these are the
    ones it starts with. ``payload`` is the message sent ("" = the id), ``topic`` where ("" = the arm's topic),
    ``seconds`` how long it takes before the arm is sent back to rest by itself, ``phrases`` what a visitor may say to
    ask for it, ``reply`` what the robot says as it does it ("" = a short acknowledgement)."""

    id: str
    title: str
    description: str
    seconds: float
    topic: str = ""
    payload: str = ""
    phrases: tuple[str, ...] = ()
    reply: str = ""
    enabled: bool = True
    menu: int = 0

    @property
    def message(self) -> str:
        return self.payload or self.id


ArmGesture = ArmPose  # the name used before the Admin could edit the list


HOME = "home"
# How long the robot is guessed to take over a move, to space out the several moves of "back to where you were": the next is
# sent when the one before should be done. Not measured: set from what the robot is seen to do.
TURN_DEG_PER_S = 45.0
MOVE_CM_PER_S = 10.0
STEP_GAP_S = 1.2  # at least this long between two moves (the link allows one a second)
JOURNAL_SIZE = 60
_OPPOSITE = {"left": "right", "right": "left", "forward": "backward", "backward": "forward"}
# What the arm is asked to do. The hardware decides how: these descriptions say what each is meant to look like. The
# message sent for each can be changed on the Admin page (the default is the id), so the hardware team's own
# words can be used.
ARM_GESTURES: tuple[ArmPose, ...] = (
    ArmPose(HOME, "ท่าพัก", "ลดแขนกลับไปอยู่ท่าพัก (ท่า 1: mosquitto_pub -t robot/pos -m 1)", 0.0, "robot/pos", payload="1",
            phrases=("ท่าพัก",), menu=1),
    ArmPose("wave", "โบกมือซ้าย", "ยกแขนซ้ายโบกมือทักทาย (ท่า 2: mosquitto_pub -t robot/pos -m 2)", 4.0, "robot/pos", payload="2",
            phrases=("โบกมือ", "โบกมือซ้าย"), menu=2),
    ArmPose("wave_right", "โบกมือขวา", "ยกแขนขวาโบกมือทักทาย (ข้อความ 3 เป็นการเดา ยังไม่ได้ยืนยัน)", 4.0, "robot/pos", payload="3",
            phrases=("โบกมือขวา",), menu=3),
    ArmPose("shake_hand", "จับมือ (Shake hand)",
            "ยื่นแขนตรง ๆ ไปข้างหน้าเหมือนจะจับมือ พอรู้สึกว่ามีคนจับก็เขย่าเบา ๆ แล้วกลับท่าเดิม", 8.0,
            phrases=("จับมือ", "shake hand", "ขอจับมือ"), menu=4),
    ArmPose("wai", "ไหว้", "ยกมือขึ้นประนมไหว้ทักทาย", 4.0, phrases=("ไหว้", "สวัสดีไหว้"), menu=5),
    ArmPose("raise", "ชูมือ", "ยกแขนขึ้นชูมือ", 3.0, phrases=("ชูมือ", "ยกมือ"), menu=6),
    ArmPose("point", "ชี้ไปข้างหน้า", "ยื่นแขนชี้ไปข้างหน้า", 3.0, phrases=("ชี้ไปข้างหน้า",), menu=7),
    ArmPose("invite", "เชิญ", "ผายมือเชิญเข้าไปด้านใน", 4.0, phrases=("เชิญ", "เชิญเข้าไป"), menu=8),
    ArmPose("cheer", "ชูแขนยินดี", "ยกแขนขึ้นแสดงความยินดี", 4.0, phrases=("ยินดีด้วย", "ชูแขนยินดี"), menu=9),
    ArmPose("six_seven", "67 (ซิกซ์เซเว่น)",
            "แขนซ้ายและแขนขวาขึ้นลงสลับกัน: ซ้ายลงขวาขึ้น แล้วซ้ายขึ้นขวาลง วนต่อเนื่องจนครบเวลา", 6.0,
            phrases=("67", "6 7", "หกเจ็ด", "หกสิบเจ็ด", "six seven", "six7", "sixty seven", "ซิกซ์เซเว่น", "ซิกเซเว่น",
                     "ซิกส์เซเว่น", "ซิกซ์เซเวน", "ซิกเซเวน"), reply="Six seven.", menu=10),
)
GESTURE_IDS = tuple(gesture.id for gesture in ARM_GESTURES)
# What the Admin may tie a pose to: the introduction to the committee (the L sign), the rest pose the arm is sent back to,
# and the pose the camera copies when a visitor does "6 7".
EVENT_INTRO, EVENT_REST, EVENT_CAMERA_67 = "intro", "rest", "camera_67"
DEFAULT_EVENTS = {EVENT_INTRO: "wave", EVENT_REST: HOME, EVENT_CAMERA_67: "six_seven"}
EVENT_NAMES = {
    EVENT_INTRO: "ทักทายกรรมการ (ท่ามือตัว L) ให้โบกด้วยท่า",
    EVENT_REST: "ท่าพักที่แขนถูกส่งกลับไปเมื่อท่าจบ",
    EVENT_CAMERA_67: "กล้องเห็นคนทำท่า 67 ให้ทำตามด้วยท่า",
}
# The camera sees a hand gesture: which pose the arm makes (the Admin changes it; "" = none). The visitor's right hand is
# answered with the robot's left (a goodbye wave is waved back), a two-finger V makes the 67 pose.
DEFAULT_GESTURE_POSES = {"Victory": "six_seven", "Wave_Visitor_Right": "wave", "Wave_Visitor_Left": "wave_right"}
_POSE_ID = re.compile(r"[a-z][a-z0-9_]{0,29}")


class HardwareController(QObject):
    # Emitted from the MQTT thread; Qt queues them to the GUI thread.
    status_changed = Signal(object)  # StatusSnapshot
    command_changed = Signal(object)  # SentCommand: sent, broker ack, hardware reply
    robot_status_received = Signal(object)  # StatusMessage on the robot's status topic
    message_received = Signal(object)  # ReceivedMessage: anything that arrives
    # From the GUI thread: the confirmation, the pause between commands, the reply window.
    state_changed = Signal()

    def __init__(
        self,
        link: HardwareLink,
        audit: Callable[[str, str, str], None] | None = None,
        safety_check: Callable[[], str | None] | None = None,
        parent: QObject | None = None,
        topics: tuple[str, ...] = (),
        store: SettingsRepository | None = None,
        default_topics: dict[str, str] | None = None,
    ) -> None:
        super().__init__(parent)
        self._link = link
        self._audit = audit
        self._safety_check = safety_check  # None = the camera is not asked
        self._area_confirmed = False
        # Topics a command may go to: the presets from .env, then those the Admin typed.
        self._store = store
        defaults = default_topics or {}
        self._default = defaults.get(GROUP_TURN) or link.config.direction_topic
        self._presets = tuple(dict.fromkeys((link.config.direction_topic, *defaults.values(), *topics)))
        self._added = [t for t in self._load(KEY_TOPICS, []) if t not in self._presets and not link.topic_refusal(t)]
        # One robot, one topic: turns, moves, typed messages and the visitor screen all use it.
        # Earlier versions kept one per kind of command; the turns' choice carries over.
        chosen = (self._load(KEY_TOPIC, None) or self._load(f"{KEY_SELECTED}.{GROUP_TURN}", None)
                  or self._load(KEY_SELECTED, None) or self._default)
        self._topic = chosen if chosen in self.topics else self._default
        for topic in self._load(KEY_LISTEN, []):
            link.listen(str(topic))  # an unusable stored topic is simply left out
        self._amounts = {
            group: self._clamp(group, self._load(f"{KEY_AMOUNT}.{group}", default))
            for group, default in DEFAULT_AMOUNTS.items()
        }
        self._visitor_enabled = self._load(KEY_VISITOR, False) is True
        stored_arm = self._load(KEY_ARM, {})
        stored_arm = stored_arm if isinstance(stored_arm, dict) else {}
        config = link.config
        self._arm_topic = str(stored_arm.get("topic") or config.arm_topic)  # for a pose with no topic of its own
        self._arm_auto_return = stored_arm.get("auto_return", True) is not False
        self._poses: list[ArmPose] = self._load_poses(stored_arm, config)
        stored_gestures = self._load(KEY_GESTURE_POSES, {})
        stored_gestures = stored_gestures if isinstance(stored_gestures, dict) else {}
        self._gesture_poses = {**DEFAULT_GESTURE_POSES, **{str(k): str(v) for k, v in stored_gestures.items()}}
        stored_events = self._load(KEY_EVENTS, {})
        stored_events = stored_events if isinstance(stored_events, dict) else {}
        self._events = {
            event: (stored_events.get(event) if self._pose_index(str(stored_events.get(event))) is not None else default)
            for event, default in DEFAULT_EVENTS.items()
        }
        self._return_timer = QTimer(self)  # the arm goes back to rest by itself after a gesture
        self._return_timer.setSingleShot(True)
        self._return_timer.timeout.connect(self._send_arm_home)
        self._arm_with_intro = self._load(KEY_ARM_INTRO, False) is True
        self._arm_by_voice = self._load(KEY_ARM_VOICE, False) is True
        # Every move sent since the robot was last at its start (reset on "this is the start"), to walk it back.
        self._journal: list[tuple[str, int]] = []
        self._queue: list[tuple[str, int]] = []  # moves still to send, one after the other
        self._queue_timer = QTimer(self)
        self._queue_timer.setSingleShot(True)
        self._queue_timer.timeout.connect(self._send_next_queued)
        self._arm_raised = False  # the intro wave was sent and the arm has not been sent home yet
        link.on_status.append(self.status_changed.emit)
        link.on_change.append(self.command_changed.emit)
        link.on_robot_status.append(self.robot_status_received.emit)
        link.on_message.append(self.message_received.emit)
        self._wake = QTimer(self)
        self._wake.setSingleShot(True)
        self._wake.timeout.connect(self.state_changed.emit)

    @property
    def link(self) -> HardwareLink:
        return self._link

    @property
    def config(self) -> HardwareLinkConfig:
        return self._link.config

    @property
    def status(self) -> StatusSnapshot:
        return self._link.status

    @property
    def last_command(self) -> SentCommand | None:
        return self._link.last_command

    @property
    def topics(self) -> tuple[str, ...]:
        return (*self._presets, *self._added)

    @property
    def topic(self) -> str:
        """Where every command goes: turns, moves, typed messages, the visitor screen's buttons."""
        return self._topic

    def is_preset(self, topic: str) -> bool:
        return topic in self._presets

    def select_topic(self, topic: str) -> str:
        """Send every command to ``topic`` from now on, adding it if it is new; "" or why not."""
        topic = topic.strip()
        reason = self._link.topic_refusal(topic)
        if reason:
            return reason
        if topic not in self.topics:
            self._added.append(topic)
            self._save(KEY_TOPICS, self._added)
            if self._audit is not None:
                self._audit(ACTOR_ADMIN, "hardware.topic_add", f"topic={topic}")
        if topic != self._topic:
            self._topic = topic
            self._save(KEY_TOPIC, topic)
            if self._audit is not None:
                self._audit(ACTOR_ADMIN, "hardware.topic_select", f"topic={topic}")
            self.state_changed.emit()
        return ""

    def remove_topic(self, topic: str) -> str:
        """Forget a topic the Admin added; the presets from .env stay."""
        if topic not in self._added:
            return "ลบได้เฉพาะ topic ที่พิมพ์เพิ่มเอง (topic จาก .env ลบไม่ได้)"
        self._added.remove(topic)
        self._save(KEY_TOPICS, self._added)
        if self._audit is not None:
            self._audit(ACTOR_ADMIN, "hardware.topic_remove", f"topic={topic}")
        if self._topic == topic:
            self._topic = self._default
            self._save(KEY_TOPIC, self._default)
        self.state_changed.emit()
        return ""

    @property
    def robot_status(self) -> StatusMessage | None:
        return self._link.robot_status

    @property
    def uses_camera(self) -> bool:
        return self._safety_check is not None

    @property
    def area_confirmed(self) -> bool:
        return self._area_confirmed

    def confirm_area(self, confirmed: bool) -> None:
        if confirmed != self._area_confirmed:
            self._area_confirmed = confirmed
            if self._audit is not None and confirmed:
                self._audit(ACTOR_ADMIN, "hardware.area_confirmed", "")
            self.state_changed.emit()

    def reset(self) -> None:
        """The Admin left: the next one confirms again."""
        self.confirm_area(False)

    def direction_refusal(self, direction: str, value: int) -> str:
        topic = self._topic
        return (
            self._link.direction_refusal(direction, value) or self._link.topic_refusal(topic)
            or self._area_refusal()
        )

    def _area_refusal(self) -> str:
        if not self._area_confirmed:
            return "ยืนยันก่อนว่าบริเวณรอบหุ่นยนต์ไม่มีคน และมีคนพร้อมกดหยุดที่ตัวหุ่นยนต์"
        if self._safety_check is not None:
            code = self._safety_check()
            if code:
                return _SAFETY_REASONS.get(code, code)
        return ""

    # --- how far a press goes (shared by the Admin page and the visitor screen) ---

    def _clamp(self, group: str, value) -> int:
        low, high = value_range(self.config, "left" if group == GROUP_TURN else "forward")
        try:
            value = int(value)
        except (TypeError, ValueError):
            value = DEFAULT_AMOUNTS[group]
        return min(max(value, low), high)

    def amount(self, group: str) -> int:
        """Degrees (turns) or centimetres (moves) that one press sends."""
        return self._amounts[group]

    def set_amount(self, group: str, value: int) -> None:
        value = self._clamp(group, value)
        if value != self._amounts[group]:
            self._amounts[group] = value
            self._save(f"{KEY_AMOUNT}.{group}", value)
            self.state_changed.emit()

    def amount_limit(self, group: str) -> int:
        """The most one press may send, from the hardware settings."""
        return value_range(self.config, "left" if group == GROUP_TURN else "forward")[1]

    # --- the visitor screen ---

    @property
    def visitor_enabled(self) -> bool:
        return self._visitor_enabled

    def set_visitor_enabled(self, enabled: bool) -> None:
        if enabled != self._visitor_enabled:
            self._visitor_enabled = enabled
            self._save(KEY_VISITOR, enabled)
            if self._audit is not None:
                self._audit(ACTOR_ADMIN, "hardware.visitor_buttons", "on" if enabled else "off")
            self.state_changed.emit()

    def visitor_refusal(self, direction: str) -> str:
        """Why a visitor's press would not be sent now ("" if it would)."""
        if not self._link.is_connected:
            return "ยังไม่ได้เชื่อมต่อกับหุ่นยนต์"
        if not self._visitor_enabled:
            return "ผู้ดูแลยังไม่เปิดให้สั่งหุ่นยนต์จากหน้าจอนี้"
        topic = self._topic
        reason = (
            self._link.direction_refusal(direction, self._amounts[group_of(direction)])
            or self._link.topic_refusal(topic)
        )
        if reason:
            return reason
        if self._safety_check is not None:
            code = self._safety_check()
            if code:
                return _SAFETY_REASONS.get(code, code)
        return ""

    def send_from_visitor(self, direction: str) -> str:
        """A press on the visitor screen: "" when sent, else why not (also logged)."""
        value = self._amounts[group_of(direction)]
        topic = self._topic
        reason = self.visitor_refusal(direction)
        if not reason:
            try:
                record = self._link.send_direction(direction, value, topic)
            except HardwareLinkError as exc:
                reason = str(exc)
            else:
                self._log("hardware.direction", record, "sent", ACTOR_TOUCH)
                self._remember(direction, value)
                self._schedule_wake()
                return ""
        if self._audit is not None:
            self._audit(ACTOR_TOUCH, "hardware.direction",
                        f"topic={topic} direction={direction} value={value} refused={reason}")
        return reason

    # --- spoken or typed moves: "หันซ้าย 70 องศา", "เดินหน้า 20 เซน", "กลับมาที่เดิม" ---

    def _remember(self, direction: str, value: int) -> None:
        self._journal.append((direction, value))
        del self._journal[:-JOURNAL_SIZE]

    @property
    def journal(self) -> tuple[tuple[str, int], ...]:
        """The moves sent since the robot was last at its start, oldest first."""
        return tuple(self._journal)

    def forget_position(self) -> None:
        """Here is the start now: "back to where you were" means this place from here on."""
        self._journal.clear()

    def voice_refusal(self, direction: str, value: int) -> str:
        """Why a spoken move would not be sent now ("" if it would): the Admin's switch for visitors, the link's own rules
        (connected, a STOP message set, the range, one command a second), and the camera if it is asked."""
        if not self._link.is_connected:
            return "ยังไม่ได้เชื่อมต่อกับหุ่นยนต์"
        if not self._visitor_enabled:
            return "ผู้ดูแลยังไม่เปิดให้สั่งหุ่นยนต์ด้วยเสียงหรือการพิมพ์"
        reason = self._link.direction_refusal(direction, value) or self._link.topic_refusal(self._topic)
        if reason:
            return reason
        if self._safety_check is not None:
            code = self._safety_check()
            if code:
                return _SAFETY_REASONS.get(code, code)
        return ""

    def drive_by_voice(self, direction: str, value: int) -> str:
        """A spoken or typed move: "" when sent, else why not (also logged)."""
        self._cancel_queue()  # a new move replaces what was left of the way back
        topic = self._topic
        reason = self.voice_refusal(direction, value)
        if not reason:
            try:
                record = self._link.send_direction(direction, value, topic)
            except HardwareLinkError as exc:
                reason = str(exc)
            else:
                self._log("hardware.direction", record, "sent", ACTOR_TOUCH)
                self._remember(direction, value)
                self._schedule_wake()
                return ""
        if self._audit is not None:
            self._audit(ACTOR_TOUCH, "hardware.direction",
                        f"topic={topic} direction={direction} value={value} refused={reason}")
        return reason

    def way_back(self) -> list[tuple[str, int]]:
        """The moves that undo the journal, in the order to send them: the last move first, turned the other way. Moves
        one after the other in the same line are added up, and none is longer than the hardware's limit."""
        merged: list[list] = []
        for direction, value in self._journal:
            axis = "turn" if direction in ("left", "right") else "move"
            signed = value if direction in ("left", "forward") else -value
            if merged and merged[-1][0] == axis:
                merged[-1][1] += signed
            else:
                merged.append([axis, signed])
        steps: list[tuple[str, int]] = []
        for axis, signed in reversed(merged):
            if signed == 0:
                continue
            if axis == "turn":
                direction, limit = ("right" if signed > 0 else "left"), self.config.turn_max_deg
            else:
                direction, limit = ("backward" if signed > 0 else "forward"), self.config.move_max_cm
            remaining = abs(signed)
            while remaining > 0:
                chunk = min(remaining, limit)
                steps.append((direction, chunk))
                remaining -= chunk
        return steps

    def return_to_start(self) -> tuple[str, int]:
        """"Back to where you were": walks the journal back. ``("", n)``: n moves are on their way (0: already there);
        otherwise why not."""
        steps = self.way_back()
        if not steps:
            return "", 0
        reason = self.voice_refusal(*steps[0])
        if reason:
            return reason, 0
        self._journal.clear()  # the way back is not itself part of the way
        self._queue = steps
        self._send_next_queued()
        return "", len(steps)

    def _cancel_queue(self) -> None:
        self._queue_timer.stop()
        self._queue.clear()

    def _send_next_queued(self) -> None:
        if not self._queue:
            return
        direction, value = self._queue[0]
        wait = self._link.seconds_until_next_direction()
        if wait > 0:
            self._queue_timer.start(int(wait * 1000) + 50)
            return
        self._queue.pop(0)
        try:
            record = self._link.send_direction(direction, value, self._topic)
        except HardwareLinkError as exc:
            logger.warning("The way back stopped: %s", exc)
            self._queue.clear()
            return
        self._log("hardware.direction", record, "sent", ACTOR_TOUCH)
        self._schedule_wake()
        if self._queue:
            takes = value / (TURN_DEG_PER_S if direction in ("left", "right") else MOVE_CM_PER_S)
            self._queue_timer.start(int(max(STEP_GAP_S, takes + 0.5) * 1000))

    # --- typed messages and listening ---

    def text_refusal(self, text: str) -> str:
        """Why ``text`` may not be sent now ("" if it may). The hardware may move on a
        typed message, so it needs everything a direction needs."""
        return (
            self._link.text_refusal(text) or self._link.topic_refusal(self._topic)
            or self._area_refusal()
        )

    def send_text(self, text: str) -> str:
        """"" when sent, else why not (also logged)."""
        topic = self._topic
        reason = self.text_refusal(text)
        if not reason:
            try:
                record = self._link.send_text(text, topic)
            except HardwareLinkError as exc:
                reason = str(exc)
            else:
                self._log("hardware.text", record, "sent")
                self._schedule_wake()
                return ""
        if self._audit is not None:
            self._audit(ACTOR_ADMIN, "hardware.text", f"topic={topic} payload={text[:200]} refused={reason}")
        return reason

    @property
    def listening(self) -> tuple[str, ...]:
        """Topics the Admin listens to (the status and reply topics are always heard)."""
        return self._link.listening

    def listen(self, topic: str) -> str:
        reason = self._link.listen(topic)
        if not reason:
            self._save(KEY_LISTEN, list(self._link.listening))
            if self._audit is not None:
                self._audit(ACTOR_ADMIN, "hardware.listen", f"topic={topic.strip()}")
            self.state_changed.emit()
        return reason

    def unlisten(self, topic: str) -> None:
        if topic not in self._link.listening:
            return
        self._link.unlisten(topic)
        self._save(KEY_LISTEN, list(self._link.listening))
        if self._audit is not None:
            self._audit(ACTOR_ADMIN, "hardware.unlisten", f"topic={topic}")
        self.state_changed.emit()

    def stop_refusal(self) -> str:
        return self._link.stop_refusal()

    # --- the arm ---

    @staticmethod
    def _seconds(value, default: float) -> float:
        try:
            return min(max(float(value), 0.5), 60.0) if value is not None else default
        except (TypeError, ValueError):
            return default

    # --- the list of poses (page "จัดการท่า") ---

    def _load_poses(self, stored_arm: dict, config) -> list[ArmPose]:
        stored = self._load(KEY_POSES, None)
        if isinstance(stored, list) and stored:
            poses = []
            for raw in stored:
                try:
                    poses.append(ArmPose(
                        id=str(raw["id"]), title=str(raw["title"]), description=str(raw.get("description", "")),
                        seconds=self._seconds(raw.get("seconds"), 4.0) if float(raw.get("seconds", 0) or 0) > 0 else 0.0,
                        topic=str(raw.get("topic", "")), payload=str(raw.get("payload", "")),
                        phrases=tuple(str(p) for p in raw.get("phrases", ())), reply=str(raw.get("reply", "")),
                        enabled=raw.get("enabled", True) is not False, menu=int(raw.get("menu", 0) or 0),
                    ))
                except (KeyError, TypeError, ValueError):
                    logger.warning("A stored pose could not be read and is left out: %r", raw)
            if poses:
                return poses
        # The first time: the defaults, with what the Admin had set before the list existed.
        payloads = stored_arm.get("payloads") if isinstance(stored_arm.get("payloads"), dict) else {}
        seconds = stored_arm.get("seconds") if isinstance(stored_arm.get("seconds"), dict) else {}
        topics = stored_arm.get("topics") if isinstance(stored_arm.get("topics"), dict) else {}
        # The .env messages count only when they were changed from their own defaults; the hardware's own come first.
        defaults = {k: v for k, v in (("wave", config.arm_wave_payload), (HOME, config.arm_home_payload)) if v != k}
        poses = []
        for pose in ARM_GESTURES:
            message = str(payloads.get(pose.id) or stored_arm.get(pose.id) or defaults.get(pose.id) or "")
            poses.append(replace(
                pose, payload=(pose.payload if not message else "" if message == pose.id else message),
                seconds=self._seconds(seconds.get(pose.id), pose.seconds) if pose.seconds > 0 else 0.0,
                topic=str(topics.get(pose.id) or pose.topic),
            ))
        return poses

    def _pose_index(self, pose_id: str) -> int | None:
        for index, pose in enumerate(self._poses):
            if pose.id == pose_id:
                return index
        return None

    @property
    def poses(self) -> tuple[ArmPose, ...]:
        """The poses in menu order."""
        order = {pose.id: index for index, pose in enumerate(self._poses)}
        return tuple(sorted(self._poses, key=lambda pose: (pose.menu or 10_000, order[pose.id])))

    def pose(self, pose_id: str) -> ArmPose | None:
        index = self._pose_index(pose_id)
        return self._poses[index] if index is not None else None

    @property
    def events(self) -> dict[str, str]:
        """Which pose each event uses: ``intro``, ``rest`` and ``camera_67``."""
        return dict(self._events)

    def set_event(self, event: str, pose_id: str) -> str:
        if event not in DEFAULT_EVENTS or self._pose_index(pose_id) is None:
            return "ไม่มีท่านี้"
        if self._events[event] != pose_id:
            self._events[event] = pose_id
            self._save(KEY_EVENTS, self._events)
            if self._audit is not None:
                self._audit(ACTOR_ADMIN, "hardware.pose_event", f"{event}={pose_id}")
            self.state_changed.emit()
        return ""

    @property
    def gesture_poses(self) -> dict[str, str]:
        """The pose the arm makes for each hand gesture the camera sees ("" = none)."""
        return {code: pose_id for code, pose_id in self._gesture_poses.items() if self._pose_index(pose_id) is not None or not pose_id}

    def pose_for_gesture(self, code: str) -> str:
        """The pose id for a gesture the camera saw, or "" (the 67 gesture follows the event "camera_67")."""
        if code == "Six_Seven":
            return self._events[EVENT_CAMERA_67]
        pose_id = self._gesture_poses.get(code, "")
        return pose_id if self._pose_index(pose_id) is not None else ""

    def set_gesture_pose(self, code: str, pose_id: str) -> str:
        if pose_id and self._pose_index(pose_id) is None:
            return "ไม่มีท่านี้"
        if self._gesture_poses.get(code, "") != pose_id:
            self._gesture_poses[code] = pose_id
            self._save(KEY_GESTURE_POSES, self._gesture_poses)
            if self._audit is not None:
                self._audit(ACTOR_ADMIN, "hardware.gesture_pose", f"{code}={pose_id or '-'}")
            self.state_changed.emit()
        return ""

    def uses_of(self, pose_id: str) -> list[str]:
        """What the pose is tied to now (the events), for the table."""
        uses = [EVENT_NAMES[event] for event, chosen in self._events.items() if chosen == pose_id]
        uses += [f"ท่ามือ {code}" for code, chosen in self._gesture_poses.items() if chosen == pose_id]
        return uses

    def _store_poses(self) -> None:
        self._save(KEY_POSES, [
            {"id": p.id, "title": p.title, "description": p.description, "seconds": p.seconds, "topic": p.topic,
             "payload": p.payload, "phrases": list(p.phrases), "reply": p.reply, "enabled": p.enabled, "menu": p.menu}
            for p in self._poses
        ])
        self.state_changed.emit()

    def pose_refusal(self, pose: ArmPose, original_id: str | None = None) -> str:
        """Why ``pose`` may not be saved ("" if it may)."""
        if not _POSE_ID.fullmatch(pose.id):
            return "รหัสท่าใช้ได้เฉพาะ a-z ตัวเลข และ _ ขึ้นต้นด้วยตัวอักษร ยาวไม่เกิน 30 (เช่น wave_left)"
        if pose.id != original_id and self._pose_index(pose.id) is not None:
            return f"มีท่ารหัส {pose.id} อยู่แล้ว"
        if not pose.title.strip() or len(pose.title) > 40:
            return "ชื่อท่าต้องไม่ว่างและยาวไม่เกิน 40 ตัวอักษร"
        if pose.topic and self._link.topic_refusal(pose.topic):
            return self._link.topic_refusal(pose.topic)
        if len(pose.payload) > 100 or any(ord(char) < 32 for char in pose.payload):
            return "ข้อความที่ส่งต้องเป็นบรรทัดเดียว ยาวไม่เกิน 100 ตัวอักษร"
        if pose.seconds < 0 or pose.seconds > 60:
            return "เวลาทำท่าต้องอยู่ระหว่าง 0 ถึง 60 วินาที"
        if any(not phrase.strip() or len(phrase) > 40 for phrase in pose.phrases):
            return "คำพูดที่เรียกท่าต้องไม่ว่างและยาวไม่เกิน 40 ตัวอักษร"
        if pose.menu < 0 or pose.menu > 999:
            return "ลำดับเมนูต้องเป็นตัวเลข 1 ถึง 999"
        return ""

    def save_pose(self, pose: ArmPose, original_id: str | None = None) -> str:
        """Add a pose, or change the one that was ``original_id``; "" or why not."""
        reason = self.pose_refusal(pose, original_id)
        if reason:
            return reason
        pose = replace(pose, id=pose.id.strip(), title=pose.title.strip(), topic=pose.topic.strip(),
                       payload=pose.payload.strip(), phrases=tuple(p.strip() for p in pose.phrases if p.strip()))
        index = self._pose_index(original_id) if original_id else None
        if index is None:
            self._poses.append(pose)
        else:
            self._poses[index] = pose
            if original_id != pose.id:  # renamed: what was tied to it follows
                for event, chosen in self._events.items():
                    if chosen == original_id:
                        self._events[event] = pose.id
                self._save(KEY_EVENTS, self._events)
        self._store_poses()
        if self._audit is not None:
            self._audit(ACTOR_ADMIN, "hardware.pose_save", f"id={pose.id} topic={pose.topic or '-'} menu={pose.menu}")
        return ""

    def delete_pose(self, pose_id: str) -> str:
        index = self._pose_index(pose_id)
        if index is None:
            return "ไม่มีท่านี้"
        uses = self.uses_of(pose_id)
        if uses:
            return "ลบไม่ได้ ท่านี้ถูกใช้อยู่: " + " / ".join(uses) + " (เปลี่ยนท่าที่ใช้ก่อน)"
        del self._poses[index]
        self._return_timer.stop()
        self._store_poses()
        if self._audit is not None:
            self._audit(ACTOR_ADMIN, "hardware.pose_delete", f"id={pose_id}")
        return ""

    def pose_for_phrase(self, text: str) -> ArmPose | None:
        """The enabled pose a visitor asked for by saying (or typing) just its phrase, else None."""
        from services.arm_phrases import core

        said = core(text)
        if not said:
            return None
        for pose in self._poses:
            if pose.enabled and any(core(phrase) == said for phrase in pose.phrases):
                return pose
        return None

    # --- the arm (what the tests and the old card used: by pose id) ---

    @property
    def arm(self) -> dict[str, str]:
        """The arm's topic, and the message sent for each pose (by pose id)."""
        return {"topic": self._arm_topic, **{pose.id: pose.message for pose in self._poses}}

    @property
    def arm_seconds(self) -> dict[str, float]:
        """How long each pose takes: after that the arm is sent back to rest, if that is switched on."""
        return {pose.id: pose.seconds for pose in self._poses}

    @property
    def arm_topics(self) -> dict[str, str]:
        """The topic of each pose that has one of its own ("" = the arm's topic)."""
        return {pose.id: pose.topic for pose in self._poses}

    def arm_topic_for(self, kind: str) -> str:
        pose = self.pose(kind)
        return (pose.topic if pose else "") or self._arm_topic

    @property
    def arm_auto_return(self) -> bool:
        return self._arm_auto_return

    def set_arm(self, topic: str, payloads: dict[str, str] | None = None, seconds: dict[str, float] | None = None,
                auto_return: bool | None = None, topics: dict[str, str] | None = None) -> str:
        """Use these for the arm from now on (only what is given changes); "" or why not."""
        topic = topic.strip()
        reason = self._link.topic_refusal(topic)
        if reason:
            return reason
        changed = [replace(pose) for pose in self._poses]
        for pose_id, text in (payloads or {}).items():
            index = self._pose_index(pose_id)
            if index is None:
                continue
            text = text.strip()
            if not text or len(text) > 100 or any(ord(char) < 32 for char in text):
                return f"ข้อความของท่า{self._poses[index].title}ต้องเป็นบรรทัดเดียว ยาวไม่เกิน 100 ตัวอักษร และไม่ว่าง"
            changed[index] = replace(changed[index], payload="" if text == pose_id else text)
        for pose_id, value in (seconds or {}).items():
            index = self._pose_index(pose_id)
            if index is not None:
                changed[index] = replace(changed[index], seconds=self._seconds(value, changed[index].seconds))
        for pose_id, own in (topics or {}).items():
            index = self._pose_index(pose_id)
            own = own.strip()
            if index is not None:
                reason = self._link.topic_refusal(own) if own else ""
                if reason:
                    return reason
                changed[index] = replace(changed[index], topic=own)
        new_auto = self._arm_auto_return if auto_return is None else bool(auto_return)
        if (topic, new_auto, changed) != (self._arm_topic, self._arm_auto_return, self._poses):
            self._arm_topic, self._arm_auto_return, self._poses = topic, new_auto, changed
            self._save(KEY_ARM, {"topic": topic, "auto_return": new_auto})
            self._store_poses()
            if self._audit is not None:
                self._audit(ACTOR_ADMIN, "hardware.arm_set", f"topic={topic} auto_return={new_auto}")
        return ""

    def _is_rest(self, kind: str) -> bool:
        return kind == self._events[EVENT_REST]

    def arm_refusal(self, kind: str) -> str:
        """Why the Admin's button for pose ``kind`` would not send now. Same safety rules as a direction."""
        pose = self.pose(kind)
        if pose is None:
            return "ไม่มีท่านี้"
        return (
            self._link.arm_refusal(pose.message, self.arm_topic_for(kind), moving=not self._is_rest(kind))
            or self._area_refusal()
        )

    def send_arm(self, kind: str) -> str:
        """The Admin's button for a pose: "" when sent, else why not (also logged). The arm is sent back to rest by itself
        when the pose should be over, unless that is switched off."""
        pose = self.pose(kind)
        reason = self.arm_refusal(kind)
        if not reason:
            payload, topic = pose.message, self.arm_topic_for(kind)
            try:
                record = self._link.send_arm(payload, topic, moving=not self._is_rest(kind))
            except HardwareLinkError as exc:
                reason = str(exc)
            else:
                self._log("hardware.arm", record, "sent")
                self._schedule_wake()
                self._after_pose(pose)
                return ""
        if self._audit is not None:
            self._audit(ACTOR_ADMIN, "hardware.arm",
                        f"topic={self.arm_topic_for(kind)} payload={pose.message if pose else kind} refused={reason}")
        return reason

    def _after_pose(self, pose: ArmPose) -> None:
        """A pose was sent: the way back to rest is timed (or, if it is the rest pose, cancelled)."""
        if self._is_rest(pose.id):
            self._return_timer.stop()
        elif self._arm_auto_return and pose.seconds > 0:
            self._return_timer.start(int(pose.seconds * 1000))

    @property
    def arm_with_intro(self) -> bool:
        return self._arm_with_intro

    def set_arm_with_intro(self, enabled: bool) -> None:
        if enabled != self._arm_with_intro:
            self._arm_with_intro = enabled
            self._save(KEY_ARM_INTRO, enabled)
            if self._audit is not None:
                self._audit(ACTOR_ADMIN, "hardware.arm_with_intro", "on" if enabled else "off")
            self.state_changed.emit()

    @property
    def arm_by_voice(self) -> bool:
        return self._arm_by_voice

    def set_arm_by_voice(self, enabled: bool) -> None:
        if enabled != self._arm_by_voice:
            self._arm_by_voice = enabled
            self._save(KEY_ARM_VOICE, enabled)
            if self._audit is not None:
                self._audit(ACTOR_ADMIN, "hardware.arm_by_voice", "on" if enabled else "off")
            self.state_changed.emit()

    def perform_gesture(self, kind: str) -> bool:
        """A visitor asked for a pose (said "67", showed it to the camera): send it, and send the arm back to rest when it
        should be over. True if sent. Never raises or waits. Needs the Admin's switch; not held back by the area
        confirmation (the visitor stands close, that is the point)."""
        pose = self.pose(kind)
        if not self._arm_by_voice or pose is None or not pose.enabled:
            reason = "switch off" if not self._arm_by_voice else "pose off or missing"
            logger.warning("Pose %s asked for but not sent: %s", kind, reason)
            if self._audit is not None:
                self._audit(ACTOR_ADMIN, "hardware.arm_voice", f"pose={kind} refused={reason}")
            return False
        if not self._send_arm_quietly(kind, "hardware.arm_voice", moving=not self._is_rest(kind)):
            return False
        self._after_pose(pose)
        return True

    def intro_wave(self) -> bool:
        """The robot starts its introduction: wave. True if the message was sent. Never raises, never waits: the
        speech must not depend on the hardware. Not held back by the Admin's area confirmation or the camera
        (the presenter stands close, that is the point); the Admin's switch is the permission."""
        if not self._arm_with_intro:
            return False
        self._return_timer.stop()  # the end of the speech sends it back, not the timer of a gesture before
        return self._send_arm_quietly(self._events[EVENT_INTRO], "hardware.arm_intro")

    def intro_end(self) -> bool:
        """The introduction is over (or cut short): the arm goes back to rest, if the wave was sent."""
        if not self._arm_raised:
            return False
        self._arm_raised = False
        self._return_timer.stop()
        return self._send_arm_quietly(self._events[EVENT_REST], "hardware.arm_intro", moving=False)

    def _send_arm_home(self) -> None:
        """The pose is over: the arm goes back to rest. Never raises."""
        self._send_arm_quietly(self._events[EVENT_REST], "hardware.arm_return", moving=False)

    def _send_arm_quietly(self, kind: str, action: str, moving: bool = True) -> bool:
        pose = self.pose(kind)
        if pose is None:
            return False
        payload, topic = pose.message, self.arm_topic_for(kind)
        try:
            record = self._link.send_arm(payload, topic, moving=moving)
        except HardwareLinkError as exc:
            logger.warning("Arm message %r not sent: %s", payload, exc)
            if self._audit is not None:
                self._audit(ACTOR_ADMIN, action, f"topic={topic} payload={payload} refused={exc}")
            return False
        except Exception:
            logger.exception("Arm message %r could not be sent", payload)
            return False
        if kind == self._events[EVENT_INTRO] and action == "hardware.arm_intro":
            self._arm_raised = True
        self._log(action, record, "sent", "system")
        return True

    def send_direction(self, direction: str, value: int) -> str:
        """"" when sent, else why not (also logged)."""
        topic = self._topic
        reason = self.direction_refusal(direction, value)
        if not reason:
            try:
                record = self._link.send_direction(direction, value, topic)
            except HardwareLinkError as exc:
                reason = str(exc)
            else:
                self._log("hardware.direction", record, "sent")
                self._remember(direction, value)
                self._schedule_wake()
                return ""
        if self._audit is not None:
            self._audit(ACTOR_ADMIN, "hardware.direction",
                        f"topic={topic} direction={direction} value={value} refused={reason}")
        return reason

    def send_stop(self, actor: str = ACTOR_ADMIN) -> str:
        """STOP to the topic in use, and to the direction topic from .env as well if that is
        another one: a stop must reach the robot even if the wrong topic was chosen."""
        self._cancel_queue()  # whatever was still to be sent is not
        for topic in dict.fromkeys((self._topic, self.config.direction_topic)):
            try:
                record = self._link.send_stop(topic)
            except HardwareLinkError as exc:
                if self._audit is not None:
                    self._audit(actor, "hardware.stop", f"refused={exc}")
                return str(exc)
            self._log("hardware.stop", record, "sent", actor)
        self._schedule_wake()
        return ""

    def start(self) -> None:
        self._link.start()

    def shutdown(self) -> None:
        self._wake.stop()
        self._return_timer.stop()
        self._queue_timer.stop()
        self._link.stop()

    # --- internals ---

    def _load(self, key: str, default):
        if self._store is None:
            return default
        try:
            raw = self._store.get(key)
            return default if raw is None else json.loads(raw)
        except Exception:
            logger.exception("Could not read %s", key)
            return default

    def _save(self, key: str, value) -> None:
        if self._store is None:
            return
        try:
            self._store.set(key, json.dumps(value, ensure_ascii=False))
        except Exception:
            logger.exception("Could not save %s", key)

    def _log(self, action: str, record: SentCommand, result: str, actor: str = ACTOR_ADMIN) -> None:
        logger.info("Hardware command from %s %s: %r on %s", actor, record.kind, record.payload, record.topic)
        if self._audit is not None:
            self._audit(actor, action, f"topic={record.topic} payload={record.payload} result={result}")

    def _schedule_wake(self) -> None:
        """Show again when the pause between commands ends and when the reply window closes."""
        pause = self._link.seconds_until_next_direction()
        if pause > 0:
            QTimer.singleShot(int(pause * 1000) + 50, self, self.state_changed.emit)
        self._wake.start(int(self.config.reply_window_s * 1000) + 50)

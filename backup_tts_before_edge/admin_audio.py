"""Admin page for sound: devices, the robot's voice, and how it listens — with tests for each."""
from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app.content_controller import MAX_MIC_CHANNEL, ContentController, ContentError
from app.speech_controller import SpeechController
from app.tuning import TuningController, TuningError
from app.voice_controller import VoiceController
from audio.microphone import MicrophoneError
from config.settings import Settings
from services.audio_playback_service import PlaybackError
from ui.tuning_widgets import TuningEditors
from ui.widgets import level_to_percent, make_button, make_label, make_section, set_style_property, ThaiLabel

SYSTEM_DEFAULT_LABEL = "ค่าเริ่มต้นของระบบ"
AUTO_MICROPHONE_LABEL = "อัตโนมัติ (ไมโครโฟนตัวแรกที่ใช้งานได้จริง)"
SPEECH_TEST_TEXT = "สวัสดีครับ นี่คือการทดสอบเสียงพูดของหุ่นยนต์ประชาสัมพันธ์"
MIC_TEST_TEXT = "ทดสอบไมโครโฟน 5 วินาที"
_LISTENING_NAMES = [
    "mic_level_threshold", "mic_silence_s", "mic_no_speech_timeout_s", "mic_max_seconds",
    "voice_follow_up",
]


class AudioTab(QWidget):
    def __init__(
        self,
        settings: Settings,
        content: ContentController,
        voice: VoiceController,
        speech: SpeechController,
        tuning: TuningController,
    ) -> None:
        super().__init__()
        self._content = content
        self._voice = voice
        self._speech = speech
        self._tuning = tuning
        self._editors = TuningEditors(["tts_speed", *_LISTENING_NAMES], self)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 8, 0)
        layout.setSpacing(12)
        layout.addWidget(self._build_devices(settings))
        layout.addWidget(self._build_voice())
        layout.addWidget(self._build_listening())

        self._status = make_label("", "message", word_wrap=True)
        buttons = QHBoxLayout()
        buttons.addWidget(make_button("บันทึก", "primary", self._save))
        buttons.addWidget(make_button("ยกเลิกการแก้ไข", "secondary", self.reload))
        buttons.addWidget(make_button("คืนค่าเริ่มต้นจาก .env", "secondary", self._restore_defaults))
        buttons.addStretch(1)
        layout.addLayout(buttons)
        layout.addWidget(self._status)
        layout.addWidget(
            make_label(
                "ค่าที่บันทึกมีผลทันทีโดยไม่ต้องเปิดโปรแกรมใหม่ เสียงที่บันทึกใช้ถอดข้อความในเครื่องเท่านั้น "
                "และไม่ถูกเก็บลงดิสก์",
                "hint", word_wrap=True,
            )
        )
        layout.addStretch(1)

        self._testing = False
        self._test_peak = 0.0
        self._editors.edited.connect(self._show_threshold_check)
        voice.level_changed.connect(self._on_level)
        voice.status_changed.connect(self._show_model_status)
        voice.mic_test_finished.connect(self._on_test_finished)
        speech.status_changed.connect(self._show_model_status)
        self._show_model_status()
        self.reload()

    # --- sections ---

    def _build_devices(self, settings: Settings) -> QWidget:
        section, layout = make_section(
            "อุปกรณ์เสียง",
            "ไมโครโฟนแบบอาร์เรย์ (เช่น ReSpeaker) ส่งเสียงที่ตัดเสียงรบกวนแล้วมาเฉพาะบางช่อง ให้เลือกช่องนั้น "
            "และให้เสียงพูดของหุ่นยนต์ออกทางช่องเสียงออกของไมโครโฟนตัวเดียวกัน เพื่อให้ตัดเสียงสะท้อนได้",
        )
        self._inputs = QComboBox()
        self._channel = QSpinBox()
        self._channel.setRange(0, MAX_MIC_CHANNEL)
        self._outputs = QComboBox()
        self._voice_status = ThaiLabel()
        self._speech_status = ThaiLabel()

        input_row = QHBoxLayout()
        input_row.setSpacing(10)
        input_row.addWidget(self._inputs, 1)
        input_row.addSpacing(8)
        input_row.addWidget(make_label("ช่องสัญญาณ", "field-label"))
        self._channel.setMinimumWidth(96)
        input_row.addWidget(self._channel)
        form = QFormLayout()
        form.setVerticalSpacing(10)
        form.addRow("ไมโครโฟน", input_row)
        form.addRow("ลำโพง", self._outputs)
        form.addRow(
            "การรับเสียงพูด",
            self._with_note(self._voice_status, self._voice.engine_text, keep="engine"),
        )
        form.addRow("เสียงพูดของหุ่นยนต์", self._with_note(self._speech_status, "PyThaiTTS fastthaig2p"))
        layout.addLayout(form)
        layout.addWidget(
            make_label(
                "ไม่ต้องเสียบไมโครโฟนภายนอก: แบบอัตโนมัติจะใช้ไมค์ในเครื่องที่ส่งเสียงได้จริง "
                "ถ้าเสียบไมค์ภายนอก กดโหลดรายการใหม่แล้วเลือกตัวนั้น วงเล็บท้ายชื่อคือช่องทางที่ Windows ใช้ "
                "(WASAPI, MME, DirectSound, WDM-KS) ตัวเดียวกันจึงมีได้หลายรายการ",
                "hint", word_wrap=True,
            )
        )
        row = QHBoxLayout()
        row.addWidget(make_button("โหลดรายการอุปกรณ์ใหม่", "secondary", self._reload_devices_and_check))
        row.addStretch(1)
        layout.addLayout(row)
        return section

    def _build_voice(self) -> QWidget:
        section, layout = make_section("เสียงพูดของหุ่นยนต์")
        layout.addWidget(self._editors.widget("tts_speed"))
        self._preview_text = QLineEdit(SPEECH_TEST_TEXT)
        self._preview_text.setMaxLength(200)
        row = QHBoxLayout()
        row.addWidget(self._preview_text, 1)
        row.addWidget(make_button("ทดลองฟัง", "primary", self._test_speech))
        layout.addWidget(make_label("ข้อความทดสอบ (ฟังด้วยความเร็วที่ตั้งอยู่ ก่อนกดบันทึกก็ได้)", "field-label"))
        layout.addLayout(row)
        return section

    def _build_listening(self) -> QWidget:
        section, layout = make_section("การฟังเสียงพูด")
        layout.addWidget(self._editors.widget("mic_level_threshold"))
        self._level = QProgressBar()
        self._level.setRange(0, 100)
        self._level.setTextVisible(False)
        self._level_text = make_label("กดทดสอบไมโครโฟนแล้วลองพูด เพื่อดูว่าเสียงพูดดังเกินเกณฑ์หรือไม่", "hint")
        self._test_button = make_button(MIC_TEST_TEXT, "secondary", self._toggle_test)
        meter = QHBoxLayout()
        meter.addWidget(self._level, 1)
        meter.addWidget(self._test_button)
        layout.addLayout(meter)
        layout.addWidget(self._level_text)
        for name in _LISTENING_NAMES[1:]:
            layout.addWidget(self._editors.widget(name))
        return section

    def _with_note(self, status: QLabel, note: str, keep: str = "") -> QWidget:
        box = QWidget()
        row = QHBoxLayout(box)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(status)
        label = make_label(note, "hint")
        if keep == "engine":
            self._engine_note = label  # refreshed with the status: the card may be replaced by the processor
        row.addWidget(label, 1)
        return box

    # --- state ---

    def reload(self) -> None:
        """Show the saved values again (drops unsaved edits)."""
        self._reload_devices()
        self._channel.setValue(self._content.mic_channel())
        self._editors.load(self._tuning.values)
        self._show_status("")

    def has_unsaved_changes(self) -> bool:
        return (
            self._editors.differs_from(self._tuning.values)
            or self._inputs.currentData() != self._content.mic_device_name()
            or self._outputs.currentData() != self._content.audio_output_name()
            or self._channel.value() != self._content.mic_channel()
        )

    def discard_changes(self) -> None:
        self.stop_test()
        self.reload()

    def stop_test(self) -> None:
        self._voice.stop_mic_test()

    def _reload_devices(self) -> None:
        try:
            self._fill(
                self._inputs, self._voice.input_devices(), self._content.mic_device_name(),
                AUTO_MICROPHONE_LABEL,
            )
            self._fill(self._outputs, self._speech.output_devices(), self._content.audio_output_name())
        except (MicrophoneError, PlaybackError) as exc:
            self._show_status(str(exc), "error")

    def _reload_devices_and_check(self) -> None:
        """A microphone may have been plugged in or allowed since: test the saved one again."""
        self.stop_test()
        self._reload_devices()
        self._voice.recheck_microphone()

    def _fill(
        self, combo: QComboBox, devices: list, saved: str | None, default_label: str = SYSTEM_DEFAULT_LABEL
    ) -> None:
        combo.clear()
        combo.addItem(default_label, None)
        for device in devices:
            label = f"{device.name}  (ค่าเริ่มต้น)" if device.is_default else device.name
            combo.addItem(label, device.name)
        index = combo.findData(saved) if saved else 0
        if index < 0:
            # Keep the saved name selectable so leaving the page does not count as an edit.
            combo.addItem(f"{saved}  (ไม่พบอุปกรณ์)", saved)
            index = combo.count() - 1
            self._show_status(f"ไม่พบอุปกรณ์ที่บันทึกไว้ ({saved}) ระบบจะใช้ค่าเริ่มต้นแทน", "error")
        combo.setCurrentIndex(index)

    # --- actions ---

    def save_changes(self) -> bool:
        return self._save()

    def _save(self) -> bool:
        try:
            self._content.save_audio_settings(
                self._inputs.currentData(), self._channel.value(), self._outputs.currentData()
            )
            self._tuning.save(self._editors.apply_to(self._tuning.values))
        except (ContentError, TuningError) as exc:
            self._show_status(str(exc), "error")
            return False
        self.stop_test()
        self._voice.recheck_microphone()  # the status shows whether the chosen microphone works
        self._show_status("บันทึกการตั้งค่าเสียงแล้ว มีผลทันที")
        return True

    def _restore_defaults(self) -> None:
        self._tuning.restore_defaults()
        self._editors.load(self._tuning.values)
        self._show_status("ใช้ค่าความเร็วพูดและการฟังจากไฟล์ .env แล้ว")

    def _toggle_test(self) -> None:
        if self._testing:
            self._voice.stop_mic_test()
            return
        try:
            self._voice.start_mic_test(self._inputs.currentData(), self._channel.value())
        except MicrophoneError as exc:
            self._show_status(str(exc), "error")
            return
        self._testing = True
        self._test_peak = 0.0
        self._test_button.setText("หยุดทดสอบ")
        self._show_status("พูดใส่ไมโครโฟน แถบระดับเสียงควรขยับ")

    def _test_speech(self) -> None:
        # Uses the saved speaker, so save first to try a different one.
        text = self._preview_text.text().strip() or SPEECH_TEST_TEXT
        speed = self._editors.apply_to(self._tuning.values).tts_speed
        if self._speech.speak(text, speed=speed):
            self._show_status(f"กำลังพูดด้วยความเร็ว {speed:.2f} เท่า ทางลำโพงที่บันทึกไว้")
        else:
            self._show_status(f"พูดไม่ได้: เสียงพูด{self._speech.status_text}", "error")

    def _threshold(self) -> float:
        return self._editors.apply_to(self._tuning.values).mic_level_threshold

    def _on_test_finished(self) -> None:
        if not self._testing:
            return
        self._testing = False
        self._test_button.setText(MIC_TEST_TEXT)
        self._level.setValue(0)
        threshold = self._threshold()
        summary = f"ระดับเสียงสูงสุดที่วัดได้ {self._test_peak:.3f} (เกณฑ์ที่นับเป็นเสียงพูด {threshold:.3f})"
        if self._test_peak >= threshold:
            self._show_status(f"{summary} — ไมโครโฟนรับเสียงพูดได้")
        else:
            self._show_status(
                f"{summary} — เสียงเบากว่าเกณฑ์ ระบบจะถือว่าไม่มีคนพูด ให้เลื่อนแถบระดับเสียงที่นับเป็น"
                "เสียงพูดลง เพิ่มระดับไมโครโฟนในระบบปฏิบัติการ หรือพูดใกล้ขึ้น",
                "error",
            )

    def _on_level(self, level: float) -> None:
        if self._testing:
            self._test_peak = max(self._test_peak, level)
            self._level.setValue(level_to_percent(level))
            self._show_threshold_check(level)

    def _show_threshold_check(self, level: float | None = None) -> None:
        if not self._testing:
            return
        level = self._test_peak if level is None else level
        threshold = self._threshold()
        heard = "นับเป็นเสียงพูด" if level >= threshold else "เงียบ (ต่ำกว่าเกณฑ์)"
        self._level_text.setText(f"ตอนนี้ {level:.3f} / เกณฑ์ {threshold:.3f} → {heard}")

    def _show_model_status(self) -> None:
        self._voice_status.setText(self._voice.status_text)
        if getattr(self, "_engine_note", None) is not None:
            self._engine_note.setText(self._voice.engine_text)
        self._speech_status.setText(self._speech.status_text)

    def _show_status(self, text: str, level: str = "info") -> None:
        self._status.setText(text)
        set_style_property(self._status, "level", level)

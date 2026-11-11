"""Check stopping on the real speaker: after STOP, does any sound still leave the program?

    .venv313\\Scripts\\python.exe tools\\speech_stop_check.py
    .venv313\\Scripts\\python.exe tools\\speech_stop_check.py --hear    # real speech-to-text for the language cases

Runs the real application, with its real voices and audio stream, on a temporary
database (your data is not touched), with the real STOP buttons. Only the microphone
is scripted, and the AI is a stand-in that can be held "thinking". Every audio block
handed to the driver is counted; after STOP no sound may be handed over, for 6 s.

What this cannot show is the sound in the room: PortAudio already holds up to about
0.2 s when STOP is pressed. That part is for a person to judge, with the speakers on.
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np  # noqa: E402
from PySide6.QtWidgets import QApplication, QPushButton  # noqa: E402

from app.application import Application  # noqa: E402
from app.speech_controller import SpeechStatus  # noqa: E402
from app.state import ConversationState  # noqa: E402
from app.voice_controller import VoiceStatus  # noqa: E402
from audio.text_to_speech import TextToSpeechService, TtsConfig  # noqa: E402
from config.settings import load_settings  # noqa: E402
from services.audio_playback_service import AudioPlaybackService  # noqa: E402
from ui.log_bridge import QtLogHandler  # noqa: E402

LONG_THAI = (
    "วิทยาลัยของเราเปิดสอนหลายแผนกวิชาครับ นักศึกษาใหม่ต้องเตรียมเอกสารมาให้ครบครับ. "
    "ฝ่ายทะเบียนเปิดทำการวันจันทร์ถึงวันศุกร์ครับ. หากมีข้อสงสัยสอบถามเจ้าหน้าที่ได้เลยครับ"
)
LONG_ENGLISH = (
    "The college offers many departments. New students should bring all of their documents. "
    "The registration office is open from Monday to Friday. Please ask our staff if you have questions."
)
QUIET_AFTER_STOP_S = 6.0


class CountingPlayback(AudioPlaybackService):
    """The real service, remembering when it last handed real sound to the driver."""

    def __init__(self) -> None:
        super().__init__()
        self.last_sound = 0.0
        self.opened = 0

    def start(self, sample_rate, device_name=None) -> None:
        self.opened += 1
        super().start(sample_rate, device_name)

    def _on_audio(self, outdata, frames, time_info, status) -> None:
        super()._on_audio(outdata, frames, time_info, status)
        if np.any(outdata):
            self.last_sound = time.monotonic()


class ScriptedMicrophone:
    def __init__(self) -> None:
        self.level, self.peak, self.recording, self.device_label = 0.0, 0.004, False, "scripted"

    def list_input_devices(self):
        return []

    def probe(self, device_name=None, channel=0) -> str:
        return self.device_label

    def start(self, device_name=None, channel=0, fallback=True) -> None:
        self.recording = True

    def stop(self) -> np.ndarray:
        self.recording = False
        return np.zeros(16_000, dtype=np.float32)

    def cancel(self) -> None:
        self.recording = False
        self.level = 0.0


class ScriptedStt:
    def __init__(self) -> None:
        self.text = ""

    def load(self) -> None:
        pass

    def transcribe(self, _audio, _language=None) -> str:
        return self.text


class HeldAi:
    model = "stand-in"

    def __init__(self) -> None:
        self.gate = threading.Event()
        self.gate.set()
        self.entered = threading.Event()

    def generate(self, _instruction, turns, max_output_tokens=400, temperature=0.3):
        self.entered.set()
        self.gate.wait(15.0)
        thai = any("฀" <= c <= "๿" for c in turns[-1].text)
        return LONG_THAI if thai else LONG_ENGLISH


class Check:
    def __init__(self, qt_app, temp: str) -> None:
        self.qt_app = qt_app
        self.microphone, self.stt, self.ai = ScriptedMicrophone(), ScriptedStt(), HeldAi()
        self.playback = CountingPlayback()
        self.tts = TextToSpeechService(TtsConfig(speed=0.9))
        settings = load_settings(environ={
            "DATA_DIR": str(Path(temp) / "data"), "LOG_DIR": str(Path(temp) / "logs"),
            "MEDIA_DIR": str(Path(temp) / "media"), "STT_ENABLED": "true", "TTS_ENABLED": "true",
            "VISION_ENABLED": "false", "VOICE_FOLLOW_UP": "false", "MIC_SILENCE_SECONDS": "0.1",
            "MIC_NO_SPEECH_TIMEOUT_S": "0.8",
        })
        self.app = Application(
            settings, QtLogHandler(), microphone=self.microphone, stt=self.stt,
            tts=self.tts, playback=self.playback, gemini=self.ai,
        )
        self.screen = self.app.window.user_screen
        self.wait(lambda: self.app.speech.status is SpeechStatus.READY, 60)
        self.wait(lambda: self.app.voice.status is VoiceStatus.READY, 10)
        self.failures = 0

    def wait(self, condition, seconds: float = 20.0) -> bool:
        deadline = time.monotonic() + seconds
        while not condition():
            if time.monotonic() > deadline:
                return False
            self.qt_app.processEvents()
            time.sleep(0.005)
        return True

    def pump(self, seconds: float) -> None:
        self.wait(lambda: False, seconds)

    def press(self, text: str) -> None:
        [b for b in self.screen.findChildren(QPushButton) if b.text() == text][0].click()

    def speak_to_robot(self, words: str) -> None:
        self.screen.show_page(self.screen.PAGE_CHAT)
        self.screen._on_talk_button()
        self.stt.text = words
        self.microphone.level = 0.2
        self.pump(0.35)
        self.microphone.level = 0.0

    def report(self, name: str, ok: bool, detail: str) -> None:
        self.failures += not ok
        print(f"{'OK ' if ok else 'BAD'} {name}: {detail}", flush=True)

    def quiet_after(self, name: str, stop) -> None:
        """``stop()``, then watch the speaker for QUIET_AFTER_STOP_S."""
        pressed = time.monotonic()
        stop()
        call_ms = (time.monotonic() - pressed) * 1000
        opened = self.playback.opened
        self.pump(QUIET_AFTER_STOP_S)
        after = self.playback.last_sound - pressed
        # The callback may be inside its last block when stop arrives: within 50 ms is that block.
        sound_after_stop = self.playback.last_sound > pressed + 0.05
        reopened = self.playback.opened != opened
        ok = not sound_after_stop and not reopened and not self.app.speech.is_speaking
        ok = ok and self.app.conversation.state.state is ConversationState.IDLE
        self.report(name, ok, f"stop took {call_ms:.0f} ms; last sound {after * 1000:+.0f} ms from the press; "
                    f"speaker reopened: {reopened}; watched {QUIET_AFTER_STOP_S:.0f} s")

    def run(self) -> int:
        press_stop = lambda: self.press("หยุดสนทนา")  # noqa: E731
        ask = "This question is certainly not in the knowledge base, is it"

        # 10, 11: while it is speaking, each way of stopping.
        for name, stop in (
            ("10/11 ปุ่ม หยุดสนทนา ระหว่างพูด", press_stop),
            ("10/11 ปุ่มแดง หยุดหุ่นยนต์ ระหว่างพูด", lambda: self.press("หยุดหุ่นยนต์")),
            ("12 ปุ่มหน้าหลัก ระหว่างพูด", lambda: self.press("หน้าหลัก")),
            ("12 เข้าหน้า Admin ระหว่างพูด", self.app.window.enter_admin),
        ):
            self.app.window.leave_admin()
            self.playback.last_sound = 0.0
            self.speak_to_robot(ask)
            heard = self.wait(lambda: self.playback.last_sound > 0)
            self.pump(0.8)
            if not heard:
                self.report(name, False, "never started speaking")
                continue
            self.quiet_after(name, stop)
        self.app.window.leave_admin()

        # 14: while the AI is making the answer.
        self.ai.gate.clear()
        self.ai.entered.clear()
        self.playback.last_sound = 0.0
        self.speak_to_robot(ask + " two")
        self.wait(self.ai.entered.is_set)
        opened = self.playback.opened
        press_stop()
        self.ai.gate.set()
        self.pump(QUIET_AFTER_STOP_S)
        self.report("14 STOP ตอน AI กำลังสร้างคำตอบ", self.playback.last_sound == 0.0 and self.playback.opened == opened,
                    f"sound after: {self.playback.last_sound != 0.0}; speaker opened: {self.playback.opened != opened}")

        # 14: while the voice is making the audio.
        self.playback.last_sound = 0.0
        self.speak_to_robot(ask + " three")
        self.wait(lambda: self.app.speech.is_speaking)
        made_at = time.monotonic()
        press_stop()
        self.pump(QUIET_AFTER_STOP_S)
        self.report("14 STOP ตอนกำลังสร้างเสียง", self.playback.last_sound < made_at + 0.05,
                    f"last sound {max(0.0, self.playback.last_sound - made_at) * 1000:.0f} ms after the press")

        # 13: a new session right after a stopped one.
        self.ai.gate.clear()
        self.speak_to_robot(ask + " four")
        self.pump(0.3)
        press_stop()
        self.ai.gate.set()
        self.playback.last_sound = 0.0
        self.speak_to_robot("เรื่องนี้ไม่มีในฐานความรู้แน่นอนครับ")
        self.wait(lambda: self.playback.last_sound > 0)
        spoken = [t for t in self.screen.transcript_texts() if t.startswith("หุ่นยนต์")]
        self.pump(0.5)
        press_stop()
        self.report("13 Session ใหม่ไม่มีเสียงเก่า", bool(spoken) and LONG_ENGLISH not in spoken[-1],
                    f"last answer on screen: {spoken[-1][:40] if spoken else None}")

        # 5, 7: nothing pressed.
        self.playback.last_sound = 0.0
        opened = self.playback.opened
        self.press("สนทนา")
        self.pump(2.0)
        self.app.window.enter_admin()
        self.pump(2.0)
        self.app.window.leave_admin()
        self.report("5/7 เปิด Robot Screen และ Dashboard โดยไม่กดเริ่ม", self.playback.opened == opened,
                    f"speaker opened: {self.playback.opened != opened}")

        # 6-style: typed on the robot screen.
        self.screen.show_page(self.screen.PAGE_CHAT)
        self.screen._input.setText("Hello")
        self.screen._send_typed_question()
        self.pump(2.0)
        self.report("1 พิมพ์ Hello: คำตอบอังกฤษบนจอ ไม่มีเสียง",
                    self.playback.opened == opened and self.screen.transcript_texts()[-1].startswith("หุ่นยนต์: Hello"),
                    self.screen.transcript_texts()[-1])

        self.app.shutdown()
        print(f"\n{'ALL OK' if not self.failures else f'{self.failures} PROBLEM(S)'}")
        return 1 if self.failures else 0


def hear_language_cases() -> int:
    """Real speech-to-text on real synthesised speech, into the real language routing."""
    from audio.speech_to_text import SpeechToTextService, SttConfig
    from audio.text_to_speech import speech_pieces
    from services.conversation_service import conversation_language

    settings = load_settings()
    stt = SpeechToTextService(SttConfig(
        model=settings.stt_model, device=settings.stt_device, compute_type=settings.stt_compute_type,
        language=settings.stt_language, model_dir=settings.model_dir,
    ))
    stt.load()
    tts = TextToSpeechService(TtsConfig(speed=0.9))
    tts.load()
    failures = 0
    for said, expected in (
        ("Hello", "en"), ("What time does the college open?", "en"),
        ("วิทยาลัยเปิดกี่โมงครับ", "th"), ("สมัครเรียนได้ที่ไหนครับ", "th"),
        ("Where can I apply?", "en"), ("สวัสดีครับ", "th"),
    ):
        audio = tts.synthesize(said)
        at_16k = np.interp(np.arange(0, len(audio), 1.5), np.arange(len(audio)), audio).astype(np.float32)
        padded = np.concatenate((np.zeros(4800, np.float32), at_16k, np.zeros(9600, np.float32)))
        heard = stt.transcribe(padded)
        language = conversation_language(heard)
        ok = language == expected
        failures += not ok
        voices = "+".join(lang for _text, lang in speech_pieces(heard))
        print(f"{'OK ' if ok else 'BAD'} said {said!r} -> heard {heard!r} -> {language} (voice {voices})", flush=True)
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description="Check stopping on the real speaker")
    parser.add_argument("--hear", action="store_true", help="only the language cases, with real speech-to-text")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    if args.hear:
        return 1 if hear_language_cases() else 0
    qt_app = QApplication([])
    with tempfile.TemporaryDirectory() as temp:
        return Check(qt_app, temp).run()


if __name__ == "__main__":
    raise SystemExit(main())

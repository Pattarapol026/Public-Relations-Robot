"""Where the speech models run: the graphics card when it can be used, the processor otherwise.

Measured on the real laptop (7 Nov 2026): the Thai medium model takes 0.5-1 s on the NVIDIA card and
5.4 s on the processor. These tests check the choices around it with a fake faster-whisper: which
device and precision are used, that a card failure moves *all* models to the processor (the Thai model
is never swapped for the general one), and that only "not on disk" leads to a download.
"""
from __future__ import annotations

import sys
import types

import numpy as np
import pytest

from audio import speech_to_text as stt


class FakeWhisper:
    created: list[tuple[str, str, str]] = []  # (path, device, compute_type)
    fail_on: tuple[str, str] | None = None  # (path fragment, device) that raises
    beams: list[int] = []

    def __init__(self, path, device="cpu", compute_type="int8", **_kw):
        if self.fail_on and self.fail_on[0] in path and self.fail_on[1] == device:
            raise RuntimeError("CUDA out of memory")
        self.path = path
        FakeWhisper.created.append((path, device, compute_type))

    def transcribe(self, audio, **kw):
        FakeWhisper.beams.append(kw["beam_size"])
        return iter(()), None

    def detect_language(self, audio):
        return "th", 0.9, [("th", 0.9), ("en", 0.01)]


@pytest.fixture
def fake_faster_whisper(monkeypatch):
    FakeWhisper.created, FakeWhisper.fail_on, FakeWhisper.beams = [], None, []
    downloads: list[bool] = []
    module = types.ModuleType("faster_whisper")
    module.WhisperModel = FakeWhisper
    utils = types.ModuleType("faster_whisper.utils")

    def download_model(name, local_files_only=False, cache_dir=None, revision=None, **_kw):
        downloads.append(local_files_only)
        if local_files_only and name == "not-on-disk":
            raise FileNotFoundError(name)
        return f"/models/{name}"

    utils.download_model = download_model
    module.utils = utils
    monkeypatch.setitem(sys.modules, "faster_whisper", module)
    monkeypatch.setitem(sys.modules, "faster_whisper.utils", utils)
    monkeypatch.setattr(stt, "register_cuda_libraries", lambda: [])
    FakeWhisper.downloads = downloads
    return FakeWhisper


def config(**changes):
    base = dict(model="small", device="cuda", compute_type="float16", language="th",
                thai_model="owner/thai-medium", detector_model="tiny")
    return stt.SttConfig(**{**base, **changes})


def test_all_three_models_go_to_the_card_when_it_can_be_used(fake_faster_whisper, monkeypatch):
    monkeypatch.setattr(stt, "cuda_available", lambda: True)
    service = stt.SpeechToTextService(config())
    service.load()
    assert service.device == "cuda"
    assert [(p.split("/")[-1], d, c) for p, d, c in fake_faster_whisper.created] == [
        ("small", "cuda", "float16"), ("thai-medium", "cuda", "float16"), ("tiny", "cuda", "float16"),
    ]
    assert "การ์ดจอ" in service.engine_text


def test_without_a_usable_card_the_processor_is_used_with_a_precision_it_has(fake_faster_whisper, monkeypatch):
    monkeypatch.setattr(stt, "cuda_available", lambda: False)
    service = stt.SpeechToTextService(config())
    service.load()
    assert service.device == "cpu"
    assert {(d, c) for _p, d, c in fake_faster_whisper.created} == {("cpu", "int8")}  # float16 does not exist there
    assert "ซีพียู" in service.engine_text


def test_a_processor_setting_never_touches_the_card(fake_faster_whisper, monkeypatch):
    monkeypatch.setattr(stt, "cuda_available", lambda: True)
    service = stt.SpeechToTextService(config(device="cpu", compute_type="int8"))
    service.load()
    assert {d for _p, d, _c in fake_faster_whisper.created} == {"cpu"}


def test_a_card_failure_moves_every_model_to_the_processor_and_keeps_the_thai_model(fake_faster_whisper, monkeypatch):
    monkeypatch.setattr(stt, "cuda_available", lambda: True)
    fake_faster_whisper.fail_on = ("thai-medium", "cuda")  # out of video memory on the big model
    service = stt.SpeechToTextService(config())
    service.load()
    assert service.device == "cpu" and service.uses_thai_model
    on_cpu = [(p.split("/")[-1], d, c) for p, d, c in fake_faster_whisper.created if d == "cpu"]
    assert on_cpu == [("small", "cpu", "int8"), ("thai-medium", "cpu", "int8"), ("tiny", "cpu", "int8")]


def test_the_thai_model_decodes_wider_on_the_card_and_greedily_on_the_processor(fake_faster_whisper, monkeypatch):
    audio = np.zeros(16_000, dtype=np.float32)
    for available, expected in ((True, stt.GPU_THAI_BEAM), (False, stt.CPU_THAI_BEAM)):
        fake_faster_whisper.beams = []
        monkeypatch.setattr(stt, "cuda_available", lambda available=available: available)
        service = stt.SpeechToTextService(config())
        service.load()
        fake_faster_whisper.beams = []  # the warm-up decodes too
        service._transcribe_with_thai_model(service._model, audio, "th")
        assert fake_faster_whisper.beams == [expected]


def test_only_a_missing_model_is_downloaded_never_a_failed_load(fake_faster_whisper, monkeypatch):
    monkeypatch.setattr(stt, "cuda_available", lambda: False)
    service = stt.SpeechToTextService(config(model="not-on-disk", thai_model="", device="cpu", compute_type="int8"))
    service.load()
    assert fake_faster_whisper.downloads[:2] == [True, False]  # looked on disk first, then fetched
    fake_faster_whisper.downloads.clear()
    fake_faster_whisper.fail_on = ("small", "cpu")  # present on disk, but it cannot be opened
    broken = stt.SpeechToTextService(config(thai_model="", device="cpu", compute_type="int8"))
    with pytest.raises(stt.SpeechToTextError):
        broken.load()
    assert fake_faster_whisper.downloads == [True]  # no network fetch for a load error


# --- how sure the model was, the opt-in recordings, and the opt-in speech prompt ---

class _Segment:
    def __init__(self, text, logprob=-0.2, no_speech=0.05):
        self.text, self.avg_logprob, self.no_speech_prob = text, logprob, no_speech


def _service_saying(monkeypatch, fake, words, **changes):
    seen = {}

    def transcribe(self, audio, **kw):
        seen.update(kw)
        return iter([_Segment(words)] if words else []), None

    monkeypatch.setattr(fake, "transcribe", transcribe)
    monkeypatch.setattr(stt, "cuda_available", lambda: True)
    service = stt.SpeechToTextService(config(**changes))
    service.load()
    return service, seen


def test_the_confidence_of_a_turn_is_logged_and_the_text_is_unchanged(fake_faster_whisper, monkeypatch, caplog):
    service, _ = _service_saying(monkeypatch, fake_faster_whisper, "สวัสดี")
    audio = np.full(16_000, 0.1, dtype=np.float32)
    with caplog.at_level("INFO"):
        assert service.transcribe(audio, "th") == "สวัสดี"
    assert "lowest logprob -0.20" in caplog.text


def test_turn_audio_is_kept_only_when_asked_for(fake_faster_whisper, monkeypatch, tmp_path):
    audio = np.full(16_000, 0.1, dtype=np.float32)
    service, _ = _service_saying(monkeypatch, fake_faster_whisper, "สวัสดี")
    service.transcribe(audio, "th")
    assert not list(tmp_path.iterdir())
    service, _ = _service_saying(monkeypatch, fake_faster_whisper, "สวัสดี", save_audio_dir=tmp_path / "turns")
    service.transcribe(audio, "th")
    saved = sorted(path.suffix for path in (tmp_path / "turns").iterdir())
    assert saved == [".txt", ".wav"]


def test_without_a_prompt_nothing_extra_is_given_to_the_model(fake_faster_whisper, monkeypatch):
    service, seen = _service_saying(monkeypatch, fake_faster_whisper, "สวัสดี")
    service.transcribe(np.full(16_000, 0.1, dtype=np.float32), "th")
    assert "initial_prompt" not in seen


def test_a_prompt_reaches_the_thai_model_and_its_echo_is_dropped(fake_faster_whisper, monkeypatch):
    prompt = "หุ่นยนต์ไอร่า วิทยาลัยเทคนิคสัตหีบ การประกวดสิ่งประดิษฐ์"
    service, seen = _service_saying(monkeypatch, fake_faster_whisper, "วิทยาลัยเทคนิคสัตหีบ", thai_prompt=prompt)
    assert service.transcribe(np.full(16_000, 0.1, dtype=np.float32), "th") == ""  # the model repeated the prompt
    assert seen["initial_prompt"] == prompt
    service, _ = _service_saying(monkeypatch, fake_faster_whisper, "ห้องน้ำอยู่ที่ไหน", thai_prompt=prompt)
    assert service.transcribe(np.full(16_000, 0.1, dtype=np.float32), "th") == "ห้องน้ำอยู่ที่ไหน"


@pytest.mark.parametrize("heard, right", [
    ("แนะนำสิ่งผลิตหน่อย", "แนะนำสิ่งประดิษฐ์หน่อย"),
    ("สิ่งประดิษฐ์ที่เจ็ดคืออะไร", "สิ่งประดิษฐ์ที่เจ็ดคืออะไร"),  # already right: untouched
    ("สิ่งประดิดที่เจ็ด", "สิ่งประดิษฐ์ที่เจ็ด"),
    ("สิ่งประดิษที่ 7", "สิ่งประดิษฐ์ที่ 7"),
    ("ไอรา สวัสดี", "ไอร่า สวัสดี"),
    ("ไอล่าอยู่ไหน", "ไอร่าอยู่ไหน"),
    ("ไอร่าอยู่ไหน", "ไอร่าอยู่ไหน"),
    ("ผลิตภัณฑ์อาหาร", "ผลิตภัณฑ์อาหาร"),  # a real word that starts alike
    ("ประดิษฐ์ครับ", "ประดิษฐ์ครับ"),
])
def test_words_of_the_event_the_thai_model_gets_wrong_are_corrected(heard, right):
    assert stt.clean_transcript(heard) == right

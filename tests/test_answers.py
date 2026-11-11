"""Phase 2 answer quality and speed: retrieval on real visitor questions, follow-ups,
answer language, the answer cache, knowledge-base failure, and the Gemini time budget."""
from __future__ import annotations

import time
from dataclasses import replace

import httpx
import pytest

from ai import knowledge_service
from ai.gemini_client import ChatTurn, GeminiClient, GeminiError
from database.models import KnowledgeItem
from services.conversation_service import (
    ConversationService,
    Exchange,
    conversation_language,
    is_follow_up,
)


def item(item_id, question, answer, keywords=()) -> KnowledgeItem:
    return KnowledgeItem(item_id, "FAQ", question, answer, tuple(keywords), True, True)


# The four entries on the robot, word for word.
ROBOT_KB = [
    item(1, "เวลาทำการของวิทยาลัย", "วิทยาลัยเปิดทำการวันจันทร์ถึงวันศุกร์ เวลา 08.00 ถึง 16.30 น. ครับ",
         ["เวลา", "ทำการ", "เปิด", "ปิด", "กี่โมง"]),
    item(2, "มีแผนกวิชาอะไรบ้าง", "มีแผนกช่างยนต์ ช่างไฟฟ้า อิเล็กทรอนิกส์ เทคโนโลยีสารสนเทศ และการบัญชีครับ",
         ["แผนก", "สาขา", "หลักสูตร", "เรียนอะไร"]),
    item(3, "สมัครเรียนได้ที่ไหน", "สมัครเรียนได้ที่งานทะเบียน อาคารอำนวยการ ชั้น 1 ครับ",
         ["สมัคร", "รับสมัคร", "ทะเบียน", "ลงทะเบียน"]),
    item(4, "ติดต่อฝ่ายประชาสัมพันธ์อย่างไร", "ติดต่อฝ่ายประชาสัมพันธ์ได้ที่เคาน์เตอร์ด้านหน้าอาคารอำนวยการครับ",
         ["ติดต่อ", "ประชาสัมพันธ์", "เบอร์", "โทร"]),
]


# --- retrieval: questions as visitors really asked them (speech-to-text errors kept) ---


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("มีภาแน่วิชาอะไรบ้าน", 2),
        ("เอ่อ ถามอะไร มีภาแน่วิชาอะไรบ้าน", 2),
        ("พี่จังหวานนุกพูดพอดี มีภาแนกวิชาอะไรบ้าง", 2),
        ("สมักเรียนได้ที่ไหนบ้าง", 3),
        ("จะติดต่อประชาสัมพันได้ยังไง", 4),
        ("ที่นี่มีสาขาอะไรให้เรียนบ้าง", 2),
        ("Where can I take my son for registration for this college?", 3),
        ("What departments do you have?", 2),
        ("How can I contact public relations?", 4),
    ],
)
def test_real_and_reworded_questions_find_their_entry(question, expected):
    match = knowledge_service.confident_match(question, ROBOT_KB)
    assert match is not None and match.id == expected


@pytest.mark.parametrize(
    "question",
    [
        "ห้องน้ำไปทางไหนครับ",  # not in the knowledge base
        "Excuse me, where can I find a restroom?",
        "ค่าเทอมเท่าไหร่ครับ",
        "ห้องสมุดเปิดกี่โมง",  # the college's hours are not the library's
        "สมัครงานที่ไหนครับ",  # applying for a job is not applying to study
        "แผนกบัญชีอยู่ตึกไหน",  # the list of departments does not say where one is
        "ต้องใช้เอกสารสมัครเรียนอะไรบ้าง",  # where to apply does not say which documents
        "When is the exam?",
        "มีบริการนวดไหม",  # shares only "บริการ...ไหม" with an entry (a live wrong answer, now fixed)
        "เช็กอินกี่โมง",  # shares only "กี่โมง" with the college's hours
        # Garbled "วิทยาลัย": its subject cannot be told from an unknown one, so it is not
        # answered offline (it still goes to the AI when online). A wrong answer is worse.
        "วิเชียไหล เปิดปิดกี่โมง",
    ],
)
def test_a_close_but_different_entry_is_never_given_as_the_answer(question):
    assert knowledge_service.confident_match(question, ROBOT_KB) is None


def test_polite_particles_do_not_stop_an_exact_match():
    assert knowledge_service.find_exact("เวลาทำการของวิทยาลัยครับ", ROBOT_KB).id == 1
    assert knowledge_service.find_exact("เวลาทำการ", ROBOT_KB) is None


# --- follow-ups and answer language ---


def test_follow_up_questions_are_recognised():
    earlier = [Exchange("ต้องใช้เอกสารอะไรบ้าง", "...", "th")]
    assert is_follow_up("แล้วถ้าเป็นนักศึกษาใหม่ล่ะ", earlier)
    assert is_follow_up("and the fees?", earlier)
    assert not is_follow_up("แล้วถ้าเป็นนักศึกษาใหม่ล่ะ", [])
    assert not is_follow_up("ขอทราบเวลาทำการของวิทยาลัยหน่อยครับ", earlier)


def test_a_follow_up_is_answered_with_the_entry_for_the_whole_conversation():
    knowledge = [item(1, "สมัครเรียนได้ที่ไหน", "ที่งานทะเบียน อาคาร 1", ["สมัคร"])]
    service = ConversationService(lambda: knowledge)  # no AI: the knowledge base alone
    history = [Exchange("สมัครเรียนได้ที่ไหน", "ที่งานทะเบียน อาคาร 1", "th")]
    assert service.respond("แล้วรับสมัครถึงกี่โมง").source == "fallback"  # standing alone: no hours entry
    assert service.respond("ตรงไหนนะ", history).answer_text == "ที่งานทะเบียน อาคาร 1"


def test_an_acronym_does_not_switch_a_thai_conversation_to_english():
    thai = [Exchange("มีแผนกอะไรบ้าง", "...", "th")]
    assert conversation_language("IT", thai) == "th"
    assert conversation_language("TOEIC?", thai) == "th"
    assert conversation_language("What about the IT department?", thai) == "en"
    assert conversation_language("IT", []) == "en"


@pytest.mark.parametrize("text", ["Hello", "hello", "HELLO", "Hi", "Thank you", "ok thanks", "Good morning"])
def test_short_english_after_thai_is_english(text):
    assert conversation_language(text, [Exchange("สวัสดีครับ", "สวัสดีครับ", "th")]) == "en"


@pytest.mark.parametrize("greeting", ["Hello", "Hi", "hi there", "Good morning!", "Hey"])
def test_an_english_greeting_gets_english_with_or_without_the_ai(greeting):
    thai = (Exchange("สวัสดีครับ", "สวัสดีครับ", "th"),)
    gemini = FakeGemini()
    for service in (ConversationService(lambda: ROBOT_KB), ConversationService(lambda: ROBOT_KB, gemini=gemini)):
        for history in ((), thai):
            result = service.respond(greeting, history)
            assert result.language == "en" and result.answer_text == "Hello! How can I help you today?"
    assert gemini.calls == []  # no need to spend a request on it


def test_alternating_languages_answers_each_question_in_its_own_language():
    entry = replace(item(1, "เวลาทำการ", "เปิดแปดโมงครับ"), question_en="Opening hours", answer_en="We open at eight.")
    service = ConversationService(lambda: [entry])
    history = []
    for question, answer, language in (
        ("เวลาทำการ", "เปิดแปดโมงครับ", "th"), ("Opening hours", "We open at eight.", "en"),
        ("เวลาทำการ", "เปิดแปดโมงครับ", "th"), ("Opening hours", "We open at eight.", "en"),
    ):
        result = service.respond(question, tuple(history))
        assert (result.answer_text, result.language) == (answer, language)
        history.append(Exchange(question, result.answer_text, result.language))


def test_an_entry_without_english_is_translated_by_the_ai_or_not_read_in_thai():
    entry = replace(item(1, "เวลาทำการ", "เปิดแปดโมงครับ"), question_en="Opening hours")
    assert ConversationService(lambda: [entry]).respond("Opening hours").source == "fallback"
    gemini = FakeGemini("We open at eight.")
    result = ConversationService(lambda: [entry], gemini=gemini).respond("Opening hours")
    assert result.answer_text == "We open at eight." and len(gemini.calls) == 1


@pytest.mark.parametrize("question, reply, expected", [
    ("Where is the library located exactly?", "ห้องสมุดอยู่ชั้นสองครับ", "I'm sorry"),
    ("ห้องสมุดอยู่ที่ไหนครับ", "The library is on the second floor.", "ขออภัย"),
])
def test_a_reply_from_the_ai_in_the_wrong_language_is_not_used(question, reply, expected):
    result = ConversationService(lambda: ROBOT_KB, gemini=FakeGemini(reply)).respond(question)
    assert reply not in result.answer_text and expected in result.answer_text
    assert result.ai_error == "wrong_language"


def test_the_last_line_of_the_instruction_repeats_the_language():
    service = ConversationService(lambda: ROBOT_KB)
    assert service.build_instruction("Where do I apply?", ROBOT_KB).endswith("entirely in English, even though the rules and data above are in Thai.")
    assert service.build_instruction("สมัครที่ไหน", ROBOT_KB).endswith("เป็นภาษาไทยทั้งหมด")


class FakeGemini:
    model = "fake"

    def __init__(self, reply="คำตอบ", error=None):
        self.reply, self.error, self.calls = reply, error, []

    def generate(self, system_instruction, turns, max_output_tokens=400, temperature=0.3):
        self.calls.append((system_instruction, list(turns)))
        if self.error:
            raise self.error
        return self.reply


def test_the_model_is_told_which_language_to_answer_in():
    gemini = FakeGemini()
    service = ConversationService(lambda: ROBOT_KB, gemini=gemini)
    service.respond("Where do I apply?")
    service.respond("สมัครที่ไหนครับ ช่วยบอกหน่อย")
    english, thai = gemini.calls[0][0], gemini.calls[1][0]
    assert "ตอบเป็นภาษาอังกฤษ" in english and "ตอบเป็นภาษาไทย" in thai
    assert "ยังไม่มีข้อมูลเรื่องนั้น" in thai and "ถามกลับ" in thai


# --- speed: the same question again, and nothing to wait for when the AI is down ---


def test_the_same_question_is_answered_again_without_asking_the_model():
    gemini = FakeGemini("วิทยาลัยมีห้าแผนกครับ")
    knowledge = list(ROBOT_KB)
    service = ConversationService(lambda: knowledge, gemini=gemini)
    first = service.respond("ที่นี่สอนอะไรบ้าง")
    again = service.respond("  ที่นี่สอนอะไรบ้าง? ")
    assert first.answer_text == again.answer_text and len(gemini.calls) == 1
    service.respond("ที่นี่สอนอะไรบ้าง", [Exchange("สวัสดี", "สวัสดีครับ", "th")])
    assert len(gemini.calls) == 2  # in a conversation the earlier turns matter: asked again
    knowledge.append(item(9, "ประกาศใหม่", "ข้อมูลใหม่"))
    service.respond("ที่นี่สอนอะไรบ้าง")
    assert len(gemini.calls) == 3  # the knowledge changed: asked again


def test_a_knowledge_base_failure_gives_a_polite_answer_not_an_error():
    def broken():
        raise RuntimeError("database is locked")

    gemini = FakeGemini()
    result = ConversationService(broken, gemini=gemini).respond("สมัครเรียนที่ไหน")
    assert result.source == "fallback" and gemini.calls == []  # no facts to give the model


class _SlowModels:
    def __init__(self, seconds):
        self.seconds, self.requests = seconds, 0

    def generate_content(self, model, contents, config):
        self.requests += 1
        time.sleep(self.seconds)  # a request the SDK's own timeout does not stop


def test_the_time_budget_holds_even_when_the_request_itself_hangs(monkeypatch):
    client = GeminiClient("test-key", "test-model", timeout_s=1.0)
    models = _SlowModels(5.0)
    monkeypatch.setattr(client, "_get_client", lambda: type("C", (), {"models": models})())
    started = time.monotonic()
    with pytest.raises(GeminiError) as caught:
        client.generate("กติกา", [ChatTurn("user", "ถาม")])
    # under 2 s, not the 5 s the request hangs (1.5 s was missed once on a busy machine)
    assert caught.value.kind == "timeout" and time.monotonic() - started < 2.0


def test_a_first_try_that_hangs_is_replaced_by_a_fresh_one(monkeypatch):
    client = GeminiClient("test-key", "test-model", timeout_s=3.0)
    monkeypatch.setattr("ai.gemini_client.FIRST_ATTEMPT_S", 0.3)
    monkeypatch.setattr("ai.gemini_client.RETRY_PAUSE_S", 0.01)
    replies = iter([None, "ตอบได้แล้วครับ"])  # None: this request never answers in time

    class Models:
        def generate_content(self, model, contents, config):
            reply = next(replies)
            if reply is None:
                time.sleep(5)
            return type("R", (), {"text": reply})()

    monkeypatch.setattr(client, "_get_client", lambda: type("C", (), {"models": Models()})())
    started = time.monotonic()
    assert client.generate("กติกา", [ChatTurn("user", "ถาม")]) == "ตอบได้แล้วครับ"
    assert time.monotonic() - started < 1.0


def test_a_slow_first_try_still_counts_after_the_second_is_sent(monkeypatch):
    """The first request is not dropped: if it answers first, its answer is used."""
    client = GeminiClient("test-key", "test-model", timeout_s=3.0)
    monkeypatch.setattr("ai.gemini_client.FIRST_ATTEMPT_S", 0.2)
    delays = iter([0.5, 5.0])  # the first is slow, the second slower

    class Models:
        def generate_content(self, model, contents, config):
            delay = next(delays)
            time.sleep(delay)
            return type("R", (), {"text": f"ตอบหลัง {delay} วินาที"})()

    monkeypatch.setattr(client, "_get_client", lambda: type("C", (), {"models": Models()})())
    started = time.monotonic()
    assert client.generate("กติกา", [ChatTurn("user", "ถาม")]) == "ตอบหลัง 0.5 วินาที"
    assert time.monotonic() - started < 1.0


def test_one_timeout_is_not_an_outage_but_two_in_a_row_are(monkeypatch):
    client = GeminiClient("test-key", "test-model", timeout_s=0.8, max_retries=0)
    replies = iter([None, "ตอบได้", None, None, "ไม่ควรถึง"])  # None: never answers in time

    class Models:
        def generate_content(self, model, contents, config):
            reply = next(replies)
            if reply is None:
                time.sleep(3)
            return type("R", (), {"text": reply})()

    monkeypatch.setattr(client, "_get_client", lambda: type("C", (), {"models": Models()})())
    ask = lambda: client.generate("กติกา", [ChatTurn("user", "ถาม")])  # noqa: E731
    with pytest.raises(GeminiError):
        ask()
    assert ask() == "ตอบได้"  # asked again at once, not skipped for 20 s
    for _ in range(2):
        with pytest.raises(GeminiError):
            ask()
    started = time.monotonic()
    with pytest.raises(GeminiError) as skipped:
        ask()
    assert skipped.value.kind == "network" and time.monotonic() - started < 0.1  # now it waits a while


def test_after_a_network_failure_the_next_questions_do_not_wait_again(monkeypatch):
    client = GeminiClient("test-key", "test-model", timeout_s=2.0, max_retries=0)
    calls = []

    class Models:
        def generate_content(self, model, contents, config):
            calls.append(1)
            raise httpx.ConnectError("no route")

    monkeypatch.setattr(client, "_get_client", lambda: type("C", (), {"models": Models()})())
    with pytest.raises(GeminiError):
        client.generate("กติกา", [ChatTurn("user", "ถาม")])
    with pytest.raises(GeminiError) as skipped:
        client.generate("กติกา", [ChatTurn("user", "ถาม")])
    assert skipped.value.kind == "network" and len(calls) == 1  # answered at once, no request
    client.reset_backoff()  # the Admin's "test AI" tries again for real
    with pytest.raises(GeminiError):
        client.generate("กติกา", [ChatTurn("user", "ถาม")])
    assert len(calls) == 2

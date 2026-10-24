"""Renters in any language (A-04): the results text is translated with every number checked. A previous
call's language is not appended and is not a transcription hint (#144). No network: the text model is a
fake."""
import asyncio
from datetime import UTC, datetime

import pytest

from landline.agents.concierge.prompt import (
    CALL_CONNECTED,
    FIRST_CALL,
    INSTRUCTIONS,
    language_name,
    primary,
)
from landline.config import Settings
from landline.contracts import (
    CallOutcome,
    CallsRequested,
    Listing,
    PreferencesUpdated,
    SessionEnded,
    SessionStarted,
)
from landline.contracts.tools import PreferenceDelta
from landline.db.catalogue import Catalogue
from landline.orchestrator import SessionRuntime
from landline.voice import bridge
from landline.voice.dispatch import CallResult, Dispatcher
from landline.voice.higgs import session_config
from landline.voice.translate import SYSTEM, Translator, keeps_numbers, numbers

NOW = datetime(2026, 10, 24, 15, 0, tzinfo=UTC)   # Saturday 11:00 in Toronto: inside business hours
SID = "s_lang000001"
RESULTS = "I booked 412 Markham St for Saturday at 2 pm. At 88 Erskine Ave, parking: $180 a month."
SPANISH = "Reservé 412 Markham St para el sábado a las 2 pm. En 88 Erskine Ave, estacionamiento: $180 al mes."


class FakeChat:
    """Stands in for agents.auditor.llm.Chat: answers with `text`, or raises, and records the asks."""

    def __init__(self, text: str | None = None, error: Exception | None = None, delay_s: float = 0):
        self.text, self.error, self.delay_s = text, error, delay_s
        self.asked: list[tuple[str, str]] = []

    async def json(self, system, user, schema, name="result"):
        self.asked.append((system, user))
        await asyncio.sleep(self.delay_s)
        if self.error:
            raise self.error
        return {"text": self.text}


# The translation


async def test_a_translation_that_keeps_every_number_is_used():
    chat = FakeChat(SPANISH)
    assert await Translator(chat)(RESULTS, "es") == SPANISH
    [(system, user)] = chat.asked
    assert "Spanish" in system and user == RESULTS


async def test_numbers_in_other_digits_and_separators_count_as_the_same():
    persian = "۴۱۲ Markham St را برای شنبه ساعت ۲ رزرو کردم. در ۸۸ Erskine Ave، پارکینگ: ۱۸۰ دلار در ماه."
    assert numbers(persian) == numbers(RESULTS)
    assert numbers("rent $2,450") == numbers("اجاره ۲٬۴۵۰ دلار") == numbers("租金 2450 元")
    assert await Translator(FakeChat(persian))(RESULTS, "fa") == persian


async def test_a_changed_dropped_or_added_number_sends_the_english():
    changed = SPANISH.replace("$180", "$190")
    dropped = "Reservé 412 Markham St para el sábado. En 88 Erskine Ave, estacionamiento: $180 al mes."
    added = SPANISH + " Depósito: $500."
    for text in (changed, dropped, added):
        assert await Translator(FakeChat(text))(RESULTS, "es") == RESULTS


async def test_english_no_language_no_model_or_a_failure_sends_the_text_as_it_is():
    chat = FakeChat(SPANISH)
    assert await Translator(chat)(RESULTS, "en") == RESULTS
    assert await Translator(chat)(RESULTS, "en-CA") == RESULTS
    assert await Translator(chat)(RESULTS, None) == RESULTS
    assert chat.asked == []   # English never reaches the model
    assert await Translator(None)(RESULTS, "es") == RESULTS
    assert await Translator(FakeChat(error=RuntimeError("429")))(RESULTS, "es") == RESULTS
    assert await Translator(FakeChat(SPANISH, delay_s=1), timeout_s=0.01)(RESULTS, "es") == RESULTS
    assert await Translator(FakeChat(""))(RESULTS, "es") == RESULTS


# #188: a Farsi results text with its numbers kept, in Persian digits, separators or 24-hour times, is
# used; any number dropped or changed sends the English.
TIMES = "Rent $3,200 a month, parking $180. Viewing Saturday at 6:30 pm. Call back by 10 am."


@pytest.mark.parametrize(("translated", "kept"), [
    ("اجاره ۳۲۰۰ دلار در ماه، پارکینگ ۱۸۰ دلار. بازدید شنبه ساعت ۶:۳۰ بعدازظهر. تا ساعت ۱۰ صبح.", True),
    ("اجاره ۳٫۲۰۰ دلار، پارکینگ ۱۸۰. بازدید شنبه ساعت ۱۸:۳۰. تا ساعت ۱۰.", True),   # ٫ and 24-hour
    ("اجاره ۳٬۲۰۰ دلار، پارکینگ ۱۸۰. بازدید شنبه ساعت ۰۶:۳۰. تا ساعت ۱۰.", True),
    ("اجاره ۳۲۰۰ دلار. بازدید شنبه ساعت ۶:۳۰. تا ساعت ۱۰.", False),                 # parking dropped
    ("اجاره ۳۲۰۰ دلار، پارکینگ ۱۹۰. بازدید شنبه ساعت ۶:۳۰. تا ساعت ۱۰.", False),    # 180 became 190
    ("اجاره ۳۲۰۰ دلار، پارکینگ ۱۸۰. بازدید شنبه ساعت ۱۸:۳۰. تا ساعت ۲۲.", False),   # 10 am read as pm
])
def test_a_translations_numbers_are_checked_whatever_their_form(translated, kept):
    assert keeps_numbers(TIMES, translated) is kept


async def test_a_farsi_results_text_with_its_numbers_kept_is_texted_in_farsi():
    farsi = "اجاره ۳٫۲۰۰ دلار، پارکینگ ۱۸۰. بازدید شنبه ساعت ۱۸:۳۰. تا ساعت ۱۰."
    assert await Translator(FakeChat(farsi))(TIMES, "fa") == farsi
    assert numbers("۲٫۵ کیلومتر") == numbers("2.5 km")   # the decimal mark is still a decimal point
    assert "Keep times in the same 12-hour form" in SYSTEM


def test_language_codes_read_as_names_whatever_their_form():
    assert [primary(c) for c in ("pt-BR", "zh_Hant", " ES ", None, "")] == ["pt", "zh", "es", "", ""]
    assert (language_name("pa"), language_name("fa-IR"), language_name("xx")) == (
        "Punjabi", "Persian (Farsi)", "the language with code xx")


# The results text after the calls


class FakeTexter:
    ready = True

    def __init__(self):
        self.sent = []

    def send_soon(self, to, from_, body):
        self.sent.append((to, from_, body))


class Booked:
    transport, uses_phone = "sim", False

    async def place(self, dial, report):
        return CallResult("done", CallOutcome(call_id=dial.call_id, listing_id=dial.listing_id,
                                              end_reason="done"))


async def _texted(language: str | None, chat: FakeChat) -> str:
    listing = Listing(listing_id="t:1", source="t", source_id="1", url="https://example.com/1",
                      first_seen=NOW, last_seen=NOW, address="1 Dial St", lat=43.65, lng=-79.38, rent=2000,
                      monthly_cost=2000, beds=1)
    rt, texter = SessionRuntime(Catalogue([listing], "test")), FakeTexter()
    settings = Settings(database_url=None, web_url="https://landline.example", transport="stub",
                        twilio_phone_number="+12895122338", higgs_max_sessions=3, listing_call_max_s=5)
    d = Dispatcher(rt, settings, runner=Booked(), texter=texter, clock=lambda: NOW,
                   translate=Translator(chat))
    await rt.start(SessionStarted(session_id=SID, at=NOW, idem_key=f"{SID}:start", channel="phone",
                                  caller_phone="+14165550199"))
    if language:
        await rt.emit(PreferencesUpdated(session_id=SID, at=NOW, idem_key=f"{SID}:lang",
                                         delta=PreferenceDelta(language=language)))
    await rt.emit(CallsRequested(session_id=SID, at=NOW, idem_key=f"{SID}:plan", listing_ids=["t:1"]))
    await rt.emit(SessionEnded(session_id=SID, at=NOW, idem_key=f"{SID}:end", reason="hangup"))
    await d.idle()
    [(_, _, body)] = texter.sent
    return body


async def test_the_results_are_texted_in_the_renters_language():
    english = await _texted(None, FakeChat())
    words = english.removeprefix("Landline: ").split("\nMap:")[0]
    chat = FakeChat(f"En español: {words}")   # a stand-in translation that keeps every number
    body = await _texted("es", chat)
    assert body.startswith("Landline: En español: ")
    assert body.endswith(f"Map: https://landline.example/s/{SID}")
    assert [user for _, user in chat.asked] == [words]


async def test_a_bad_translation_texts_the_english_results():
    english = await _texted(None, FakeChat())
    assert await _texted("es", FakeChat("Todo listo, 3 lugares.")) == english


# Opening a call


def test_a_previous_call_is_not_appended_and_does_not_hint_the_language():
    instructions, start = bridge.opening("Last call: a 1 bed under $2,500.", "es")
    assert instructions == INSTRUCTIONS + FIRST_CALL
    assert "Spanish" not in instructions and "Last call" not in instructions
    assert start[-1] == {"type": "response.create"}
    assert bridge.hint("Last call: …", "es-MX") is None


def test_english_or_a_first_call_adds_no_language_line_and_no_hint():
    assert "spoke" not in bridge.opening("Last call: …", "en")[0]
    assert "spoke" not in bridge.opening("Last call: …", None)[0]
    instructions, start = bridge.opening(None, "es")   # a first call: nothing remembered to welcome back
    assert instructions == INSTRUCTIONS + FIRST_CALL
    assert start[-1] == {"type": "response.create"}   # no response object (#129)
    assert start[0]["item"]["content"][0]["text"] == CALL_CONNECTED
    hints = bridge.hint(None, "es"), bridge.hint("Last call: …", None), bridge.hint("Last call: …", "yue")
    assert hints == (None, None, None)   # Cantonese has no two-letter code: Higgs detects it


def test_the_greeting_invites_any_language():
    assert "any language" in FIRST_CALL and "Renters may speak any language" in INSTRUCTIONS


def test_the_opening_never_tells_the_model_to_avoid_tools():
    """FIRST_CALL ends the session instructions. "Don't call a tool to open the call" there made the
    Concierge call no tools all call in 7 of 21 holdout sessions (#136), so the greeting's no-tool rule
    comes with the rule for every turn after it."""
    text = " ".join(FIRST_CALL.split()).lower()
    assert "don't call a tool" not in text
    assert "from the renter's first answer on, call your tools first in every turn" in text


def test_the_transcription_hint_goes_in_only_when_known():
    hinted = session_config(instructions="x", tools=[], language="fa")
    assert hinted["session"]["audio"]["input"]["transcription"] == {"model": "higgs-stt-3.1",
                                                                     "language": "fa"}
    plain = session_config(instructions="x", tools=[])
    assert plain["session"]["audio"]["input"]["transcription"] == {"model": "higgs-stt-3.1"}

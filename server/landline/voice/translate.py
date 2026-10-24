"""The results text in the renter's language (A-04): one text-model call after the calls, never on a turn.

The Narrator writes the results in English from the board and the call outcomes (O-06). A renter who
spoke another language gets them translated before they're texted, and code checks the translation:
every number in it must be one the English had, as often, or the English goes instead (hard rule 2: a
model never changes a price, a time or an address number). No key, a timeout or an error also sends the
English, so the text always goes out.
"""
from __future__ import annotations

import asyncio
import logging
import re
import unicodedata
from collections import Counter
from typing import Any

from landline.agents.concierge.prompt import language_name, primary

log = logging.getLogger(__name__)

TIMEOUT_S = 25   # Higgs took 14–30 s on Farsi under load (24 Oct); the text waits for it
SYSTEM = (
    "You translate a short text message from Landline, an assistant that helps people rent in Toronto, "
    "into {language} for the renter who will read it. Keep every number, price, day, time and street "
    "address exactly as written, with the digits 0-9 and street names in Latin letters. Keep times in the "
    "same 12-hour form (6:30 pm stays 6:30 pm). Add nothing and leave nothing out. Answer with the "
    "translation only."
)
SCHEMA = {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"],
          "additionalProperties": False}
# A thousands separator between digits: "2,450", "2٬450", "2 450" (no-break or narrow spaces), and the
# Persian decimal mark when three digits follow: "۳٫۲۰۰" for 3,200 is a common slip (#188).
SEPARATOR = re.compile(r"(?<=\d)[,٬٫  '’](?=\d{3}(?!\d))")
# The Persian decimal mark otherwise is the decimal point it is: "۲٫۵" is 2.5.
DECIMAL = re.compile(r"(?<=\d)٫(?=\d)")
NUMBER = re.compile(r"\d+(?:\.\d+)?")
# An English 12-hour time ("6:30 pm", "6 pm"), which a translation may give in its 24-hour form.
CLOCK = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*([ap])\.?m\.?(?![a-z])", re.I)


class Translator:
    """`await translate(text, language)`: the text in that language, or the text itself."""

    def __init__(self, chat: Any | None, timeout_s: float = TIMEOUT_S) -> None:
        self.chat, self.timeout_s = chat, timeout_s   # chat: agents.auditor.llm.Chat, or None

    async def __call__(self, text: str, language: str | None) -> str:
        lang = primary(language)
        if not text or not lang or lang == "en" or self.chat is None:
            return text
        try:
            out = await asyncio.wait_for(
                self.chat.json(SYSTEM.format(language=language_name(lang)), text, SCHEMA, name="translation"),
                self.timeout_s)
        except Exception as exc:   # a timeout, a refusal, a 429: the English still goes out
            log.warning("results not translated to %s: %r", lang, exc)
            return text
        said = str(out.get("text") or "").strip() if isinstance(out, dict) else ""
        if not said:
            return text
        if not keeps_numbers(text, said):
            log.warning("results translation to %s changed a number (%s, was %s); texting English",
                        lang, sorted(numbers(said).elements()), sorted(numbers(text).elements()))
            return text
        return said


def keeps_numbers(english: str, translated: str) -> bool:
    """Whether the translation has exactly the English numbers, as often. Its times may be the English
    12-hour ones, or every one of them in its 24-hour form ("6:30 pm" as "18:30"), never any other time:
    a pm read as am still sends the English (#188)."""
    said = numbers(translated)
    return said == numbers(english) or said == numbers(CLOCK.sub(_h24, english))


def _h24(m: re.Match) -> str:
    hour = int(m.group(1)) % 12 + (12 if m.group(3).lower() == "p" else 0)
    return f"{hour}:{m.group(2)}" if m.group(2) else str(hour)


def numbers(text: str) -> Counter[str]:
    """Every number in `text`, read the same whatever digits it's written in: "$2,450", "۲٬۴۵۰" and
    "۲٫۴۵۰" are all 2450, "۲٫۵" is 2.5, and "09" is 9."""
    ascii_digits = "".join(str(d) if (d := unicodedata.decimal(c, None)) is not None else c for c in text)
    plain = DECIMAL.sub(".", SEPARATOR.sub("", ascii_digits))
    return Counter(_bare(n) for n in NUMBER.findall(plain))


def _bare(number: str) -> str:
    whole, dot, fraction = number.partition(".")
    return (whole.lstrip("0") or "0") + dot + fraction

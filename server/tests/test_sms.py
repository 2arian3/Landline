"""Texting through Twilio's Messages API, with httpx's mock transport in place of the network."""
import base64
from urllib.parse import parse_qs

import httpx

from landline.voice.sms import Texter


def _texter(status: int = 201, body: dict | None = None) -> tuple[Texter, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, json=body if body is not None else {"sid": "SM123"})

    return Texter("AC_test", "tok", client=httpx.AsyncClient(transport=httpx.MockTransport(answer))), seen


async def test_send_posts_the_message_with_the_account_credentials():
    texter, seen = _texter()
    assert await texter.send("+14165550100", "+12892765257", "Landline: https://x/s/s_1") == "SM123"
    request = seen[0]
    assert str(request.url) == "https://api.twilio.com/2010-04-01/Accounts/AC_test/Messages.json"
    assert request.headers["authorization"] == "Basic " + base64.b64encode(b"AC_test:tok").decode()
    assert parse_qs(request.content.decode()) == {
        "To": ["+14165550100"], "From": ["+12892765257"], "Body": ["Landline: https://x/s/s_1"]}
    await texter.close()


async def test_a_refused_message_returns_none():
    texter, _ = _texter(400, {"code": 21608, "message": "unverified number"})
    assert await texter.send("+14165550100", "+12892765257", "hi") is None
    await texter.close()


def test_without_credentials_it_isnt_ready():
    assert not Texter(None, "tok").ready and not Texter("AC_test", None).ready and Texter("AC", "t").ready

"""Twilio pieces for the renter's call, without the SDK: TwiML, the webhook signature, and the messages
we send on a bidirectional media stream."""
from __future__ import annotations

import base64
import hashlib
import hmac
from typing import Any
from xml.sax.saxutils import escape, quoteattr

XML_HEAD = '<?xml version="1.0" encoding="UTF-8"?>'


def twiml_stream(ws_url: str, call_id: str) -> str:
    """Hands the call to our media stream. The call id travels as a <Parameter> and comes back in the
    `start` frame's customParameters: Twilio drops query strings on stream URLs (PORTING bug #1)."""
    return (f"{XML_HEAD}<Response><Connect><Stream url={quoteattr(ws_url)}>"
            f'<Parameter name="call_id" value={quoteattr(call_id)}/>'
            "</Stream></Connect></Response>")


def twiml_say_hangup(text: str) -> str:
    return f"{XML_HEAD}<Response><Say>{escape(text)}</Say><Hangup/></Response>"


def twiml_hangup() -> str:
    return f"{XML_HEAD}<Response><Hangup/></Response>"


def signature(auth_token: str, url: str, params: dict[str, str]) -> str:
    """X-Twilio-Signature: HMAC-SHA1 over the full public URL followed by each POST field's name and
    value, sorted by name, base64-encoded."""
    payload = url + "".join(name + params[name] for name in sorted(params))
    digest = hmac.new(auth_token.encode(), payload.encode(), hashlib.sha1).digest()
    return base64.b64encode(digest).decode()


def valid_signature(auth_token: str, url: str, params: dict[str, str], given: str) -> bool:
    return bool(given) and hmac.compare_digest(signature(auth_token, url, params), given)


def media(stream_sid: str, payload: str) -> dict[str, Any]:
    """Audio to play to the caller: base64 μ-law 8 kHz, exactly what Higgs sends."""
    return {"event": "media", "streamSid": stream_sid, "media": {"payload": payload}}


def clear(stream_sid: str) -> dict[str, Any]:
    """Drops audio Twilio has buffered but not played yet, so the agent stops when the caller talks."""
    return {"event": "clear", "streamSid": stream_sid}

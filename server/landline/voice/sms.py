"""Texting the renter: Twilio's Messages API over httpx, without the SDK.

Sending is a network call, so a voice turn never waits for it: `send_soon` starts the send in the
background, keeps a reference so it isn't dropped, and logs how it went. The numbers come from the call
(the renter's caller ID, and the number they dialled), never from a model (hard rule 2).
"""
from __future__ import annotations

import asyncio
import logging

import httpx

log = logging.getLogger(__name__)

API = "https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"
TIMEOUT_S = 10


class Texter:
    def __init__(self, account_sid: str | None, auth_token: str | None,
                 client: httpx.AsyncClient | None = None) -> None:
        self.account_sid, self.auth_token = account_sid, auth_token
        self.client = client or httpx.AsyncClient(timeout=TIMEOUT_S)
        self._sending: set[asyncio.Task] = set()

    @property
    def ready(self) -> bool:
        return bool(self.account_sid and self.auth_token)

    async def send(self, to: str, from_: str, body: str) -> str | None:
        """Sends one SMS and returns Twilio's message sid, or None (logged) when it can't."""
        try:
            r = await self.client.post(API.format(sid=self.account_sid),
                                       auth=(self.account_sid, self.auth_token),
                                       data={"To": to, "From": from_, "Body": body})
        except httpx.HTTPError as exc:
            log.warning("SMS to …%s failed: %r", to[-4:], exc)
            return None
        if r.status_code >= 300:
            detail = r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text
            log.warning("SMS to …%s refused (%s): %s", to[-4:], r.status_code, detail)
            return None
        sid = r.json().get("sid")
        log.info("SMS to …%s sent (%s)", to[-4:], sid)
        return sid

    def send_soon(self, to: str, from_: str, body: str) -> None:
        """Starts `send` in the background."""
        task = asyncio.create_task(self.send(to, from_, body))
        self._sending.add(task)
        task.add_done_callback(self._sending.discard)

    async def close(self) -> None:
        await asyncio.gather(*self._sending, return_exceptions=True)
        await self.client.aclose()

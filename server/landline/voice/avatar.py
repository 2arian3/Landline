"""The Concierge's face (A-11): a Higgs Avatar that lip-syncs the renter's call, live, on their board.

Boson's realtime avatar joins a LiveKit Cloud room and turns speech it's sent into lip-synced video
(docs.boson.ai, Higgs Avatar, "Realtime sessions"). The call itself doesn't change: Higgs Realtime still
talks to the renter over Twilio or the browser socket, and each chunk of the Concierge's audio is also
queued for the avatar. Nothing here can hold up or end the call (hard rule 8: the page is never
load-bearing); a failed avatar is logged and the call goes on with a voice only.

- **Started by a page, not by the call.** A board that opens during a live renter call asks for the avatar
  (POST /api/sessions/{id}/avatar), so a phone call nobody is watching costs no avatar slot.
- **Code owns identity.** The room is named after the call id, and the three people in it are fixed: this
  server (`AGENT`, the only one the avatar takes audio from), the avatar (`AVATAR`, publishes video and
  audio) and any number of pages (`viewer-…`, subscribe only). The page never sees a key or a secret.
- **The wire** (LiveKit Agents' avatar protocol): each response is one byte stream on `lk.audio_stream`
  with `sample_rate` and `num_channels` attributes, PCM16 mono 24 kHz; `lk.clear_buffer` when the renter
  talks over it; the avatar calls our `lk.playback_finished` once it has played what it was sent.
- **Audio:** the browser's is already PCM16 24 kHz and passes through; the phone's (μ-law 8 kHz) is
  decoded and resampled. Only responses that start once the avatar is live are sent, whole.
- **Ends with the call.** After a goodbye the last words play out (up to END_GRACE_S), then the session is
  released and the room left. Boson also ends it at max_duration_seconds, or once the room is empty.
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import hmac
import json
import logging
import secrets
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import httpx
import numpy as np

from landline.config import Settings
from landline.voice.higgs import PCMU

log = logging.getLogger(__name__)

BOSON_API = "https://api.boson.ai/v1"
AGENT = "landline-agent"       # this server in the room: the avatar's publisher_identity
AVATAR = "landline-avatar"     # the avatar Boson adds to the room
AUDIO_TOPIC = "lk.audio_stream"
CLEAR_BUFFER = "lk.clear_buffer"
PLAYBACK_FINISHED = "lk.playback_finished"
RATE = 24000                   # what the avatar takes: PCM16 mono at 24 kHz
START_TIMEOUT_S = 60           # POST /v1/avatar/sessions answers once the avatar has joined the room
READY_S = 20                   # after that, for the avatar's tracks to show in the room
RETRY_S = 20                   # a refused start (no free slot, an outage) is tried again after this
SEND_OUT_S = 3                 # at the end, for audio still queued to reach the avatar
END_GRACE_S = 8                # and for the avatar to finish saying it, after a goodbye
FAILS_BEFORE_GIVING_UP = 3     # chunks in a row that couldn't be sent: the room is gone

JoinRoom = Callable[[str, str], Awaitable[Any]]


@dataclass(frozen=True)
class AvatarConfig:
    boson_api_key: str
    livekit_url: str
    livekit_api_key: str
    livekit_api_secret: str
    avatar_id: str
    max_s: int                 # the session's max_duration_seconds: a renter call's deadline and a margin

    @classmethod
    def from_settings(cls, s: Settings) -> AvatarConfig | None:
        """None unless every value is set: no avatar, and nothing about the call changes."""
        if not (s.boson_api_key and s.livekit_url and s.livekit_api_key and s.livekit_api_secret):
            return None
        return cls(s.boson_api_key, s.livekit_url, s.livekit_api_key, s.livekit_api_secret, s.higgs_avatar_id,
                   max_s=min(3600, int(s.renter_call_max_s) + 120))

    def token(self, identity: str, room: str, *, publish: bool = False, subscribe: bool = True,
              data: bool = False, kind: str | None = None, attributes: dict[str, str] | None = None) -> str:
        return livekit_token(self.livekit_api_key, self.livekit_api_secret, identity=identity, room=room,
                             ttl_s=self.max_s + 300, publish=publish, subscribe=subscribe, data=data,
                             kind=kind, attributes=attributes)


def livekit_token(api_key: str, secret: str, *, identity: str, room: str, ttl_s: int, publish: bool = False,
                  subscribe: bool = True, data: bool = False, kind: str | None = None,
                  attributes: dict[str, str] | None = None, now: float | None = None) -> str:
    """A LiveKit access token (HS256 JWT) to join one room, the claims livekit-api's AccessToken writes. A
    publisher may publish only camera and microphone tracks."""
    at = int(now if now is not None else time.time())
    video: dict[str, Any] = {"roomJoin": True, "room": room, "canPublish": publish, "canSubscribe": subscribe,
                             "canPublishData": data}
    if publish:
        video["canPublishSources"] = ["camera", "microphone"]
    claims: dict[str, Any] = {"sub": identity, "iss": api_key, "nbf": at, "exp": at + ttl_s, "video": video}
    if kind:
        claims["kind"] = kind
    if attributes:
        claims["attributes"] = attributes

    def part(obj: dict[str, Any]) -> bytes:
        return base64.urlsafe_b64encode(json.dumps(obj, separators=(",", ":")).encode()).rstrip(b"=")

    signed = part({"alg": "HS256", "typ": "JWT"}) + b"." + part(claims)
    sig = base64.urlsafe_b64encode(hmac.new(secret.encode(), signed, hashlib.sha256).digest()).rstrip(b"=")
    return (signed + b"." + sig).decode()


def _ulaw_table() -> np.ndarray:
    """G.711 μ-law byte → PCM16 sample."""
    out = np.zeros(256, dtype=np.int16)
    for byte in range(256):
        u = ~byte & 0xFF
        exponent, mantissa = (u >> 4) & 0x07, u & 0x0F
        magnitude = (((mantissa << 3) + 0x84) << exponent) - 0x84
        out[byte] = -magnitude if u & 0x80 else magnitude
    return out


_ULAW = _ulaw_table()


class ToAvatar:
    """Higgs's audio as the avatar takes it: PCM16 little-endian mono at 24 kHz. The browser's already is.
    The phone's μ-law 8 kHz is decoded and stretched to three samples each, a straight line from the last
    sample of the chunk before, so chunk edges don't click."""

    def __init__(self, audio_format: dict[str, Any]) -> None:
        self.ulaw = audio_format.get("type") == PCMU["type"]
        self.last = 0.0

    def __call__(self, b64: str) -> bytes:
        raw = base64.b64decode(b64)
        if not self.ulaw or not raw:
            return raw
        x = _ULAW[np.frombuffer(raw, dtype=np.uint8)].astype(np.float32)
        before = np.concatenate(([self.last], x[:-1]))
        step = (x - before) / 3
        out = np.empty(len(x) * 3, dtype=np.float32)
        out[0::3], out[1::3], out[2::3] = before + step, before + 2 * step, x
        self.last = float(x[-1])
        return np.round(out).astype("<i2").tobytes()


async def join_livekit(url: str, token: str) -> Any:
    """Joins the room as this server. Nothing is subscribed: the server sends audio and never needs the
    avatar's media. LiveKit's native library loads only here, when the first avatar starts."""
    from livekit import rtc

    room = rtc.Room()
    await room.connect(url, token, rtc.RoomOptions(auto_subscribe=False))
    return room


_FLUSH, _CLEAR, _END = object(), object(), object()   # queue markers: end of a response, barge-in, call over


class Avatar:
    """One renter call's avatar: made when the call starts, started by the first page that asks (`want`),
    ended with the call (`close`). The bridge feeds it every response; none of those calls waits on the
    network."""

    def __init__(self, call_id: str, session_id: str, audio_format: dict[str, Any], config: AvatarConfig, *,
                 http: httpx.AsyncClient, join_room: JoinRoom = join_livekit,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.call_id, self.session_id, self.config = call_id, session_id, config
        self.room_name = f"landline-{call_id}"
        self.http, self.join_room, self.clock = http, join_room, clock
        self.convert = ToAvatar(audio_format)
        self.state = "idle"          # idle → starting → live → closed; failed → starting again after RETRY_S
        self.failed_at = 0.0
        self.session: str | None = None   # Boson's avatar session id
        self.room: Any = None
        self.queue: asyncio.Queue[Any] = asyncio.Queue()
        self.sending = False         # the response under way goes to the avatar
        self.flushed = self.finished = 0   # responses sent whole, and the avatar's word it played them
        self.played = asyncio.Event()
        self.streams = 0
        self._start: asyncio.Task[None] | None = None
        self._sender: asyncio.Task[None] | None = None

    # ── the page ────────────────────────────────────────────────────────────

    def want(self) -> None:
        """A page wants to see the Concierge. Starts the avatar once; a refused start is tried again after
        RETRY_S."""
        if self.state == "failed" and self.clock() - self.failed_at >= RETRY_S:
            self.state = "idle"
        if self.state != "idle":
            return
        self.state, self.queue = "starting", asyncio.Queue()
        self._start = asyncio.create_task(self._run())

    def viewer(self) -> dict[str, str]:
        """What a page needs to watch: the room and a token that can only subscribe in it."""
        identity = f"viewer-{secrets.token_hex(4)}"
        return {"url": self.config.livekit_url, "room": self.room_name, "identity": AVATAR,
                "token": self.config.token(identity, self.room_name)}

    # ── the call (voice/bridge.py), in Higgs's order ───────────────────────

    def response_started(self) -> None:
        self.sending = self.state == "live"

    def audio(self, b64: str) -> None:
        if self.sending:
            self.queue.put_nowait(b64)

    def response_done(self) -> None:
        if self.sending:
            self.queue.put_nowait(_FLUSH)
        self.sending = False

    def barge_in(self) -> None:
        """The renter talked over the Concierge: drop what hasn't gone out and stop the avatar."""
        if self.state != "live":
            return
        self.sending = False
        while not self.queue.empty():
            self.queue.get_nowait()
        self.queue.put_nowait(_CLEAR)

    async def close(self, *, play_out: bool = False) -> None:
        """The call ended. After a goodbye (`play_out`) the avatar gets to finish it, within bounds; then the
        session is released and the room left. Never raises."""
        if self.state == "closed":
            return
        live, self.state, self.sending = self.state == "live", "closed", False
        if self._start is not None and not self._start.done():
            self._start.cancel()
            await asyncio.gather(self._start, return_exceptions=True)
        if self._sender is not None:
            self.queue.put_nowait(_END)
            try:
                await asyncio.wait_for(self._sender, SEND_OUT_S)
            except Exception:   # too slow (wait_for cancels it) or broken: what's left isn't said
                self._sender.cancel()
        if live and play_out and self.finished < self.flushed:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self.played.wait(), END_GRACE_S)
        await self._release()
        log.info("avatar for call %s closed", self.call_id)

    # ── inside ──────────────────────────────────────────────────────────────

    async def _run(self) -> None:
        cfg = self.config
        try:
            self.room = await self.join_room(cfg.livekit_url, cfg.token(AGENT, self.room_name, data=True))
            self.room.local_participant.register_rpc_method(PLAYBACK_FINISHED, self._finished)
            r = await self.http.post(f"{BOSON_API}/avatar/sessions", timeout=START_TIMEOUT_S, headers={
                "Authorization": f"Bearer {cfg.boson_api_key}"}, json={
                "model": "higgs-avatar", "avatar_id": cfg.avatar_id, "max_duration_seconds": cfg.max_s,
                "transport": {
                    "type": "livekit", "url": cfg.livekit_url, "room_name": self.room_name,
                    "participant_token": cfg.token(AVATAR, self.room_name, publish=True, subscribe=False,
                                                   data=True, kind="agent",
                                                   attributes={"lk.publish_on_behalf": AGENT}),
                    "participant_identity": AVATAR, "publisher_identity": AGENT,
                    "audio_source": "data_stream"}})
            if r.status_code >= 300:
                raise RuntimeError(f"Boson refused the avatar: {r.status_code} {r.text[:300]}")
            self.session = str(r.json()["id"])
            await self._arrived()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("avatar for call %s failed to start: %s", self.call_id, exc)
            await self._release()
            if self.state == "starting":
                self.state, self.failed_at = "failed", self.clock()
            return
        if self.state != "starting":   # the call ended while it started
            return
        self.state = "live"
        self._sender = asyncio.create_task(self._send())
        log.info("avatar for call %s live in %s (%s)", self.call_id, self.room_name, self.session)

    async def _arrived(self) -> None:
        """Waits for the avatar's tracks in the room: audio sent before then would be lost."""
        deadline = self.clock() + READY_S
        while self.clock() < deadline:
            avatar = self.room.remote_participants.get(AVATAR)
            if avatar is not None and avatar.track_publications:
                return
            await asyncio.sleep(0.1)
        raise RuntimeError(f"the avatar didn't appear in {self.room_name} within {READY_S}s")

    def _finished(self, data: Any) -> str:
        """The avatar played everything it was sent (lk.playback_finished)."""
        if getattr(data, "caller_identity", AVATAR) != AVATAR:
            return "reject"
        self.finished = self.flushed
        self.played.set()
        return "ok"

    async def _send(self) -> None:
        """Sends the queue to the avatar in order: one byte stream per response."""
        local = self.room.local_participant
        writer, fails = None, 0
        while True:
            item = await self.queue.get()
            try:
                if item is _END or item is _FLUSH or item is _CLEAR:
                    if writer is not None:
                        await writer.aclose()
                        writer = None
                        if item is not _CLEAR:
                            self.flushed += 1
                            self.played.clear()
                    if item is _END:
                        return
                    if item is _CLEAR:
                        self.finished = self.flushed   # nothing it was sent is still to play
                        await local.perform_rpc(destination_identity=AVATAR, method=CLEAR_BUFFER, payload="")
                else:
                    pcm = self.convert(item)
                    if writer is None:
                        self.streams += 1
                        writer = await local.stream_bytes(
                            f"landline-{self.streams}", topic=AUDIO_TOPIC, destination_identities=[AVATAR],
                            attributes={"sample_rate": str(RATE), "num_channels": "1"})
                    await writer.write(pcm)
                fails = 0
            except Exception as exc:
                fails += 1
                writer = None
                log.warning("avatar for call %s: couldn't send audio (%s)", self.call_id, exc)
                if fails >= FAILS_BEFORE_GIVING_UP:
                    log.warning("avatar for call %s: giving up, the call goes on without it", self.call_id)
                    if self.state == "live":   # not closing: a page that asks again starts a new one
                        self.state = "failed"
                        self.failed_at, self.sending = self.clock(), False
                        await self._release()
                    return

    async def _release(self) -> None:
        session, room, self.session, self.room = self.session, self.room, None, None
        if session is not None:
            try:
                await self.http.delete(f"{BOSON_API}/avatar/sessions/{session}", timeout=10,
                                       headers={"Authorization": f"Bearer {self.config.boson_api_key}"})
            except Exception as exc:
                log.warning("avatar session %s not released (%s); Boson ends it when the room empties",
                            session, exc)
        if room is not None:
            with contextlib.suppress(Exception):
                await room.disconnect()


class Avatars:
    """The live renter calls' avatars, by session. Lives on app.state (hard rule 4)."""

    def __init__(self, config: AvatarConfig | None, *, join_room: JoinRoom = join_livekit,
                 http: httpx.AsyncClient | None = None, clock: Callable[[], float] = time.monotonic) -> None:
        self.config, self.join_room, self.clock = config, join_room, clock
        self.http = http
        self.live: dict[str, Avatar] = {}
        self._ending: set[asyncio.Task[None]] = set()

    @classmethod
    def from_settings(cls, settings: Settings) -> Avatars:
        return cls(AvatarConfig.from_settings(settings))

    @property
    def ready(self) -> bool:
        return self.config is not None

    def open(self, call_id: str, session_id: str, audio_format: dict[str, Any]) -> Avatar | None:
        """The renter call's avatar, not started yet. None when avatars are off."""
        if self.config is None:
            return None
        if self.http is None:
            self.http = httpx.AsyncClient()
        avatar = Avatar(call_id, session_id, audio_format, self.config, http=self.http,
                        join_room=self.join_room, clock=self.clock)
        self.live[session_id] = avatar
        return avatar

    def get(self, session_id: str) -> Avatar | None:
        return self.live.get(session_id)

    def end(self, session_id: str, avatar: Avatar, *, play_out: bool = False) -> None:
        """The call is over: no page can ask for this avatar any more, and it closes in the background, so
        ending the call never waits on Boson or LiveKit."""
        if self.live.get(session_id) is avatar:
            del self.live[session_id]
        task = asyncio.create_task(avatar.close(play_out=play_out))
        self._ending.add(task)
        task.add_done_callback(self._ending.discard)

    async def aclose(self) -> None:
        """Shutdown: every avatar released, nothing left holding a slot."""
        for session_id, avatar in list(self.live.items()):
            self.end(session_id, avatar)
        if self._ending:
            await asyncio.gather(*self._ending, return_exceptions=True)
        if self.http is not None:
            await self.http.aclose()

"""The Concierge's face (A-11): a Higgs Avatar lip-syncing the renter's call in a LiveKit room. No network:
LiveKit is a fake room, Boson a mock transport, Higgs the scripted fake from the call tests."""
import asyncio
import base64
import hashlib
import hmac
import json
import time
from dataclasses import replace

import httpx
import numpy as np
import pytest
from test_renter_calls import FakeHiggs, _client, _hang_up_and_wait

from landline.config import Settings
from landline.voice import avatar as av
from landline.voice.avatar import AGENT, AVATAR, Avatar, AvatarConfig, Avatars, ToAvatar, livekit_token
from landline.voice.higgs import PCM_24K, PCMU

CONFIG = AvatarConfig("bai-test", "wss://landline-test.livekit.cloud", "APItest", "secret-test",
                      "builtin_claire_v1", max_s=1020)


def _claims(token: str, secret: str = CONFIG.livekit_api_secret) -> dict:
    head, body, sig = token.split(".")
    expected = hmac.new(secret.encode(), f"{head}.{body}".encode(), hashlib.sha256).digest()
    assert base64.urlsafe_b64decode(sig + "=" * (-len(sig) % 4)) == expected, "bad signature"
    return json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))


class FakeWriter:
    def __init__(self, name, topic, destination_identities, attributes):
        self.name, self.topic, self.to, self.attributes = name, topic, destination_identities, attributes
        self.chunks: list[bytes] = []
        self.closed = False

    async def write(self, data):
        assert not self.closed
        self.chunks.append(data)

    async def aclose(self):
        self.closed = True


class FakeLocal:
    def __init__(self):
        self.streams: list[FakeWriter] = []
        self.rpcs: list[tuple[str, str, str]] = []
        self.methods: dict = {}

    async def stream_bytes(self, name, *, topic="", destination_identities=None, attributes=None):
        writer = FakeWriter(name, topic, destination_identities, attributes)
        self.streams.append(writer)
        return writer

    async def perform_rpc(self, *, destination_identity, method, payload):
        self.rpcs.append((destination_identity, method, payload))
        return "ok"

    def register_rpc_method(self, name, handler):
        self.methods[name] = handler


class FakeRoom:
    def __init__(self):
        self.local_participant = FakeLocal()
        self.remote_participants: dict = {}
        self.disconnected = False

    async def disconnect(self):
        self.disconnected = True


class Played:
    """What LiveKit hands the lk.playback_finished handler."""
    def __init__(self, caller=AVATAR):
        self.caller_identity = caller
        self.payload = json.dumps({"playback_position": 1.0, "interrupted": False})


class FakeLiveKit:
    """The room this server joins, and Boson putting the avatar in it."""

    def __init__(self, status=201):
        self.status = status
        self.rooms: list[FakeRoom] = []
        self.joined: list[tuple[str, str]] = []
        self.requests: list[httpx.Request] = []

    async def join(self, url, token):
        self.joined.append((url, token))
        room = FakeRoom()
        self.rooms.append(room)
        return room

    def boson(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.method == "DELETE":
            return httpx.Response(200, json={"id": "avrt_1", "status": "releasing"})
        if self.status != 201:
            return httpx.Response(self.status, json={"error": {"code": "capacity_exhausted"}})
        self.rooms[-1].remote_participants[AVATAR] = type("P", (), {"track_publications": {"TR_v": 1}})()
        return httpx.Response(201, json={"id": "avrt_1", "status": "active", "avatar_identity": AVATAR})

    def avatars(self, clock=time.monotonic) -> Avatars:
        http = httpx.AsyncClient(transport=httpx.MockTransport(self.boson))
        return Avatars(CONFIG, join_room=self.join, http=http, clock=clock)


async def _until(check, timeout=2.0):
    deadline = time.monotonic() + timeout
    while not check():
        assert time.monotonic() < deadline, "timed out"
        await asyncio.sleep(0.005)


async def _live(lk: FakeLiveKit, audio_format=PCM_24K, clock=time.monotonic) -> tuple[Avatars, Avatar]:
    avatars = lk.avatars(clock)
    face = avatars.open("c_test123456", "s_test123456", audio_format)
    face.want()
    await _until(lambda: face.state == "live")
    return avatars, face


def _pcm(*samples: int) -> str:
    return base64.b64encode(np.array(samples, dtype="<i2").tobytes()).decode()


# ── tokens and audio ────────────────────────────────────────────────────────

def test_a_livekit_token_is_a_signed_jwt_for_one_room():
    token = livekit_token("APIkey", "s3cret", identity="viewer-1", room="landline-c_1", ttl_s=600, now=1000)
    claims = _claims(token, "s3cret")
    assert claims == {"sub": "viewer-1", "iss": "APIkey", "nbf": 1000, "exp": 1600, "video": {
        "roomJoin": True, "room": "landline-c_1", "canPublish": False, "canSubscribe": True,
        "canPublishData": False}}
    avatar = _claims(livekit_token("APIkey", "s3cret", identity=AVATAR, room="r", ttl_s=60, publish=True,
                                   subscribe=False, data=True, kind="agent",
                                   attributes={"lk.publish_on_behalf": AGENT}), "s3cret")
    assert avatar["video"]["canPublishSources"] == ["camera", "microphone"]
    assert avatar["kind"] == "agent" and avatar["attributes"] == {"lk.publish_on_behalf": AGENT}


def test_avatars_are_off_without_every_livekit_value():
    base = Settings(database_url=None, web_url="", transport="stub", boson_api_key="k", livekit_url="wss://x",
                    livekit_api_key="a", livekit_api_secret="s")
    assert AvatarConfig.from_settings(base).max_s == 1020   # the renter call's 900 s and a margin
    for missing in ("boson_api_key", "livekit_url", "livekit_api_key", "livekit_api_secret"):
        assert AvatarConfig.from_settings(replace(base, **{missing: None})) is None
    assert AvatarConfig.from_settings(replace(base, renter_call_max_s=9000)).max_s == 3600
    assert not Avatars.from_settings(replace(base, livekit_url=None)).ready


def test_browser_audio_goes_to_the_avatar_as_it_is():
    assert ToAvatar(PCM_24K)(_pcm(1, -2, 300)) == np.array([1, -2, 300], dtype="<i2").tobytes()


def test_phone_audio_is_decoded_from_mulaw_and_stretched_to_24k():
    to = ToAvatar(PCMU)
    # μ-law 0xFF is silence, 0x80 the loudest positive sample, 0x00 the loudest negative.
    out = np.frombuffer(to(base64.b64encode(bytes([0xFF, 0x80, 0x00])).decode()), dtype="<i2")
    assert len(out) == 9
    assert list(out[0:3]) == [0, 0, 0]
    assert list(out[3:6]) == [10708, 21416, 32124]          # a straight line up to the loud sample
    assert out[8] == -32124
    # The next chunk picks up from where this one ended, so the edge doesn't click.
    nxt = np.frombuffer(to(base64.b64encode(bytes([0xFF])).decode()), dtype="<i2")
    assert list(nxt) == [-21416, -10708, 0]


# ── one avatar's life ───────────────────────────────────────────────────────

async def test_a_page_asking_starts_the_avatar_in_the_calls_own_room():
    lk = FakeLiveKit()
    avatars, face = await _live(lk)
    url, token = lk.joined[0]
    assert url == CONFIG.livekit_url
    agent = _claims(token)
    assert agent["sub"] == AGENT and agent["video"]["room"] == "landline-c_test123456"
    assert agent["video"]["canPublishData"] and not agent["video"]["canPublish"]
    created = lk.requests[0]
    assert (created.method, created.url.path) == ("POST", "/v1/avatar/sessions")
    assert created.headers["authorization"] == "Bearer bai-test"
    body = json.loads(created.content)
    assert body["avatar_id"] == "builtin_claire_v1" and body["max_duration_seconds"] == 1020
    transport = body["transport"]
    assert transport["room_name"] == "landline-c_test123456" and transport["url"] == CONFIG.livekit_url
    assert (transport["participant_identity"], transport["publisher_identity"]) == (AVATAR, AGENT)
    assert _claims(transport["participant_token"])["sub"] == AVATAR
    # A second ask doesn't start a second avatar.
    face.want()
    await asyncio.sleep(0.02)
    assert len(lk.requests) == 1 and len(lk.joined) == 1
    viewer = face.viewer()
    assert viewer["identity"] == AVATAR and viewer["room"] == "landline-c_test123456"
    seen = _claims(viewer["token"])
    assert seen["sub"].startswith("viewer-") and seen["video"]["canSubscribe"]
    assert not seen["video"]["canPublish"] and not seen["video"]["canPublishData"]
    await avatars.aclose()


async def test_each_response_goes_to_the_avatar_as_one_stream():
    lk = FakeLiveKit()
    avatars, face = await _live(lk)
    local = lk.rooms[0].local_participant
    face.response_started()
    face.audio(_pcm(1, 2))
    face.audio(_pcm(3))
    face.response_done()
    face.response_started()
    face.audio(_pcm(4))
    face.response_done()
    await _until(lambda: len(local.streams) == 2 and local.streams[1].closed)
    first, second = local.streams
    assert first.topic == "lk.audio_stream" and first.to == [AVATAR]
    assert first.attributes == {"sample_rate": "24000", "num_channels": "1"}
    assert b"".join(first.chunks) == np.array([1, 2, 3], dtype="<i2").tobytes() and first.closed
    assert b"".join(second.chunks) == np.array([4], dtype="<i2").tobytes()
    await avatars.aclose()


async def test_audio_from_before_the_avatar_was_live_is_not_sent():
    lk = FakeLiveKit()
    avatars = lk.avatars()
    face = avatars.open("c_test123456", "s_test123456", PCM_24K)
    face.response_started()          # this response began before any page asked
    face.want()
    await _until(lambda: face.state == "live")
    face.audio(_pcm(9))
    face.response_done()
    await asyncio.sleep(0.02)
    assert lk.rooms[0].local_participant.streams == []
    await avatars.aclose()


async def test_barge_in_drops_unsent_audio_and_clears_the_avatar():
    lk = FakeLiveKit()
    avatars, face = await _live(lk)
    local = lk.rooms[0].local_participant
    face.response_started()
    face.audio(_pcm(1))
    await _until(lambda: local.streams and local.streams[0].chunks)
    face.audio(_pcm(2))
    face.barge_in()                  # before the sender got to the second chunk
    face.audio(_pcm(3))              # the rest of the cancelled response
    await _until(lambda: local.rpcs)
    assert local.rpcs == [(AVATAR, "lk.clear_buffer", "")]
    assert local.streams[0].closed and b"".join(local.streams[0].chunks) == np.array([1], "<i2").tobytes()
    await avatars.aclose()


async def test_after_a_goodbye_the_avatar_finishes_then_leaves():
    lk = FakeLiveKit()
    avatars, face = await _live(lk)
    room, local = lk.rooms[0], lk.rooms[0].local_participant
    face.response_started()
    face.audio(_pcm(5))
    face.response_done()
    await _until(lambda: local.streams and local.streams[0].closed)
    avatars.end("s_test123456", face, play_out=True)
    assert avatars.get("s_test123456") is None   # no page can ask for it now
    await asyncio.sleep(0.05)
    assert not room.disconnected                # still saying goodbye
    assert local.methods["lk.playback_finished"](Played("someone-else")) == "reject"
    assert local.methods["lk.playback_finished"](Played()) == "ok"
    await _until(lambda: room.disconnected)
    assert [(r.method, r.url.path) for r in lk.requests][-1] == ("DELETE", "/v1/avatar/sessions/avrt_1")
    assert face.state == "closed"
    await avatars.aclose()


async def test_a_hang_up_releases_the_avatar_at_once(monkeypatch):
    monkeypatch.setattr(av, "END_GRACE_S", 30)
    lk = FakeLiveKit()
    avatars, face = await _live(lk)
    face.response_started()
    face.audio(_pcm(5))
    face.response_done()
    await face.close(play_out=False)
    assert lk.rooms[0].disconnected and lk.requests[-1].method == "DELETE"


async def test_a_refused_start_is_tried_again_later_and_the_call_never_notices():
    now = [100.0]
    lk = FakeLiveKit(status=429)
    avatars = lk.avatars(clock=lambda: now[0])
    face = avatars.open("c_test123456", "s_test123456", PCMU)
    face.want()
    await _until(lambda: face.state == "failed")
    assert lk.rooms[0].disconnected            # nothing left in the room
    face.response_started()
    face.audio("AAAA")                         # the call goes on; nothing is queued
    assert face.queue.empty()
    face.want()
    assert face.state == "failed"              # too soon
    now[0] += av.RETRY_S
    lk.status = 201
    face.want()
    await _until(lambda: face.state == "live")
    assert len(lk.joined) == 2
    await avatars.aclose()
    assert lk.rooms[1].disconnected


async def test_a_call_that_ends_while_the_avatar_starts_leaves_nothing_behind():
    lk = FakeLiveKit()
    gate = asyncio.Event()

    async def slow_join(url, token):
        await gate.wait()
        return await lk.join(url, token)

    http = httpx.AsyncClient(transport=httpx.MockTransport(lk.boson))
    avatars = Avatars(CONFIG, join_room=slow_join, http=http)
    face = avatars.open("c_test123456", "s_test123456", PCM_24K)
    face.want()
    await asyncio.sleep(0.01)
    await face.close(play_out=True)
    assert face.state == "closed" and lk.requests == []
    await avatars.aclose()


# ── the endpoint and a whole call ───────────────────────────────────────────

class GatedHiggs(FakeHiggs):
    """Plays its script only once the session's avatar is live, as Higgs would for the next response."""

    def __init__(self, app, script):
        super().__init__(script=script)
        self.app = app

    async def events(self):
        while not any(face.state == "live" for face in self.app.state.avatars.live.values()):
            await asyncio.sleep(0.005)
        async for event in super().events():
            yield event


def _wait(check, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not check():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.01)


def test_the_endpoint_says_when_there_is_no_avatar_to_show():
    with _client() as c:
        r = c.post("/api/sessions/s_nobody12345/avatar")
        assert (r.status_code, r.json()) == (404, {"reason": "off"})
        c.app.state.avatars = FakeLiveKit().avatars()
        r = c.post("/api/sessions/s_nobody12345/avatar")
        assert (r.status_code, r.json()) == (404, {"reason": "no_call"})


def test_a_browser_call_shows_the_concierge_lip_syncing_it():
    spoken = np.array([7, -7, 70], dtype="<i2").tobytes()
    lk = FakeLiveKit()
    with _client(judge_audio_ready=True) as c:
        fake = GatedHiggs(c.app, script=[
            {"type": "response.created", "response": {"id": "r1"}},
            {"type": "response.output_audio.delta", "delta": base64.b64encode(spoken).decode()},
            {"type": "response.done", "response": {"id": "r1", "output": []}},
        ])
        c.app.state.connect_higgs, c.app.state.avatars = fake.connect, lk.avatars()
        start = c.post("/api/judge/start").json()
        sid = start["session_id"]
        with c.websocket_connect(start["ws_url"]) as ws:
            _wait(lambda: c.app.state.avatars.get(sid) is not None)
            r = c.post(f"/api/sessions/{sid}/avatar")
            assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
            join = r.json()
            assert join["url"] == CONFIG.livekit_url and join["identity"] == AVATAR
            assert _claims(join["token"])["video"] == {"roomJoin": True, "room": join["room"],
                                                       "canPublish": False, "canSubscribe": True,
                                                       "canPublishData": False}
            assert ws.receive_bytes() == spoken          # the renter still hears it from the call
            local = lk.rooms[0].local_participant
            _wait(lambda: local.streams and local.streams[0].closed)
            assert b"".join(local.streams[0].chunks) == spoken   # and the avatar says it too
            _hang_up_and_wait(c, ws, sid)
        _wait(lambda: lk.rooms[0].disconnected)
        assert lk.requests[-1].method == "DELETE"
        assert c.post(f"/api/sessions/{sid}/avatar").json() == {"reason": "no_call"}


def test_a_failed_start_answers_unavailable():
    lk = FakeLiveKit(status=503)
    with _client(judge_audio_ready=True) as c:
        c.app.state.connect_higgs, c.app.state.avatars = FakeHiggs().connect, lk.avatars()
        start = c.post("/api/judge/start").json()
        sid = start["session_id"]
        with c.websocket_connect(start["ws_url"]) as ws:
            _wait(lambda: c.app.state.avatars.get(sid) is not None)
            assert c.post(f"/api/sessions/{sid}/avatar").status_code == 200   # starting
            _wait(lambda: c.app.state.avatars.get(sid).state == "failed")
            r = c.post(f"/api/sessions/{sid}/avatar")
            assert r.status_code == 503 and r.json() == {"reason": "unavailable", "retry_after_s": av.RETRY_S}
            _hang_up_and_wait(c, ws, sid)


@pytest.mark.parametrize("fmt", [PCM_24K, PCMU])
async def test_nothing_reaches_livekit_until_a_page_asks(fmt):
    lk = FakeLiveKit()
    avatars = lk.avatars()
    face = avatars.open("c_test123456", "s_test123456", fmt)
    face.response_started()
    face.audio("AAAA")
    face.response_done()
    face.barge_in()
    await avatars.aclose()
    assert lk.joined == [] and lk.requests == [] and face.state == "closed"

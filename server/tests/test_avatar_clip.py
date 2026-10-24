"""Higgs Avatar clips after a call (voice/avatar_clip.py). No network: a fake video client, or httpx's
mock transport for the one request shape a sim call sends."""
import asyncio
import io
import wave
from datetime import UTC, datetime

import httpx
from fastapi.testclient import TestClient

from landline.app import create_app
from landline.config import Settings
from landline.contracts import Listing
from landline.db.catalogue import Catalogue
from landline.db.rows import AudioRow, AvatarClip
from landline.voice.avatar_clip import SAMPLE_FACE, Busy, Clips, HiggsVideos, agent_script, agent_wav

NOW = datetime(2026, 10, 24, tzinfo=UTC)

RATE = 8000


def _catalogue() -> Catalogue:
    listings = [Listing(listing_id=f"t:{i}", source="t", source_id=str(i), url=f"https://example.com/{i}",
                        first_seen=NOW, last_seen=NOW, address=f"{i} Test St", lat=43.65, lng=-79.38,
                        rent=2000, monthly_cost=2000, beds=1, sqft=700) for i in (1, 2)]
    return Catalogue(listings, "test")


def _pcm(call_id: str, seq: int, seconds: int, sample: bytes = b"\x01\x00", *, track: str = "agent",
         rate: int = RATE) -> AudioRow:
    return AudioRow(call_id, track, seq, 0, "pcm16", rate, sample * (rate * seconds))


class Memory:
    """call_avatar without Postgres. A second claim of the same call does nothing."""

    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}
        self.audio: dict[str, list[AudioRow]] = {}
        self.lines: dict[str, list[str]] = {}
        self.order: list[tuple[str, str, str, str | None]] = []

    async def claim(self, call_id: str) -> bool:
        if call_id in self.rows:
            return False
        self.rows[call_id] = {"status": "queued", "video_id": None, "error": None, "video": None}
        return True

    async def save(self, call_id: str, *, status: str, boson_video_id: str | None = None,
                   error: str | None = None, video: bytes | None = None) -> None:
        row = self.rows[call_id]
        row["status"] = status
        if boson_video_id is not None:
            row["video_id"] = boson_video_id
        row["error"] = error
        if video is not None:
            row["video"] = video

    async def agent_audio(self, call_id: str) -> list[AudioRow]:
        return self.audio.get(call_id, [])

    async def agent_lines(self, call_id: str) -> list[str]:
        return self.lines.get(call_id, [])

    def add(self, session_id: str, call_id: str, role: str, listing_id: str | None, status: str,
            video: bytes | None = None) -> None:
        self.rows[call_id] = {"status": status, "video_id": None, "error": None, "video": video}
        self.order.append((session_id, call_id, role, listing_id))

    async def clips(self, session_id: str) -> list[AvatarClip]:
        rows = [item for item in self.order if item[0] == session_id]
        rows.sort(key=lambda item: (0 if item[2] == "renter" else 1, self.order.index(item)))
        return [AvatarClip(call_id=call_id, role=role, listing_id=listing_id,
                           status=self.rows[call_id]["status"])
                for _, call_id, role, listing_id in rows]

    async def mp4(self, call_id: str, session_id: str | None) -> bytes | None:
        if session_id is not None and not any(s == session_id and c == call_id for s, c, _, _ in self.order):
            return None
        row = self.rows.get(call_id)
        if row is None or row["status"] != "ready" or not row["video"]:
            return None
        return row["video"]


class Fake:
    """Higgs /v1/videos, in process. `hold` pauses inside create; `busy` raises 429 that many times first."""

    def __init__(self, *, error: Exception | None = None, busy: int = 0, hold: asyncio.Event | None = None):
        self.calls: list[dict] = []
        self.error = error
        self.busy = busy
        self.hold = hold
        self.mp4 = b"mp4-bytes"

    async def create(self, *, image_url: str, wav: bytes | None, text: str | None) -> str:
        self.calls.append({"image": image_url, "wav": wav, "text": text})
        if self.busy:
            self.busy -= 1
            raise Busy(0)
        if self.hold is not None:
            await self.hold.wait()
        if self.error is not None:
            raise self.error
        return "vid"

    async def status(self, video_id: str) -> tuple[str, str | None]:
        return "completed", None

    async def content(self, video_id: str) -> bytes:
        return self.mp4

    async def aclose(self) -> None:
        return None


def _clips(store: Memory, client: Fake | HiggsVideos, key: str | None = "key") -> Clips:
    return Clips(store, key, SAMPLE_FACE, client)


def test_agent_chunks_become_one_wav_and_stop_at_60_seconds():
    rows = [
        _pcm("c", 2, 61, b"\x02\x00"),          # later in the call, stored first
        _pcm("c", 0, 30, track="renter"),       # the other person: not in the clip
        _pcm("c", 1, 1, b"\x01\x00"),           # Landline's first second
        _pcm("c", 3, 1, rate=24000),            # a different rate is dropped, not mixed in
    ]
    wav = agent_wav(rows)
    assert wav is not None
    with wave.open(io.BytesIO(wav)) as parsed:
        assert parsed.getnchannels() == 1
        assert parsed.getframerate() == RATE
        assert parsed.getnframes() == RATE * 60
        frames = parsed.readframes(parsed.getnframes())
    assert frames[:2] == b"\x01\x00"             # seq order, not the order the chunks arrived
    assert frames[RATE * 2:RATE * 2 + 2] == b"\x02\x00"
    assert agent_wav([]) is None
    assert agent_script(["  hello  ", "", "there"]) == "hello there"
    assert len(agent_script(["x" * 6000]) or "") == 5000


async def test_a_second_schedule_does_not_start_a_second_job():
    store, hold = Memory(), asyncio.Event()
    store.audio["c1"] = [_pcm("c1", 1, 1)]
    fake = Fake(hold=hold)
    clips = _clips(store, fake)
    first = clips.schedule("c1")
    assert first is not None
    for _ in range(50):
        if fake.calls:
            break
        await asyncio.sleep(0)
    else:
        raise AssertionError("the first render never asked Higgs")
    second = clips.schedule("c1")
    await asyncio.sleep(0)
    hold.set()
    await first
    assert second is not None
    await second
    assert len(fake.calls) == 1
    assert store.rows["c1"]["status"] == "ready"
    assert store.rows["c1"]["video"] == b"mp4-bytes"


async def test_a_sim_call_submits_input_tts_and_no_audio_file():
    seen: dict[str, bytes] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            seen["body"] = request.content
            return httpx.Response(200, json={"id": "vid"})
        if request.url.path.endswith("/content"):
            return httpx.Response(200, content=b"mp4")
        return httpx.Response(200, json={"status": "completed"})

    store = Memory()
    store.lines["c1"] = ["Still available?", "Dogs are fine."]
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    clips = _clips(store, HiggsVideos("key", http))
    task = clips.schedule("c1")
    assert task is not None
    await task
    await http.aclose()
    body = seen["body"]
    assert b"input_tts" in body
    assert b"Still+available" in body
    assert b"Dogs+are+fine" in body
    assert b"landline.wav" not in body
    assert store.rows["c1"]["status"] == "ready"


async def test_a_render_error_leaves_the_call_ended():
    ended = {"reason": "hangup"}
    store = Memory()
    store.audio["c1"] = [_pcm("c1", 1, 1)]
    clips = _clips(store, Fake(error=RuntimeError("Higgs down")))
    task = clips.schedule("c1")
    assert task is not None
    await task
    assert store.rows["c1"]["status"] == "failed"
    assert "Higgs down" in (store.rows["c1"]["error"] or "")
    assert ended == {"reason": "hangup"}


async def test_a_busy_higgs_is_retried():
    store = Memory()
    store.audio["c1"] = [_pcm("c1", 1, 1)]
    fake = Fake(busy=1)
    task = _clips(store, fake).schedule("c1")
    assert task is not None
    await task
    assert len(fake.calls) == 2
    assert store.rows["c1"]["status"] == "ready"


def test_the_session_route_lists_the_renter_first_and_serves_the_mp4_only_when_ready():
    store = Memory()
    store.add("s1", "c_list", "listing", "t:1", "ready", b"listing-mp4")
    store.add("s1", "c_rent", "renter", None, "rendering")
    store.add("s2", "c_else", "listing", "t:2", "ready", b"other-session")
    with TestClient(create_app(Settings(database_url=None, web_url="http://localhost:3000", transport="stub"),
                               _catalogue())) as client:
        client.app.state.clips = _clips(store, Fake())
        body = client.get("/api/sessions/s1/avatars").json()
        assert [(row["call_id"], row["address"], row["status"]) for row in body] == [
            ("c_rent", None, "rendering"),
            ("c_list", "1 Test St", "ready"),
        ]
        assert client.get("/api/sessions/s1/avatars/c_rent").status_code == 404
        ready = client.get("/api/sessions/s1/avatars/c_list")
        assert ready.status_code == 200
        assert ready.content == b"listing-mp4"
        assert ready.headers["content-type"].startswith("video/mp4")
        assert client.get("/api/sessions/s1/avatars/c_else").status_code == 404

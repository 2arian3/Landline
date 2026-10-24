"""Higgs Avatar clips after a call (voice/avatar_clip.py). No network and no ffmpeg: a fake video client
and a fake compositor. One httpx mock checks the speech and video request shapes."""
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
from landline.db.rows import AudioRow, AvatarClip, TurnRow
from landline.voice.avatar_clip import (
    MALE_FACE,
    SAMPLE_FACE,
    Busy,
    Clips,
    HiggsVideos,
    gender_of_voice,
    pair_from_audio,
    pair_from_pieces,
    pcm_of_wav,
    side_faces,
)

NOW = datetime(2026, 10, 24, tzinfo=UTC)

RATE = 8000


def _catalogue() -> Catalogue:
    listings = [Listing(listing_id=f"t:{i}", source="t", source_id=str(i), url=f"https://example.com/{i}",
                        first_seen=NOW, last_seen=NOW, address=f"{i} Test St", lat=43.65, lng=-79.38,
                        rent=2000, monthly_cost=2000, beds=1, sqft=700) for i in (1, 2)]
    return Catalogue(listings, "test")


def _pcm(call_id: str, seq: int, seconds: int, sample: bytes = b"\x01\x00", *, track: str = "agent",
         rate: int = RATE, start_ms: int = 0) -> AudioRow:
    return AudioRow(call_id, track, seq, start_ms, "pcm16", rate, sample * (rate * seconds))


def _turn(speaker: str, text: str, start: int | None = None, end: int | None = None, idx: int = 0) -> TurnRow:
    return TurnRow("c", idx, speaker, text, None, None, start, end)


def _tone(rate: int, ms: int, sample: bytes = b"\x01\x00") -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(sample * (rate * ms // 1000))
    return buf.getvalue()


def _frames(wav: bytes) -> bytes:
    pcm, _rate = pcm_of_wav(wav)
    return pcm


class Memory:
    """call_avatar without Postgres. A second claim of the same call does nothing."""

    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}
        self.audio: dict[str, list[AudioRow]] = {}
        self.turns: dict[str, list[TurnRow]] = {}
        self.roles: dict[str, str] = {}
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

    async def call(self, call_id: str) -> tuple[str, list[AudioRow], list[TurnRow]]:
        return self.roles.get(call_id, "renter"), self.audio.get(call_id, []), self.turns.get(call_id, [])

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
    """Higgs /v1/videos and /v1/audio/speech, in process. `hold` pauses inside create; `busy` raises 429
    that many times first."""

    def __init__(self, *, error: Exception | None = None, busy: int = 0, hold: asyncio.Event | None = None):
        self.calls: list[dict] = []
        self.spoken: list[tuple[str, str]] = []
        self.error = error
        self.busy = busy
        self.hold = hold
        self.mp4 = b"mp4-bytes"
        self._n = 0

    async def speak(self, text: str, voice: str) -> bytes:
        self.spoken.append((text, voice))
        return _tone(RATE, 500)

    async def create(self, *, image_url: str, wav: bytes | None, text: str | None,
                     voice: str = "default") -> str:
        self.calls.append({"image": image_url, "wav": wav, "text": text, "voice": voice})
        if self.busy:
            self.busy -= 1
            raise Busy(0)
        if self.hold is not None:
            await self.hold.wait()
        if self.error is not None:
            raise self.error
        self._n += 1
        return f"vid{self._n}"

    async def status(self, video_id: str) -> tuple[str, str | None]:
        return "completed", None

    async def content(self, video_id: str) -> bytes:
        return self.mp4

    async def aclose(self) -> None:
        return None


class Joined:
    """The compositor. Records the spans and returns one file, so the tests never run ffmpeg."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def join(self, left: bytes, right: bytes, *, left_spans, right_spans, duration_ms: int) -> bytes:
        self.calls.append({"left": left, "right": right, "left_spans": tuple(left_spans),
                           "right_spans": tuple(right_spans), "duration_ms": duration_ms})
        return b"side-by-side"


def _clips(store: Memory, client: Fake | HiggsVideos, key: str | None = "key",
           joined: Joined | None = None) -> Clips:
    return Clips(store, key, SAMPLE_FACE, client, joined or Joined())


def test_a_recorded_call_keeps_each_person_in_place_and_stops_at_60_seconds():
    rows = [
        _pcm("c", 1, 1, b"\x02\x00", start_ms=2000),          # the concierge, two seconds in
        _pcm("c", 0, 1, b"\x01\x00", start_ms=0),             # the concierge's first second
        _pcm("c", 2, 3, b"\x03\x00", track="renter"),         # the renter, the whole three seconds
        _pcm("c", 3, 1, rate=24000, track="renter"),          # a different rate is dropped
    ]
    pair = pair_from_audio(rows, [_turn("renter", "hello", 1000, 2000)], "renter")
    assert pair is not None
    renter, agent = pair
    assert renter.duration_ms == 3000 and agent.duration_ms == 3000
    assert renter.spans == ((1000, 2000),)
    assert agent.spans == ((0, 1000), (2000, 3000))
    heard = _frames(renter.wav)
    assert heard[:RATE * 2] == b"\x00\x00" * RATE          # before their turn
    assert heard[RATE * 2:RATE * 4] == b"\x03\x00" * RATE  # the one second they spoke
    assert heard[RATE * 4:] == b"\x00\x00" * RATE
    said = _frames(agent.wav)
    assert said[:2] == b"\x01\x00"
    assert said[RATE * 2:RATE * 2 + 2] == b"\x00\x00"      # quiet while the renter talks
    assert said[RATE * 4:RATE * 4 + 2] == b"\x02\x00"
    assert pair_from_audio([], [], "renter") is None

    long = pair_from_audio([_pcm("c", 0, 61, b"\x04\x00")], [], "renter")
    assert long is not None
    assert long[1].duration_ms == 60_000
    assert len(_frames(long[1].wav)) == RATE * 60 * 2


def test_text_turns_are_laid_back_to_back_and_a_line_past_60_seconds_is_dropped():
    second = b"\x01\x00" * RATE
    pieces = [("agent", second, RATE), ("landlord", b"\x02\x00" * RATE, RATE),
              ("agent", b"\x03\x00" * (RATE * 60), RATE)]
    pair = pair_from_pieces(pieces, "listing")
    assert pair is not None
    caller, landlord = pair
    assert caller.spans == ((0, 1000),)
    assert landlord.spans == ((1000, 2000),)
    assert _frames(caller.wav) == second + bytes(RATE * 2)
    assert _frames(landlord.wav) == bytes(RATE * 2) + b"\x02\x00" * RATE
    assert pair_from_pieces([("agent", b"\x01\x00" * (RATE * 61), RATE)], "listing") is None


def test_the_two_faces_differ_and_follow_the_known_gender():
    assert side_faces("renter", "female", None, SAMPLE_FACE) == (MALE_FACE, SAMPLE_FACE)
    assert side_faces("listing", "female", "male", SAMPLE_FACE) == (SAMPLE_FACE, MALE_FACE)
    assert side_faces("listing", "male", "male", SAMPLE_FACE) == (MALE_FACE, SAMPLE_FACE)
    assert gender_of_voice("oliver") == "male"
    assert gender_of_voice(" Marcus ") == "male"
    assert gender_of_voice("default") == "female"
    assert gender_of_voice("chloe") == "female"


async def test_a_second_schedule_does_not_start_a_second_job():
    store, hold = Memory(), asyncio.Event()
    store.audio["c1"] = [_pcm("c1", 1, 1)]
    fake, joined = Fake(hold=hold), Joined()
    clips = _clips(store, fake, joined=joined)
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
    assert len(fake.calls) == 2                         # one face each, not a second render of the call
    assert store.rows["c1"]["status"] == "ready"
    assert store.rows["c1"]["video"] == b"side-by-side"
    assert len(joined.calls) == 1


async def test_a_recorded_call_sends_both_faces_and_stores_the_composite():
    store = Memory()
    store.roles["c1"] = "renter"
    store.audio["c1"] = [
        _pcm("c1", 0, 1, b"\x01\x00", start_ms=0),
        _pcm("c1", 1, 1, b"\x03\x00", track="renter", start_ms=1000),
    ]
    store.turns["c1"] = [_turn("renter", "hello", 1000, 2000)]
    fake, joined = Fake(), Joined()
    task = _clips(store, fake, joined=joined).schedule("c1", agent_gender="female")
    assert task is not None
    await task
    renter, agent = fake.calls
    assert renter["image"] == MALE_FACE and agent["image"] == SAMPLE_FACE
    assert renter["text"] is None and agent["text"] is None
    assert _frames(renter["wav"])[RATE * 2:RATE * 2 + 2] == b"\x03\x00"
    assert _frames(renter["wav"])[:2] == b"\x00\x00"
    assert _frames(agent["wav"])[:2] == b"\x01\x00"
    assert _frames(agent["wav"])[RATE * 2:RATE * 2 + 2] == b"\x00\x00"
    assert joined.calls[0]["left_spans"] == ((1000, 2000),)
    assert joined.calls[0]["right_spans"] == ((0, 1000),)
    assert store.rows["c1"]["video"] == b"side-by-side"
    assert store.rows["c1"]["video_id"] == "vid1,vid2"


async def test_a_text_call_speaks_each_side_then_sends_two_wavs():
    store = Memory()
    store.roles["c1"] = "listing"
    store.turns["c1"] = [
        _turn("agent", "Still available?", idx=0),
        _turn("landlord", "Dogs are fine.", idx=1),
    ]
    fake = Fake()
    task = _clips(store, fake).schedule("c1", agent_gender="female", other_gender="male")
    assert task is not None
    await task
    assert fake.spoken == [("Still available?", "default"), ("Dogs are fine.", "oliver")]
    caller, landlord = fake.calls
    assert caller["image"] == SAMPLE_FACE and landlord["image"] == MALE_FACE
    assert _frames(caller["wav"]) == b"\x01\x00" * (RATE // 2) + bytes(RATE)
    assert _frames(landlord["wav"]) == bytes(RATE) + b"\x01\x00" * (RATE // 2)
    assert store.rows["c1"]["video"] == b"side-by-side"


async def test_a_video_request_uploads_the_wav_and_speech_returns_audio():
    seen: list[tuple[str, bytes]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.path, request.content))
        if request.url.path.endswith("/speech"):
            return httpx.Response(200, content=_tone(RATE, 200))
        if request.url.path.endswith("/content"):
            return httpx.Response(200, content=b"mp4")
        if request.method == "POST":
            return httpx.Response(200, json={"id": "vid"})
        return httpx.Response(200, json={"status": "completed"})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    videos = HiggsVideos("key", http)
    spoken = await videos.speak("Hello", "oliver")
    assert pcm_of_wav(spoken)[1] == RATE
    await videos.create(image_url=MALE_FACE, wav=spoken, text=None)
    await http.aclose()
    speech, video = seen
    assert speech[0].endswith("/audio/speech")
    assert b"oliver" in speech[1] and b"Hello" in speech[1]
    assert video[0].endswith("/videos")
    assert b"landline.wav" in video[1]
    assert b"coaching-centered" in video[1]
    assert b"input_tts" not in video[1]


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
    assert len(fake.calls) == 3                        # the first face once more, then the second face
    assert store.rows["c1"]["status"] == "ready"
    assert store.rows["c1"]["video"] == b"side-by-side"


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

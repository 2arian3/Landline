"""A side-by-side clip of a finished call, stored for the board and admin.

Not a live face. After the call, each person is sent to POST /v1/videos (Higgs Avatar, async): the renter
and the concierge on an inbound call, Landline's caller and the landlord on an outbound one. Each request
gets a WAV of the same length, at most 60 seconds, with that person's speech in place and silence
everywhere else. ffmpeg then holds each side on its first frame except while they are speaking, and puts
the two pictures side by side. One MP4 is kept on the call.

A judge-mode listing call is text, so each line is spoken first (POST /v1/audio/speech) and those clips
are laid back to back on the same kind of timeline. A line that would run past 60 seconds is left out.

The face is chosen before each video request. The concierge and the caller follow their Higgs voice. A
sim landlord follows the persona. A renter, and a landlord on the phone, have no gender stored, so they
get the still the other person is not using.

Nothing here can end a call or hold one up. A missing key, a missing face, or a failed render is a
row on the call and a log line.
"""
from __future__ import annotations

import asyncio
import contextlib
import io
import json
import logging
import shutil
import subprocess
import tempfile
import wave
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import httpx

from landline.db.pool import Database
from landline.db.rows import AudioRow, AvatarClip, TurnRow
from landline.voice.recorder import pcm16

log = logging.getLogger(__name__)

BOSON = "https://api.boson.ai/v1"
SAMPLE_FACE = "https://docs.boson.ai/public/avatar/sample.jpg"   # Boson's sample: a woman
# Boson's published Higgs Avatar still of a man. The video API takes a picture, not an avatar id.
MALE_FACE = "https://www.boson.ai/images/higgs-avatar/roles/coaching-centered.jpg"
MALE_VOICES = frozenset({"jake", "marcus", "oliver"})
MALE_TTS = "oliver"       # a male Higgs TTS preset, used when a text clip has no recording
SIZE = "480x640"          # each face; the stored clip is two of these side by side
OUT_W, OUT_H = 480, 640
MAX_S = 60                # Higgs Avatar refuses a longer driving clip
MAX_TTS = 5_000           # input_tts.input and /v1/audio/speech, the same cap
POLL_TRIES = 40
CREATE_TRIES = 5


@dataclass(frozen=True)
class Side:
    """One person on the shared clock: a mono PCM16 WAV, and when they are speaking inside it."""

    wav: bytes
    spans: tuple[tuple[int, int], ...]   # milliseconds, speech only
    duration_ms: int


def gender_of_voice(voice: str) -> str:
    """female or male, from a Higgs preset. `default` is the sample woman's voice."""
    return "male" if voice.strip().lower() in MALE_VOICES else "female"


def face_for(gender: str | None, female: str) -> str:
    """The still sent as ref_image. A man gets the male still; anyone else keeps the configured face."""
    if gender == "male":
        return MALE_FACE
    return female or SAMPLE_FACE


def other_face(used: str, female: str) -> str:
    """The still `used` is not, so the two people in one clip are not the same photo."""
    woman = female or SAMPLE_FACE
    return woman if used == MALE_FACE else MALE_FACE


def tts_voice_for(gender: str | None) -> str:
    """The voice that speaks a text line, so the mouth matches the face."""
    return MALE_TTS if gender == "male" else "default"


def tracks_for(role: str) -> tuple[str, str]:
    """Left, right. Inbound is the renter and the concierge; outbound is the caller and the landlord."""
    if role == "listing":
        return "agent", "landlord"
    return "renter", "agent"


def side_faces(role: str, agent_gender: str | None, other_gender: str | None, female: str) -> tuple[str, str]:
    """The left and right stills. A person with no gender gets the face the other one isn't using, and a
    known gender that would copy the other face does too."""
    agent = face_for(agent_gender, female)
    other = face_for(other_gender, female) if other_gender else other_face(agent, female)
    if other == agent:
        other = other_face(agent, female)
    if role == "listing":
        return agent, other
    return other, agent


def pair_from_audio(rows: Sequence[AudioRow], turns: Sequence[TurnRow], role: str,
                    max_s: int = MAX_S) -> tuple[Side, Side] | None:
    """Both people on one clock, cut at `max_s`. Each WAV is that long: their speech in place, silence
    elsewhere. The agent's spans are where their chunks sit. The other person's spans are their turns
    (`audio_start_ms` / `audio_end_ms`), and their audio outside those turns is silenced. None when this
    call has no audio at one rate."""
    left_name, right_name = tracks_for(role)
    names = {left_name, right_name}
    rate = _rate(rows, names)
    if rate is None:
        return None
    end = 0
    for row in rows:
        if row.track in names and row.rate == rate:
            end = max(end, _chunk_end(row))
    if end <= 0:
        return None
    dur = min(end, max_s * 1000)
    return (_side(rows, turns, left_name, rate, dur), _side(rows, turns, right_name, rate, dur))


def pair_from_pieces(pieces: Sequence[tuple[str, bytes, int]], role: str,
                     max_s: int = MAX_S) -> tuple[Side, Side] | None:
    """Text turns already spoken, in order: `(speaker, pcm16, rate)`. Laid back to back. The person who
    isn't saying a line is silence, so they can be held still. A line that would run past `max_s`, and
    every line after it, is dropped. None when nothing fits."""
    if not pieces:
        return None
    rate = pieces[0][2]
    left_name, right_name = tracks_for(role)
    cap = rate * max_s
    left, right = bytearray(), bytearray()
    left_spans: list[tuple[int, int]] = []
    right_spans: list[tuple[int, int]] = []
    cursor = 0
    for speaker, pcm, piece_rate in pieces:
        if piece_rate != rate or speaker not in (left_name, right_name):
            continue
        n = len(pcm) // 2
        if n <= 0:
            continue
        if cursor >= cap or cursor + n > cap:
            break
        start = cursor * 1000 // rate
        end = (cursor + n) * 1000 // rate
        gap = bytes(n * 2)
        heard = pcm[:n * 2]
        if speaker == left_name:
            left += heard
            right += gap
            left_spans.append((start, end))
        else:
            right += heard
            left += gap
            right_spans.append((start, end))
        cursor += n
    if cursor == 0:
        return None
    dur = cursor * 1000 // rate
    return (Side(_wav(bytes(left), rate), tuple(left_spans), dur),
            Side(_wav(bytes(right), rate), tuple(right_spans), dur))


def _rate(rows: Sequence[AudioRow], tracks: set[str]) -> int | None:
    for row in sorted(rows, key=lambda r: (r.start_ms, r.seq)):
        if row.track in tracks and row.rate:
            return row.rate
    return None


def _chunk_end(row: AudioRow) -> int:
    width = 1 if row.format == "pcmu" else 2
    return row.start_ms + len(row.audio) * 1000 // (row.rate * width)


def _side(rows: Sequence[AudioRow], turns: Sequence[TurnRow], track: str, rate: int, dur_ms: int) -> Side:
    pcm, chunk_spans = _place(rows, track, rate, dur_ms)
    if track != "agent":
        spoken = _turn_spans(turns, track, dur_ms)
        if spoken:
            pcm = _keep(pcm, rate, spoken)
            return Side(_wav(pcm, rate), spoken, dur_ms)
    return Side(_wav(pcm, rate), chunk_spans, dur_ms)


def _place(rows: Sequence[AudioRow], track: str, rate: int,
           dur_ms: int) -> tuple[bytes, tuple[tuple[int, int], ...]]:
    samples = rate * dur_ms // 1000
    out = bytearray(samples * 2)
    spans: list[tuple[int, int]] = []
    for row in sorted(rows, key=lambda r: (r.start_ms, r.seq)):
        if row.track != track or row.rate != rate or row.start_ms >= dur_ms:
            continue
        pcm = pcm16(row.audio, row.format)
        at = row.start_ms * rate // 1000
        take = min(len(pcm) // 2, samples - at)
        if take <= 0:
            continue
        out[at * 2:(at + take) * 2] = pcm[:take * 2]
        spans.append((row.start_ms, row.start_ms + take * 1000 // rate))
    return bytes(out), _merge(spans)


def _turn_spans(turns: Sequence[TurnRow], speaker: str, dur_ms: int) -> tuple[tuple[int, int], ...]:
    spans = []
    for turn in turns:
        if turn.speaker != speaker or turn.audio_start_ms is None or turn.audio_end_ms is None:
            continue
        start = max(0, turn.audio_start_ms)
        end = min(dur_ms, turn.audio_end_ms)
        if end > start:
            spans.append((start, end))
    return _merge(spans)


def _keep(pcm: bytes, rate: int, spans: tuple[tuple[int, int], ...]) -> bytes:
    """Silence everywhere outside `spans`, so a turn's trailing quiet is not sent as speech."""
    out = bytearray(len(pcm))
    samples = len(pcm) // 2
    for start, end in spans:
        a = min(samples, max(0, start * rate // 1000))
        b = min(samples, max(a, end * rate // 1000))
        out[a * 2:b * 2] = pcm[a * 2:b * 2]
    return bytes(out)


def _merge(spans: list[tuple[int, int]]) -> tuple[tuple[int, int], ...]:
    if not spans:
        return ()
    ordered = sorted(spans)
    merged = [ordered[0]]
    for start, end in ordered[1:]:
        if start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return tuple(merged)


def _wav(pcm: bytes, rate: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(pcm)
    return buf.getvalue()


def pcm_of_wav(wav: bytes) -> tuple[bytes, int]:
    """Mono PCM16 and its rate. A speech response that isn't that is refused."""
    with wave.open(io.BytesIO(wav)) as parsed:
        if parsed.getnchannels() != 1 or parsed.getsampwidth() != 2:
            raise ValueError("avatar speech must be mono pcm16")
        return parsed.readframes(parsed.getnframes()), parsed.getframerate()


class Busy(Exception):
    """429 from Higgs: every replica is busy, or the key is rate limited. `retry_after` is seconds."""

    def __init__(self, retry_after: float) -> None:
        self.retry_after = retry_after


class VideoClient(Protocol):
    async def create(self, *, image_url: str, wav: bytes | None, text: str | None,
                     voice: str = "default") -> str: ...
    async def speak(self, text: str, voice: str) -> bytes: ...
    async def status(self, video_id: str) -> tuple[str, str | None]: ...
    async def content(self, video_id: str) -> bytes: ...
    async def aclose(self) -> None: ...


class Compositor(Protocol):
    def join(self, left: bytes, right: bytes, *, left_spans: Sequence[tuple[int, int]],
             right_spans: Sequence[tuple[int, int]], duration_ms: int) -> bytes: ...


class HiggsVideos:
    """POST /v1/videos and /v1/audio/speech, then poll and download. One client for the process."""

    def __init__(self, api_key: str, http: httpx.AsyncClient | None = None) -> None:
        self.api_key = api_key
        self._http = http
        self._own = http is None

    async def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=httpx.Timeout(60.0))
        return self._http

    def _auth(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"}

    async def speak(self, text: str, voice: str) -> bytes:
        """One line, as a WAV, so a text call can be laid on the same clock as a recorded one."""
        response = await (await self._client()).post(
            f"{BOSON}/audio/speech", headers={**self._auth(), "Content-Type": "application/json"},
            json={"model": "higgs-tts-3", "input": text[:MAX_TTS], "voice": voice or "default",
                  "response_format": "wav"})
        _busy(response)
        response.raise_for_status()
        return response.content

    async def create(self, *, image_url: str, wav: bytes | None, text: str | None,
                     voice: str = "default") -> str:
        data: dict[str, str] = {"model": "higgs-avatar", "size": SIZE, "ref_image": image_url}
        files = None
        if wav is not None:
            files = {"input": ("landline.wav", wav, "audio/wav")}
        else:
            speech = {"model": "higgs-tts-3", "input": text or "", "voice": voice or "default"}
            data["input_tts"] = json.dumps(speech)
        response = await (await self._client()).post(
            f"{BOSON}/videos", headers=self._auth(), data=data, files=files)
        _busy(response)
        response.raise_for_status()
        return str(response.json()["id"])

    async def status(self, video_id: str) -> tuple[str, str | None]:
        response = await (await self._client()).get(f"{BOSON}/videos/{video_id}", headers=self._auth())
        _busy(response)
        response.raise_for_status()
        body = response.json()
        return str(body.get("status") or ""), body.get("error")

    async def content(self, video_id: str) -> bytes:
        response = await (await self._client()).get(
            f"{BOSON}/videos/{video_id}/content", headers=self._auth())
        _busy(response)
        response.raise_for_status()
        return response.content

    async def aclose(self) -> None:
        if self._own and self._http is not None:
            await self._http.aclose()
            self._http = None


def _busy(response: httpx.Response) -> None:
    if response.status_code != 429:
        return
    try:
        wait = float(response.headers.get("Retry-After") or 2)
    except ValueError:
        wait = 2.0
    raise Busy(max(0.0, wait))


def _enable(spans: Sequence[tuple[int, int]]) -> str:
    """An ffmpeg expression that is true only inside `spans` (milliseconds)."""
    if not spans:
        return "0"
    return "+".join(f"between(t,{start / 1000:.3f},{end / 1000:.3f})" for start, end in spans)


class FfmpegJoin:
    """Holds each face on its first frame except while that person is speaking, then places them
    side by side."""

    def join(self, left: bytes, right: bytes, *, left_spans: Sequence[tuple[int, int]],
             right_spans: Sequence[tuple[int, int]], duration_ms: int) -> bytes:
        if shutil.which("ffmpeg") is None:
            raise RuntimeError("ffmpeg is not installed")
        dur = f"{max(duration_ms, 1) / 1000:.3f}"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            left_mp4, right_mp4 = root / "left.mp4", root / "right.mp4"
            left_png, right_png = root / "left.png", root / "right.png"
            out = root / "out.mp4"
            left_mp4.write_bytes(left)
            right_mp4.write_bytes(right)
            _ffmpeg(["-i", str(left_mp4), "-vf", r"select=eq(n\,0)", "-vframes", "1", str(left_png)])
            _ffmpeg(["-i", str(right_mp4), "-vf", r"select=eq(n\,0)", "-vframes", "1", str(right_png)])
            graph = (
                f"[0:v]scale={OUT_W}:{OUT_H}:force_original_aspect_ratio=increase,"
                f"crop={OUT_W}:{OUT_H},setsar=1[Ls];"
                f"[1:v]scale={OUT_W}:{OUT_H}:force_original_aspect_ratio=increase,"
                f"crop={OUT_W}:{OUT_H},setsar=1[Rs];"
                f"[2:v]scale={OUT_W}:{OUT_H}:force_original_aspect_ratio=increase,"
                f"crop={OUT_W}:{OUT_H},setsar=1[Lt];"
                f"[3:v]scale={OUT_W}:{OUT_H}:force_original_aspect_ratio=increase,"
                f"crop={OUT_W}:{OUT_H},setsar=1[Rt];"
                f"[Ls][Lt]overlay=eof_action=pass:enable='{_enable(left_spans)}'[L];"
                f"[Rs][Rt]overlay=eof_action=pass:enable='{_enable(right_spans)}'[R];"
                f"[L][R]hstack=inputs=2,format=yuv420p[v]"
            )
            _ffmpeg(["-loop", "1", "-t", dur, "-i", str(left_png),
                     "-loop", "1", "-t", dur, "-i", str(right_png),
                     "-i", str(left_mp4), "-i", str(right_mp4),
                     "-filter_complex", graph, "-map", "[v]", "-an", "-t", dur, str(out)])
            return out.read_bytes()


def _ffmpeg(args: list[str]) -> None:
    try:
        done = subprocess.run(["ffmpeg", "-y", *args], capture_output=True, check=False)
    except FileNotFoundError as exc:
        raise RuntimeError("ffmpeg is not installed") from exc
    if done.returncode != 0:
        err = done.stderr.decode("utf-8", "replace")[-400:]
        raise RuntimeError(f"ffmpeg failed: {err}")


class Store(Protocol):
    async def claim(self, call_id: str) -> bool: ...
    async def save(self, call_id: str, *, status: str, boson_video_id: str | None = None,
                   error: str | None = None, video: bytes | None = None) -> None: ...
    async def call(self, call_id: str) -> tuple[str, list[AudioRow], list[TurnRow]]: ...
    async def clips(self, session_id: str) -> list[AvatarClip]: ...
    async def mp4(self, call_id: str, session_id: str | None) -> bytes | None: ...


class DbStore:
    """call_avatar, call_audio, call_turns and the call's role on the app's database."""

    def __init__(self, db: Database) -> None:
        self.db = db

    async def claim(self, call_id: str) -> bool:
        return await self.db.avatars.claim(call_id)

    async def save(self, call_id: str, *, status: str, boson_video_id: str | None = None,
                   error: str | None = None, video: bytes | None = None) -> None:
        await self.db.avatars.save(
            call_id, status=status, boson_video_id=boson_video_id, error=error, video=video)

    async def call(self, call_id: str) -> tuple[str, list[AudioRow], list[TurnRow]]:
        row = await self.db.calls.get(call_id)
        role = row.role if row is not None else "renter"
        return role, await self.db.audio.list(call_id), await self.db.turns.list(call_id)

    async def clips(self, session_id: str) -> list[AvatarClip]:
        return await self.db.avatars.for_session(session_id)

    async def mp4(self, call_id: str, session_id: str | None) -> bytes | None:
        row = await self.db.avatars.file(call_id, session_id)
        if row is None or row.status != "ready" or not row.video:
            return None
        return row.video


class Clips:
    """Renders one clip per call, in the background, and serves the ones that are ready."""

    def __init__(self, store: Store | None, api_key: str | None, image_url: str | None,
                 client: VideoClient | None = None, compositor: Compositor | None = None) -> None:
        self.store = store
        self.api_key = api_key
        self.image_url = SAMPLE_FACE if image_url is None else image_url
        self.client = client if client is not None else HiggsVideos(api_key or "")
        self.compositor = compositor if compositor is not None else FfmpegJoin()
        self._tasks: set[asyncio.Task[None]] = set()

    @classmethod
    def open(cls, settings: Any, db: Database | None) -> Clips:
        image = getattr(settings, "higgs_avatar_image_url", None) or SAMPLE_FACE
        key = getattr(settings, "boson_api_key", None)
        if db is None:
            return cls(None, key, image)
        return cls(DbStore(db), key, image)

    def schedule(self, call_id: str, *, agent_gender: str | None = None,
                 other_gender: str | None = None) -> asyncio.Task[None] | None:
        """Render this call's clip. Never raises: a failure stays inside the task. None with no database
        or no running loop. A call already claimed does not start a second render.

        `agent_gender` is the concierge or the outbound caller. `other_gender` is the landlord when the
        call knows one (a sim persona). A renter, and a landlord on the phone, pass none."""
        if self.store is None:
            return None
        try:
            task = asyncio.get_running_loop().create_task(self.run(call_id, agent_gender, other_gender))
        except RuntimeError:
            log.warning("avatar clip for %s not started: no event loop", call_id)
            return None
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def run(self, call_id: str, agent_gender: str | None = None,
                  other_gender: str | None = None) -> None:
        store = self.store
        if store is None:
            return
        try:
            if not await store.claim(call_id):
                return
            if not self.api_key or not self.image_url:
                await store.save(call_id, status="skipped")
                return
            role, audio, turns = await store.call(call_id)
            pair = pair_from_audio(audio, turns, role)
            if pair is None:
                pair = pair_from_pieces(await self._spoken(turns, role, agent_gender, other_gender), role)
            if pair is None:
                await store.save(call_id, status="skipped")
                return
            left, right = pair
            left_face, right_face = side_faces(role, agent_gender, other_gender, self.image_url)
            left_id = await self._create(left.wav, left_face)
            right_id = await self._create(right.wav, right_face)
            await store.save(call_id, status="rendering", boson_video_id=f"{left_id},{right_id}")
            left_mp4, right_mp4 = await self._wait(left_id), await self._wait(right_id)
            video = await asyncio.to_thread(
                self.compositor.join, left_mp4, right_mp4, left_spans=left.spans,
                right_spans=right.spans, duration_ms=left.duration_ms)
            await store.save(call_id, status="ready", video=video)
        except Exception as exc:
            log.warning("avatar clip for %s failed: %s", call_id, exc)
            with contextlib.suppress(Exception):
                await store.save(call_id, status="failed", error=str(exc)[:300])

    async def listed(self, session_id: str) -> list[AvatarClip]:
        if self.store is None:
            return []
        return await self.store.clips(session_id)

    async def mp4(self, call_id: str, session_id: str | None) -> bytes | None:
        if self.store is None:
            return None
        return await self.store.mp4(call_id, session_id)

    async def _spoken(self, turns: Sequence[TurnRow], role: str, agent_gender: str | None,
                      other_gender: str | None) -> list[tuple[str, bytes, int]]:
        """Each line of a text call, in order, as PCM. The voice matches that side's face."""
        assert self.client is not None
        left_name, right_name = tracks_for(role)
        pieces: list[tuple[str, bytes, int]] = []
        for turn in turns:
            if turn.speaker not in (left_name, right_name):
                continue
            text = turn.text.strip()
            if not text:
                continue
            gender = agent_gender if turn.speaker == "agent" else other_gender
            pcm, rate = pcm_of_wav(await self.client.speak(text[:MAX_TTS], tts_voice_for(gender)))
            if pcm:
                pieces.append((turn.speaker, pcm, rate))
        return pieces

    async def _create(self, wav: bytes, image: str) -> str:
        assert self.client is not None
        delay = 1.0
        last: Exception | None = None
        for _ in range(CREATE_TRIES):
            try:
                return await self.client.create(image_url=image, wav=wav, text=None)
            except Busy as exc:
                last = exc
                await asyncio.sleep(exc.retry_after or delay)
                delay = min(delay * 2, 8)
        raise RuntimeError("Higgs Avatar was busy") from last

    async def _wait(self, video_id: str) -> bytes:
        assert self.client is not None
        delay = 1.0
        for _ in range(POLL_TRIES):
            try:
                status, error = await self.client.status(video_id)
            except Busy as exc:
                await asyncio.sleep(exc.retry_after or delay)
                delay = min(delay * 2, 8)
                continue
            if status == "completed":
                try:
                    return await self.client.content(video_id)
                except Busy as exc:
                    await asyncio.sleep(exc.retry_after or delay)
                    delay = min(delay * 2, 8)
                    continue
            if status == "failed":
                raise RuntimeError(error or "Higgs Avatar failed")
            await asyncio.sleep(delay)
            delay = min(delay * 1.5, 8)
        raise TimeoutError("Higgs Avatar did not finish")

    async def aclose(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        await self.client.aclose()

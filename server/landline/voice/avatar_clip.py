"""A Higgs Avatar clip of Landline's voice, made after a call and stored for the board and admin.

Not a live face. When a call's audio is stored, this sends that voice to POST /v1/videos (Higgs Avatar,
async), polls until the MP4 is ready, and keeps it on the call. The driving audio is the agent track
only: what Landline said, chunks in order, without the silence between turns, and only the first 60
seconds (the API's cap). A judge-mode listing call is text, so it has no recording; the same words
Landline said go as `input_tts` instead.

Nothing here can end a call or hold one up. A missing key, a missing face, or a failed render is a
row on the call and a log line.
"""
from __future__ import annotations

import asyncio
import contextlib
import io
import json
import logging
import wave
from collections.abc import Sequence
from typing import Any, Protocol

import httpx

from landline.db.pool import Database
from landline.db.rows import AudioRow, AvatarClip
from landline.voice.recorder import pcm16

log = logging.getLogger(__name__)

BOSON = "https://api.boson.ai/v1"
SAMPLE_FACE = "https://docs.boson.ai/public/avatar/sample.jpg"
SIZE = "480x640"          # portrait, so two clips sit side by side
MAX_S = 60                # Higgs Avatar refuses a longer driving clip
MAX_TTS = 5_000           # input_tts.input, the same cap as POST /v1/audio/speech
POLL_TRIES = 40
CREATE_TRIES = 5


class Busy(Exception):
    """429 from Higgs: every replica is busy, or the key is rate limited. `retry_after` is seconds."""

    def __init__(self, retry_after: float) -> None:
        self.retry_after = retry_after


class VideoClient(Protocol):
    async def create(self, *, image_url: str, wav: bytes | None, text: str | None) -> str: ...
    async def status(self, video_id: str) -> tuple[str, str | None]: ...
    async def content(self, video_id: str) -> bytes: ...
    async def aclose(self) -> None: ...


def agent_wav(rows: Sequence[AudioRow], max_s: int = MAX_S) -> bytes | None:
    """PCM16 WAV of the agent track, chunks in `seq` order, the first `max_s` seconds. None with no speech."""
    agent = sorted((r for r in rows if r.track == "agent"), key=lambda r: r.seq)
    if not agent:
        return None
    rate = agent[0].rate
    cap = rate * 2 * max_s
    pcm = bytearray()
    for row in agent:
        if row.rate != rate:
            continue
        pcm.extend(pcm16(row.audio, row.format))
        if len(pcm) >= cap:
            del pcm[cap:]
            break
    if not pcm:
        return None
    buf = io.BytesIO()
    with wave.open(buf, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(pcm)
    return buf.getvalue()


def agent_script(lines: Sequence[str], max_chars: int = MAX_TTS) -> str | None:
    """What Landline said, in order, cut to the TTS cap. None when it said nothing."""
    text = " ".join(part.strip() for line in lines if (part := line.strip()))
    text = text[:max_chars].strip()
    return text or None


class HiggsVideos:
    """POST /v1/videos, then poll and download. One client for the process."""

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

    async def create(self, *, image_url: str, wav: bytes | None, text: str | None) -> str:
        data: dict[str, str] = {"model": "higgs-avatar", "size": SIZE, "ref_image": image_url}
        files = None
        if wav is not None:
            files = {"input": ("landline.wav", wav, "audio/wav")}
        else:
            speech = {"model": "higgs-tts-3", "input": text or "", "voice": "default"}
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


class Store(Protocol):
    async def claim(self, call_id: str) -> bool: ...
    async def save(self, call_id: str, *, status: str, boson_video_id: str | None = None,
                   error: str | None = None, video: bytes | None = None) -> None: ...
    async def agent_audio(self, call_id: str) -> list[AudioRow]: ...
    async def agent_lines(self, call_id: str) -> list[str]: ...
    async def clips(self, session_id: str) -> list[AvatarClip]: ...
    async def mp4(self, call_id: str, session_id: str | None) -> bytes | None: ...


class DbStore:
    """call_avatar, call_audio and call_turns on the app's database."""

    def __init__(self, db: Database) -> None:
        self.db = db

    async def claim(self, call_id: str) -> bool:
        return await self.db.avatars.claim(call_id)

    async def save(self, call_id: str, *, status: str, boson_video_id: str | None = None,
                   error: str | None = None, video: bytes | None = None) -> None:
        await self.db.avatars.save(
            call_id, status=status, boson_video_id=boson_video_id, error=error, video=video)

    async def agent_audio(self, call_id: str) -> list[AudioRow]:
        return [row for row in await self.db.audio.list(call_id) if row.track == "agent"]

    async def agent_lines(self, call_id: str) -> list[str]:
        return [turn.text for turn in await self.db.turns.list(call_id) if turn.speaker == "agent"]

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
                 client: VideoClient | None = None) -> None:
        self.store = store
        self.api_key = api_key
        self.image_url = SAMPLE_FACE if image_url is None else image_url
        self.client = client if client is not None else HiggsVideos(api_key or "")
        self._tasks: set[asyncio.Task[None]] = set()

    @classmethod
    def open(cls, settings: Any, db: Database | None) -> Clips:
        image = getattr(settings, "higgs_avatar_image_url", None) or SAMPLE_FACE
        key = getattr(settings, "boson_api_key", None)
        if db is None:
            return cls(None, key, image)
        return cls(DbStore(db), key, image)

    def schedule(self, call_id: str) -> asyncio.Task[None] | None:
        """Render this call's clip. Never raises: a failure stays inside the task. None with no database
        or no running loop. A call already claimed does not start a second render."""
        if self.store is None:
            return None
        try:
            task = asyncio.get_running_loop().create_task(self.run(call_id))
        except RuntimeError:
            log.warning("avatar clip for %s not started: no event loop", call_id)
            return None
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def run(self, call_id: str) -> None:
        store = self.store
        if store is None:
            return
        try:
            if not await store.claim(call_id):
                return
            if not self.api_key or not self.image_url:
                await store.save(call_id, status="skipped")
                return
            wav = agent_wav(await store.agent_audio(call_id))
            text = None if wav is not None else agent_script(await store.agent_lines(call_id))
            if wav is None and text is None:
                await store.save(call_id, status="skipped")
                return
            video_id = await self._create(wav, text)
            await store.save(call_id, status="rendering", boson_video_id=video_id)
            mp4 = await self._wait(video_id)
            await store.save(call_id, status="ready", video=mp4)
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

    async def _create(self, wav: bytes | None, text: str | None) -> str:
        assert self.client is not None
        delay = 1.0
        last: Exception | None = None
        for _ in range(CREATE_TRIES):
            try:
                return await self.client.create(image_url=self.image_url or SAMPLE_FACE, wav=wav, text=text)
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

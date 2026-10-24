"""The call recorder (A-10): both sides of a call on one clock, where each line sits on it, and the WAV read
back. No network, no database: a fake clock drives it."""
import base64
import io
import struct
import wave

import pytest

from landline.db.rows import AudioRow
from landline.voice.higgs import PCM_24K, PCMU
from landline.voice.recorder import (
    PCM16_24K,
    PCMU_8K,
    CallRecorder,
    Chunk,
    Placed,
    duration_ms,
    format_of,
    pcm16,
    render_wav,
)


def _b64(n: int, byte: int = 0x7F) -> str:
    return base64.b64encode(bytes([byte]) * n).decode()


def _recorder(fmt=PCMU_8K):
    now = [100.0]
    return CallRecorder(fmt, clock=lambda: now[0]), now


def _bytes(chunks, track):
    return [(c.start_ms, len(c.audio)) for c in chunks if c.track == track]


def test_formats_follow_the_transport():
    assert format_of(PCMU) == PCMU_8K and format_of(PCM_24K) == PCM16_24K
    assert (PCMU_8K.bytes_per_ms, PCM16_24K.bytes_per_ms) == (8, 48)
    with pytest.raises(ValueError):
        format_of({"type": "audio/opus"})


def test_the_callers_audio_is_stored_back_to_back_from_zero_in_whole_milliseconds():
    rec, _ = _recorder()
    for _ in range(3):
        rec.heard(_b64(160))   # three 20 ms Twilio frames
    rec.heard(_b64(5))
    chunks, _ = rec.take()
    assert _bytes(chunks, "renter") == [(0, 480)]   # 60 ms; the 5 bytes wait for a whole millisecond
    rec.heard(_b64(3))
    chunks, _ = rec.take()
    assert _bytes(chunks, "renter") == [(60, 8)]
    assert rec.finish() == ([], [])


def test_the_agents_audio_is_stored_where_it_played_and_only_once_it_has():
    rec, now = _recorder()
    now[0] += 1.0                          # 1 s into the call
    rec.played(_b64(800), "a1")            # 100 ms
    rec.played(_b64(800), "a1")            # sent at once: it plays after the first
    assert rec.span("a1") == (1000, 1200)
    assert rec.take() == ([], [])          # nothing has played yet
    now[0] += 0.150
    assert _bytes(rec.take()[0], "agent") == [(1000, 1200)]   # 150 ms of 8 kHz μ-law
    now[0] += 0.350
    assert _bytes(rec.take()[0], "agent") == [(1150, 400)]
    now[0] = 102.0                         # after a silence, the next line starts when it's sent
    rec.played(_b64(80), "a2")
    assert rec.span("a2") == (2000, 2010)
    now[0] += 1
    assert _bytes(rec.take()[0], "agent") == [(2000, 80)]


def test_a_barge_in_drops_what_the_caller_never_heard_and_ends_the_line_there():
    rec, now = _recorder()
    rec.speech_started("u1", 500)
    rec.speech_stopped("u1", 1100)
    now[0] += 1.0
    rec.played(_b64(1600), "a1")           # 200 ms of agent speech from 1,000 ms
    rec.stored("a1", 3)                    # its transcript arrived: stored as turn 3 at (1000, 1200)
    now[0] += 0.050
    rec.cut()                              # the caller talks over it 50 ms in
    assert rec.span("a1") == (1000, 1050)
    assert rec.span("u1") == (500, 1100)   # a barge-in never moves the caller's lines
    chunks, moved = rec.take()
    assert _bytes(chunks, "agent") == [(1000, 400)]
    assert moved == [Placed(3, 1000, 1050)]
    assert rec.take()[1] == []             # reported once
    now[0] += 1
    rec.played(_b64(80), "a2")             # the next response starts a new run where it's sent
    assert rec.span("a2") == (2050, 2060)


def test_a_callers_line_is_placed_by_higgss_numbers_or_by_the_audio_sent_so_far():
    rec, _ = _recorder()
    rec.speech_started("u1", 1236)
    rec.speech_stopped("u1", 4500)
    assert rec.span("u1") == (1236, 4500)
    rec.heard(_b64(8000))                  # 1 s of μ-law
    rec.speech_started("u2", None)
    rec.speech_stopped("u2", "not a number")
    assert rec.span("u2") == (1000, 1000)
    assert rec.span("unknown") == (None, None) and rec.span(None) == (None, None)


def test_finish_keeps_what_played_and_drops_the_rest():
    rec, now = _recorder()
    rec.heard(_b64(13))
    now[0] += 1.0
    rec.played(_b64(800), "a1")
    rec.stored("a1", 0)
    now[0] += 0.030                        # the caller hangs up 30 ms into a 100 ms line
    chunks, moved = rec.finish()
    assert _bytes(chunks, "renter") == [(0, 8), (1, 5)]
    assert _bytes(chunks, "agent") == [(1000, 240)]
    assert moved == [Placed(0, 1000, 1030)]


def test_pcm16_is_cut_on_whole_samples():
    rec, now = _recorder(PCM16_24K)
    rec.played(_b64(4800), "a1")           # 100 ms of 24 kHz PCM16
    now[0] += 0.0101
    chunks, _ = rec.take()
    assert _bytes(chunks, "agent") == [(0, 480)]   # 10 ms; the next chunk starts on a whole millisecond
    now[0] += 1
    assert rec.take()[0] == [Chunk("agent", 10, bytes([0x7F]) * 4320)]


def test_mu_law_decodes_to_pcm16():
    assert pcm16(bytes([0xFF, 0x7F, 0x00, 0x80]), "pcmu") == struct.pack("<4h", 0, 0, -32124, 32124)
    assert pcm16(b"\x01\x02", "pcm16") == b"\x01\x02"


def _row(track, seq, start_ms, audio, fmt="pcmu", rate=8000):
    return AudioRow("c_test000001", track, seq, start_ms, fmt, rate, audio)


def _frames(wav: bytes):
    with wave.open(io.BytesIO(wav)) as w:
        n, channels, rate = w.getnframes(), w.getnchannels(), w.getframerate()
        data = w.readframes(n)
    return channels, rate, list(struct.unpack(f"<{len(data) // 2}h", data))


def test_a_recording_reads_back_as_wav_with_silence_where_nobody_spoke():
    rows = [_row("renter", 0, 0, b"\xff" * 8 + b"\x00" * 8),   # 1 ms of silence, then 1 ms loud
            _row("agent", 0, 1, b"\x80" * 8)]
    assert duration_ms(rows) == 2
    channels, rate, samples = _frames(render_wav(rows, [("renter", "landlord")]))
    assert (channels, rate, samples) == (1, 8000, [0] * 8 + [-32124] * 8)
    channels, _, samples = _frames(render_wav(rows, [("renter", "landlord"), ("agent",)]))
    assert channels == 2
    assert samples[0::2] == [0] * 8 + [-32124] * 8   # left: the caller
    assert samples[1::2] == [0] * 8 + [32124] * 8    # right: the agent, from 1 ms
    _, _, samples = _frames(render_wav(rows, [("agent",)], from_ms=1, to_ms=2))
    assert samples == [32124] * 8                    # one line's slice


def test_pcm16_chunks_read_back_unchanged():
    pcm = struct.pack("<48h", *range(48))            # 2 ms at 24 kHz
    channels, rate, samples = _frames(render_wav([_row("renter", 0, 1, pcm, "pcm16", 24000)], [("renter",)]))
    assert (channels, rate, samples) == (1, 24000, [0] * 24 + list(range(48)))

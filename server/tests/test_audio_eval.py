"""The audio sim's measurement code (E-04) with no network: codecs, the Ear's turn-taking on a fake clock,
and the timing math on hand-built timelines."""
import asyncio
import base64

from evals.audio_clips import CLIPS, INTERRUPTIONS, LANDLORD, RENTER
from evals.sim import audio


def test_ulaw_matches_g711():
    assert audio.ulaw([0]) == b"\xff"
    assert audio.ulaw([32767])[0] == 0x80 and audio.ulaw([-32768])[0] == 0x00
    assert audio.ulaw([1000, -1000]) == bytes([0xce, 0x4e])


def test_resample_keeps_length_in_time():
    assert len(audio.resample(list(range(160)), 8000, 24000)) == 480
    assert len(audio.resample([5] * 300, 24000, 8000)) == 100


def test_onset_and_end_of_voice():
    samples = [0] * 800 + [4000] * 1600 + [0] * 800
    assert audio.onset_ms(samples, 16000) == 50 and audio.voiced_ms(samples, 16000) == 150


def test_every_clip_is_committed_and_loads_in_both_formats():
    names = [*RENTER, *INTERRUPTIONS,
             *(f"{p}-{n}" for p, lines in LANDLORD.items() for n in range(len(lines)))]
    for name in names:
        assert (CLIPS / f"{name}.wav").exists(), name
    c = audio.Clip.load(CLIPS / "r1-budget.wav", audio.PCM24, RENTER["r1-budget"])
    assert len(c.audio) % 2 == 0 and 0 <= c.onset_ms < c.voice_end_ms
    u = audio.Clip.load(CLIPS / "linda-honest-0.wav", audio.PCMU8, LANDLORD["linda-honest"][0])
    assert len(u.audio) < len(c.audio)


def _clip(name="a", seconds=0.2, keyword=""):
    return audio.Clip(name, name, audio.PCMU8.silence * int(8000 * seconds), 0, int(seconds * 1000), keyword)


def test_the_ear_speaks_after_the_agent_has_played_and_paces_frames():
    clock = [0.0]
    tl = audio.Timeline(clock=lambda: clock[0])
    ear = audio.Ear(audio.PCMU8, [_clip("one")], tl)

    async def frames(n):
        out = []
        for _ in range(n):
            clock[0] += audio.TICK_S
            out.append(await ear.receive_audio())
        return out

    async def go():
        await frames(5)
        assert not any(m["kind"] == "ear:speech_start" for m in tl.marks)    # nothing heard yet
        await ear.send_audio(base64.b64encode(b"\xff" * 8000).decode())       # one second of agent audio
        await frames(int((1 + audio.SPEAK_AFTER_S) / audio.TICK_S) + 2)
        kinds = [m["kind"] for m in tl.marks]
        assert "ear:reply_starts" in kinds and "ear:speech_start" in kinds
        await frames(10)
        assert any(m["kind"] == "ear:speech_end" for m in tl.marks)
        assert len(base64.b64decode(await ear.receive_audio())) == int(8000 * audio.TICK_S)

    asyncio.run(go())


def test_first_audio_from_speech_end_and_from_speech_stopped():
    marks = [
        {"t": 1.0, "kind": "ear:speech_start", "clip": "a"},
        {"t": 3.0, "kind": "ear:speech_end", "clip": "a", "voice_end_s": 2.9},
        {"t": 3.5, "kind": "in:input_audio_buffer.speech_stopped"},
        {"t": 4.4, "kind": "in:response.output_audio.delta"},
        {"t": 4.5, "kind": "in:response.output_audio.delta"},
        {"t": 9.0, "kind": "ear:speech_start", "clip": "b"},
        {"t": 10.0, "kind": "ear:speech_end", "clip": "b"},           # never answered
    ]
    got = audio.first_audio(marks)
    assert [(x.clip, x.from_speech_end_ms, x.from_speech_stopped_ms) for x in got] == \
        [("a", 1500, 900), ("b", None, None)]
    assert audio.summary([1500, None, 900, 1100]) == {"n": 3, "missing": 1, "p50": 1100, "p90": 1500}


def test_a_barge_in_is_heard_cleared_and_answered():
    marks = [
        {"t": 5.0, "kind": "in:response.output_audio.delta", "response_id": "r1"},
        {"t": 6.0, "kind": "in:response.done", "status": "completed", "response_id": "r1"},
        {"t": 7.0, "kind": "ear:speech_start", "clip": "x-dog", "voice_start_s": 7.1, "barge": True,
         "keyword": r"\bdogs?\b"},
        {"t": 7.5, "kind": "in:input_audio_buffer.speech_started"},
        {"t": 7.5, "kind": "ear:clear", "played_ms": 2000},
        {"t": 8.9, "kind": "in:input_audio_buffer.speech_stopped"},
        {"t": 9.5, "kind": "in:response.function_call_arguments.done", "arguments": '{"pets": ["dog"]}'},
        {"t": 10.0, "kind": "in:response.output_audio_transcript.done", "transcript": "Got it, a dog too."},
    ]
    [b] = audio.barge_ins(marks)
    assert (b.detected_ms, b.cleared_ms, b.played_ms, b.interrupted_status) == (400, 400, 2000, "completed")
    assert b.answered and b.ok and b.server_end_ms is None
    marks[-1]["transcript"], marks[-2]["arguments"] = "Sure, which area?", "{}"
    del marks[3]                                                        # never heard
    [b] = audio.barge_ins(marks)
    assert not b.ok and b.why == ["not heard in time", "next reply didn't pick it up"]


def test_greeting_time():
    marks = [{"t": 0.2, "kind": "out:response.create"}, {"t": 0.65, "kind": "in:response.output_audio.delta"}]
    assert audio.greeting_audio_ms(marks) == 450


def test_a_truncate_and_higgs_ack_are_read():
    marks = [
        {"t": 7.0, "kind": "ear:speech_start", "clip": "x-dog", "voice_start_s": 7.0, "barge": True,
         "keyword": "dog"},
        {"t": 7.3, "kind": "in:input_audio_buffer.speech_started"},
        {"t": 7.3, "kind": "out:conversation.item.truncate", "item_id": "i1", "audio_end_ms": 1500},
        {"t": 7.3, "kind": "ear:clear", "played_ms": 1500},
        {"t": 7.4, "kind": "in:conversation.item.truncated", "item_id": "i1", "audio_end_ms": 1500},
        {"t": 8.5, "kind": "in:input_audio_buffer.speech_stopped"},
        {"t": 9.0, "kind": "in:response.output_audio_transcript.done", "transcript": "A dog is fine."},
    ]
    [b] = audio.barge_ins(marks)
    assert (b.server_end_ms, b.acked_end_ms, b.truncate_error) == (1500, 1500, "") and b.ok

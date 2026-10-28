"""The audio sim (E-04): time to first audio, barge-in recovery and call length on real Higgs audio.

    uv run python -m evals.run_audio                 # all runs, then the report; needs BOSON_API_KEY
    uv run python -m evals.run_audio --no-report     # print only

Every run is a real voice path with an `Ear` (evals/sim/audio.py) at the other end, saying Higgs-synthesized
clips (evals/audio_clips.py) in real time:
- **Concierge, first audio**: two renter briefs through `voice/bridge.converse` on 24 kHz PCM (the browser).
- **Concierge, barge-in**: the same briefs, talking over each long reply `BARGE_AFTER_S` in. One more brief
  runs with the harness sending `conversation.item.truncate` (what had played) on `speech_started`, which the
  bridge doesn't do yet, to see whether Higgs takes it.
- **Caller**: three tune personas' scripted lines through `voice/phone_caller.PhoneCaller` on μ-law (the
  phone), each a whole call from pick-up to goodbye: first audio per turn and the call's length.

At most two Higgs sessions run at once (the shared key answers 429 above about five). Without
BOSON_API_KEY it skips and leaves the report as it was. Timelines go to evals/runs/<stamp>-audio/.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

from evals import report
from evals.audio_clips import CLIPS, INTERRUPTIONS, LANDLORD, RENTER
from evals.run_concierge import CATALOGUE_PATH, RUNS_DIR, _sha
from evals.sim import audio
from evals.sim.calls import load_calls
from landline.config import ROOT, Settings
from landline.contracts import CallsRequested, PreferencesUpdated, SessionEnded, SessionStarted
from landline.contracts.tools import PreferenceDelta
from landline.db.catalogue import Catalogue, _from_fixture
from landline.orchestrator import SessionRuntime
from landline.voice.bridge import converse
from landline.voice.calls import RenterCalls
from landline.voice.dispatch import Dial, Dispatcher
from landline.voice.gemini_live import session_connect
from landline.voice.phone_caller import PhoneCaller

SUMMARY = report.REPORTS / "audio.json"
BRIEFS = {"r1": [k for k in RENTER if k.startswith("r1-")], "r2": [k for k in RENTER if k.startswith("r2-")]}
CALLER_PERSONAS = list(LANDLORD)
CONCURRENCY = 2


def clip(name: str, fmt: audio.Format, text: str, keyword: str = "") -> audio.Clip:
    return audio.Clip.load(CLIPS / f"{name}.wav", fmt, text, keyword)


def tapped(timeline: audio.Timeline, connect_voice, played=None):
    async def connect(key: str, cfg: dict[str, Any]):
        return audio.Tap(await connect_voice(key, cfg), timeline, played)
    return connect


async def concierge(
    brief: str, settings: Settings, catalogue: Catalogue, *, barges: int = 0, truncate: bool = False
) -> dict[str, Any]:
    fmt, timeline = audio.PCM24, audio.Timeline()
    lines = [clip(k, fmt, RENTER[k]) for k in BRIEFS[brief]]
    cuts = [clip(k, fmt, text, kw) for k, (text, kw) in INTERRUPTIONS.items()]
    ear = audio.Ear(fmt, lines, timeline, barges=barges, interruptions=cuts)
    runtime = SessionRuntime(catalogue)
    calls = RenterCalls(cap=4)
    call = calls.open(caller=None)
    calls.claim(call.call_id)
    await runtime.start(SessionStarted(session_id=call.session_id, at=datetime.now(UTC),
                                       idem_key=f"{call.session_id}:started", channel="browser"))
    started = time.monotonic()
    connect = tapped(timeline, session_connect(settings), ear.played_ms if truncate else None)
    reason = await converse(ear, call, calls=calls, runtime=runtime, connect_higgs=connect, settings=settings)
    await runtime.close()
    name = f"concierge-{brief}{'-barge' if barges else ''}{'-truncate' if truncate else ''}"
    return {"run": name, "path": "browser (24 kHz PCM)",
            "end": reason, "seconds": round(time.monotonic() - started, 1), "marks": timeline.marks}


async def caller(persona: str, settings: Settings, catalogue: Catalogue) -> dict[str, Any]:
    case = next(c for c in load_calls("tune") if c.id == persona)
    fmt, timeline = audio.PCMU8, audio.Timeline()
    lines = [clip(f"{persona}-{n}", fmt, text) for n, text in enumerate(LANDLORD[persona])]
    ear = audio.Ear(fmt, lines, timeline, first=True)
    runtime = SessionRuntime(catalogue)
    # no runner: freezes the plan, queues the call
    Dispatcher(runtime, replace(settings, call_concurrency=1))
    sid, now, p = f"s_audio_{persona}", datetime.now(UTC), case.plan
    await runtime.start(SessionStarted(session_id=sid, at=now, idem_key=f"{sid}:started", channel="judge"))
    share = p.shareable
    await runtime.emit(PreferencesUpdated(
        session_id=sid, at=now, idem_key=f"{sid}:prefs", delta=PreferenceDelta(
            move_in=share.move_in, occupants=share.occupants, pets=share.pets or None)))
    await runtime.emit(CallsRequested(session_id=sid, at=now, idem_key=f"{sid}:plan",
                                      listing_ids=[case.listing_id],
                                      extra_questions=p.questions, availability=p.availability,
                                      may_book=p.may_book))
    await runtime.emit(SessionEnded(session_id=sid, at=now, idem_key=f"{sid}:ended", reason="hangup"))
    for _ in range(50):
        if runtime.state(sid).plan is not None:
            break
        await asyncio.sleep(0.05)
    state = runtime.state(sid)
    dial = Dial(call_id=next(iter(state.calls)), session_id=sid, listing_id=case.listing_id, to=None,
                plan=state.plan, deadline=datetime.now(UTC) + timedelta(seconds=settings.listing_call_max_s))
    started = time.monotonic()
    result = await PhoneCaller(runtime, tapped(timeline, session_connect(settings)),
                               settings.voice_api_key() or "", voice=settings.live_voice())(dial, ear)
    seconds = round(time.monotonic() - started, 1)
    await runtime.close()
    out = result.outcome
    return {"run": f"caller-{persona}", "path": "phone (μ-law 8 kHz)", "end": result.end_reason,
            "seconds": seconds, "facts": len(out.facts) if out else 0,
            "booked": bool(out and out.viewing), "red_flags": len(out.red_flags) if out else 0,
            "marks": timeline.marks}


def measure(runs: list[dict[str, Any]]) -> dict[str, Any]:
    """The report's numbers from the runs' timelines."""
    first: dict[str, list[audio.FirstAudio]] = {"concierge": [], "caller": []}
    greeting, barges, cut, calls = [], [], [], []
    for r in runs:
        marks = r["marks"]
        if r["run"].endswith("-barge"):
            barges += audio.barge_ins(marks)
            continue
        if r["run"].endswith("-barge-truncate"):
            cut += audio.barge_ins(marks)
            continue
        agent = "caller" if r["run"].startswith("caller") else "concierge"
        first[agent] += audio.first_audio(marks)
        if agent == "concierge":
            greeting.append(audio.greeting_audio_ms(marks))
        else:
            turns = sum(m["kind"] == "in:response.output_audio_transcript.done" for m in marks)
            calls.append({"persona": r["run"].removeprefix("caller-"), "seconds": r["seconds"],
                          "end": r["end"], "caller_turns": turns, "facts": r.get("facts"),
                          "booked": r.get("booked"), "red_flags": r.get("red_flags")})
    out: dict[str, Any] = {"greeting_ms": audio.summary(greeting)}
    for agent, items in first.items():
        out[agent] = {"from_speech_end": audio.summary([x.from_speech_end_ms for x in items]),
                      "from_speech_stopped": audio.summary([x.from_speech_stopped_ms for x in items]),
                      "turns": len(items)}
    ok = [b for b in barges if b.ok]
    played = [b for b in barges if b.played_ms is not None]
    out["barge_in"] = {
        "tries": len(barges), "ok": len(ok),
        "detected": audio.summary([b.detected_ms for b in barges]),
        "cleared": audio.summary([b.cleared_ms for b in barges]),
        "answered": sum(b.answered for b in barges),
        "truncated_by_us": sum(b.server_end_ms is not None for b in barges),
        "cancelled": sum(b.interrupted_status == "cancelled" for b in barges),
        "played_ms": audio.summary([b.played_ms for b in played]),
        "failures": [{"clip": b.clip, "why": b.why, "answer": b.answer[:120]} for b in barges if not b.ok],
    }
    sent = [b for b in cut if b.server_end_ms is not None]
    out["barge_in_with_truncate"] = {
        "tries": len(cut), "ok": sum(b.ok for b in cut), "sent": len(sent),
        "acked": sum(b.acked_end_ms is not None for b in sent),
        "matched": sum(b.acked_end_ms is not None and b.acked_end_ms == b.server_end_ms for b in sent),
        "errors": sorted({b.truncate_error for b in sent if b.truncate_error}),
        "answered": sum(b.answered for b in cut),
        "detected": audio.summary([b.detected_ms for b in cut]),
    }
    out["calls"] = calls
    return out


async def run(keep_report: bool) -> int:
    settings = Settings.load()
    if not settings.voice_api_key():
        which = "GEMINI_API_KEY" if settings.voice_provider == "gemini" else "BOSON_API_KEY"
        print(f"{which} is not set: skipping the audio sim.")
        return 0
    settings = replace(settings, database_url=None, record_audio=False)
    catalogue = Catalogue(_from_fixture(CATALOGUE_PATH), "eval")
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out_dir = RUNS_DIR / f"{stamp}-audio"
    out_dir.mkdir(parents=True, exist_ok=True)
    gate = asyncio.Semaphore(CONCURRENCY)
    jobs = [lambda b=b: concierge(b, settings, catalogue) for b in BRIEFS]
    jobs += [lambda b=b: concierge(b, settings, catalogue, barges=6) for b in BRIEFS]
    jobs += [lambda: concierge("r1", settings, catalogue, barges=6, truncate=True)]
    jobs += [lambda p=p: caller(p, settings, catalogue) for p in CALLER_PERSONAS]

    async def gated(job) -> dict[str, Any] | None:
        async with gate:
            try:
                r = await job()
            except Exception as exc:   # one broken run is reported, not fatal
                print(f"  run failed: {type(exc).__name__}: {exc}", flush=True)
                return None
            (out_dir / f"{r['run']}.json").write_text(json.dumps(r, ensure_ascii=False, indent=1),
                                                     encoding="utf-8")
            print(f"  {r['run']:24} {r['end']:12} {r['seconds']:6.1f}s", flush=True)
            return r

    runs = [r for r in await asyncio.gather(*(gated(j) for j in jobs)) if r]
    numbers = measure(runs)
    numbers.update({"at": datetime.now(UTC).isoformat(timespec="seconds"), "sha": _sha(),
                    "traces": out_dir.relative_to(ROOT).as_posix(), "runs": len(runs),
                    "clips": "synthesized with Higgs TTS (higgs-tts-v3, voice default)"})
    print(json.dumps({k: v for k, v in numbers.items() if k not in ("calls",)}, indent=1))
    for c in numbers["calls"]:
        print(f"  call {c['persona']:14} {c['seconds']:5.1f}s  "
              f"{c['caller_turns']} Caller turns  end {c['end']}")
    if not keep_report:
        prev = json.loads(SUMMARY.read_text(encoding="utf-8")).get("current") if SUMMARY.exists() else None
        SUMMARY.write_text(
            json.dumps({"current": numbers, "previous": prev}, ensure_ascii=False, indent=1) + "\n",
            encoding="utf-8")
        report.write()
    return 0


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--no-report", action="store_true")
    return asyncio.run(run(parser.parse_args().no_report))


if __name__ == "__main__":
    raise SystemExit(main())

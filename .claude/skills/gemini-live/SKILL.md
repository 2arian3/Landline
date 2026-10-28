---
name: gemini-live
description: Gemini Live as the voice model behind Landline's Higgs-shaped session. Covers setup, audio rates for Twilio and the browser, tool replies, the interrupt rule, and what the bridge must not see. Use when VOICE_PROVIDER=gemini, or when changing server/landline/voice/gemini_live.py or the codec.
---

# Gemini Live

Checked against [the Live API](https://ai.google.dev/gemini-api/docs/live-api) on 27 Oct 2026. The bridge, Caller, sim and line still speak the Higgs dialect in `.claude/skills/higgs-realtime/SKILL.md`. `voice/gemini_live.py` translates. Do not leak Live events upward.

## Connect

`VOICE_PROVIDER=gemini` makes `app.state.connect_higgs` open Gemini. `GEMINI_API_KEY`, `GEMINI_LIVE_MODEL` (default `gemini-3.8-live`), `GEMINI_LIVE_VOICE` (default `Aoede`, a prebuilt voice). Higgs's `default` is not a Gemini voice; `Settings.live_voice()` picks the right one.

`connect(api_key, session)` takes the same `session_config(...)` dict as Higgs and returns once setup has completed. Setup cannot be changed on the open socket.

Avatar clips stay on Boson (`BOSON_API_KEY`, `voice/avatar_clip.py`). The phone and judge paths are ready when `Settings.voice_api_key()` is set. `/health`'s `higgs` field is that session's health either way.

## Audio

| Path | Bridge sees | Gemini sees |
|---|---|---|
| Phone | G.711 μ-law 8 kHz, base64, both ways | PCM16 16 kHz in, PCM16 24 kHz out |
| Browser | PCM16 24 kHz, base64 on the session, raw bytes on the socket | PCM16 24 kHz in (MIME `audio/pcm;rate=24000`; Gemini resamples), PCM16 24 kHz out |

`voice/audio_codec.py` converts. The browser wire format in `web/lib/judgeAudio.ts` does not change.

## What the bridge sends

- `input_audio_buffer.append` → `send_realtime_input` audio.
- `conversation.item.create` user text, then `response.create` → one `send_client_content` with `turn_complete: true`. That is the greeting, a map tap, and the wrap-up note.
- `function_call_output`, then the one `response.create` `run_tool` sends → one `send_tool_response`. Do not also send client content: `turn_complete: true` interrupts the reply the tool response just started. A second `response.create` for that response is a no-op (#175).
- Tool output with no `response.create` (#219, the reply already spoke) is still answered, or the session stalls. The follow-up audio and transcript are dropped until the next user turn.

## What the bridge reads

Live audio, transcripts, tool calls and `turnComplete` become `response.created`, `response.output_audio.delta`, `response.output_audio_transcript.done`, `response.function_call_arguments.done`, and `response.done`. `response.done`'s `response.output` lists each `function_call`, which `response_settles` reads. `interrupted` becomes `input_audio_buffer.speech_started` (the bridge already clears the phone on that). `speech_stopped` is synthesized from bytes heard; Gemini does not send Higgs's `audio_start_ms`.

`modality="text"` (the eval simulator) sends `responseModalities: ["TEXT"]` and emits `response.output_text.delta` / `.done`. If the model rejects TEXT, evals stay on Higgs. `make eval` follows `VOICE_PROVIDER`.

## Spike

`uv run python scripts/spike_gemini_live.py` prints time to first audio, one tool round trip, whether a clip barges in, whether TEXT mode answers, and how many sessions open before rejection. It does not dial. Re-measure `HIGGS_MAX_SESSIONS` from that concurrency number before setting `VOICE_PROVIDER=gemini`. The migration checkout had no `GEMINI_API_KEY`, so those numbers are not filled in yet.

## Playback

Higgs delivers a reply in one burst. Gemini streams closer to real time, so `Playback.remaining()` is shorter. The hang-up grace in the bridge is unchanged until a live goodbye shows it cutting someone off.

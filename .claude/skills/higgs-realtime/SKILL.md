---
name: higgs-realtime
description: Boson Higgs Realtime protocol for Landline's voice agents. Covers connecting, session.update fields, audio formats for Twilio (pcmu) and the browser (pcm 24 kHz), event names, tool calls, injecting text between turns, barge-in, and limits and close codes. Use when touching server/landline/voice/, the Concierge or Caller agents, or anything that sends or receives Higgs events.
---

# Higgs Realtime

Verified against docs.boson.ai on 21 Oct 2026. When something fails, re-check `https://docs.boson.ai/llms.txt` before guessing. Higgs is compatible with the **GA** OpenAI Realtime protocol, **not** the beta one that Realest used.

## Connect

- `wss://api.boson.ai/v1/realtime?model=higgs-realtime`, header `Authorization: Bearer $BOSON_API_KEY`. No `OpenAI-Beta` header.
- The first client message must be `session.update`. `session.created` arrives only after it; `conversation.created` is never sent.
- In the browser, mint an ephemeral key (`POST /v1/realtime/client_secrets`, prefix `bai-eph-`), or proxy audio through our server (preferred: tools stay in-process).

## session.update

```json
{"type": "session.update", "session": {
  "model": "higgs-realtime",
  "instructions": "...",
  "output_modalities": ["audio"],
  "audio": {
    "input":  {"format": {"type": "audio/pcmu"}, "transcription": {"model": "higgs-stt-3.1"},
               "turn_detection": {"type": "server_vad"}},
    "output": {"format": {"type": "audio/pcmu"}, "voice": "default"}
  },
  "tools": [...], "tool_choice": "auto", "temperature": 0.3}}
```

- **Twilio** uses `audio/pcmu` (G.711 μ-law 8 kHz) both ways. Pass the frames through untouched; no resampling.
- **Browser** uses `{"type": "audio/pcm", "rate": 24000}` (PCM16 mono). `audio/pcm` also accepts 8000, 16000 and 48000.
- `output_modalities` must be exactly `["audio"]` or `["text"]`.
- Voices: `"default"`, a Boson preset, or `voice_<id>` (custom voice from `POST /v1/audio/voices`). OpenAI names like `alloy` fail the update.
- The update fails if you send `include`, `prompt`, `tracing` or `idle_timeout_ms`. `truncation` accepts only `"auto"` or `"disabled"`.
- `server_vad` threshold defaults to 0.55; `min_speech_duration` (default 0.125) is an extension. `semantic_vad` also exists.

## Events you handle

| Event | Meaning |
|---|---|
| `response.output_audio.delta` | Audio out → forward to Twilio `media` (with `streamSid`) or the browser |
| `response.output_audio_transcript.done` | What the agent said (turn text, speaker `agent`) |
| `conversation.item.input_audio_transcription.completed` | What the human said (no `.delta` events exist) |
| `input_audio_buffer.speech_started` / `speech_stopped` | Barge-in and turn state |
| `response.created` / `response.done` | The agent is / stopped responding; `status` is `completed` or `cancelled` |
| `response.function_call_arguments.done` | A tool call: run it, send `function_call_output`, then `response.create` |
| `conversation.context.summarized`, `session.idle_timeout`, `session.max_duration_reached`, `response.output_audio_transcript.length` | Higgs extensions: log them, tolerate unknown types |

Never emitted: `conversation.item.done`, `rate_limits.updated`. Don't gate logic on them.

## Tools

1. On `response.function_call_arguments.done`, run the tool in-process with the session and listing taken from the **Call**, never from the arguments.
2. Send `conversation.item.create` with `{"type": "function_call_output", "call_id": ..., "output": "<≤25 spoken words>"}`.
3. Send `response.create`.

## Injecting text between turns

`conversation.item.create` takes **text only**, and the `system` role is rejected. Inject as `{"type": "message", "role": "user", "content": [{"type": "input_text", "text": "[call result] ..."}]}`. Send it only when no response is in flight and the human isn't speaking, then `response.create`. Wrap everything quoted: `[landlord said] "…"`. The `Line` class in `voice/line.py` owns this logic.

## Barge-in

The server cancels and truncates on its own. On `input_audio_buffer.speech_started`, still send Twilio `{"event": "clear", "streamSid": ...}` so buffered audio stops. `conversation.item.truncate` and `response.cancel` are supported but optional.

## Limits and close codes

- `input_audio_buffer.append`: at most 1 MiB of base64 per event.
- Close `1013`: concurrency limit, so retry with backoff, behind our semaphore. `4429`: out of credit. `3000`: bad or expired key. `1000`: normal, including idle timeout (5 min without user speech) and max duration.
- Pricing: about $0.0023/min of speech in and $0.014/min out. Credits are not the constraint; concurrency is.

## Spike results (fill in from A-01)

Run the playground: `uv run python scripts/spike_higgs.py` (or `make spike`). Needs `BOSON_API_KEY`. Use `--write-skill` to fill this table.

| Measure | Value |
|---|---|
| Time to first audio (pcmu) | _tbd_ |
| Max concurrent sessions before 1013 | _tbd_ |
| `conversation.item.truncate` accepted | _tbd_ |
| Farsi + English transcription on 8 kHz | _tbd_ |

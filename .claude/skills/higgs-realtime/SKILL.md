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

1. On `response.function_call_arguments.done`, run the tool in-process with the session and listing taken from the **Call**, never from the arguments. In the Concierge, that's `voice/bridge.py` `run_tool`, which the eval simulator also uses.
2. Send `conversation.item.create` with `{"type": "function_call_output", "call_id": ..., "output": "<≤25 spoken words>"}`.
3. Send `response.create`.

What the model really sends (eval, 24 Oct; `docs/EVAL_FINDINGS.md`):
- **Numbers where the schema says string.** `"listing_refs": [1, 2]` against `list[str]` failed validation, so "call the top two" saved nothing. `tools.handle` turns numeric refs into words. Expect the same for any field a person says as a number.
- **Arguments for another tool.** `availability` and `may_book` went into `update_preferences`. Pydantic drops unknown keys without a word, and a "success" reply makes the agent believe it saved them. `tools.handle` answers "Not saved: …; call call_listings with them." Never ignore extra keys quietly.
- **Answers one at a time.** After a reply that says what's missing, the agent sends only the new field, so a second `call_listings` updates the plan rather than replacing it.

## end_session: say goodbye first

Nothing is said after `end_session`: `run_tool` sends no `response.create` once `ctx.hang_up` is set, and the bridge hangs up at that response's `response.done`. So the goodbye has to come **before** the tool call, in the same response. When the model calls it first anyway, `run_tool` swaps the answer for `tools.SAY_GOODBYE` and sends one plain `response.create`. `ends_call` then waits for the response after it (`ctx.bye_response` holds the silent one's id). Whether the model spoke is read from **deltas** (`response.output_audio.delta`, or `response.output_text.delta` in text): in a response with text and then a tool call, `function_call_arguments.done` arrives before that text's `.done`.

## Don't put instructions on response.create mid-call

`response.create` with `"response": {"instructions": ...}` **replaces** the session's instructions for that response, and the model answers as if the conversation weren't there. Every goodbye asked for that way came out as "Hello! How can I help you today?" (21 of 21). The returning-caller greeting sends `WELCOME_BACK` that way today, and in a probe it ignored the remembered facts and made a memory up in 3 of 3 runs. That's open, see `docs/EVAL_FINDINGS.md`. Use response instructions only for a self-contained line with nothing to remember, like the first `GREETING`. To steer one response mid-call, put the request in a tool's output or in injected user text, and keep the session instructions in force.

## Injecting text between turns

`conversation.item.create` takes **text only**, and the `system` role is rejected. Inject as `{"type": "message", "role": "user", "content": [{"type": "input_text", "text": "[renter is looking at] 412 Markham"}]}`. Send it only when no response is in flight and the human isn't speaking, then `response.create`. Wrap everything as quoted data. The `Line` class in `voice/line.py` owns this logic (planned in A-02; not written yet). Only the Concierge gets injected text (focus taps and the reconnect summary); a Caller gets everything it needs in its instructions at dial time.

## Text mode (the eval simulator)

Verified 24 Oct. `output_modalities: ["text"]` is accepted with or without the `audio` block (`session_config(..., modality="text")`). Typed input goes in as user `input_text`. Output is `response.output_text.delta` and `.done` (the full text in `text`), and tool events are unchanged. An agent turn ends at the first `response.done` whose `response.output` has no `function_call`. `function_call_arguments.done` carries `response_id`, and `response.done` carries `response.id`. A typed turn with a tool call takes about 0.9 s.

## Agent problems seen in the eval

The Concierge, as of `6f99149`. Read these before changing its prompt; the details and numbers are in `docs/EVAL_FINDINGS.md`.
- Treats "the first one looks great" as a focus, not a like.
- Repeats an address that two cards share ("30 Carabob Court"), so the ref never resolves.
- After a switch to Farsi, may narrate in English ("I've updated your search…") and call no tool. Three prompt changes didn't fix it; a Spanish example made it worse.
- Runs past two sentences in about a quarter of turns, mostly by reading the whole board line back.
- Says "I've noted…" without a tool call, less often now. The eval only catches this in English.
- Once made up viewing times ("today 10 am to 6 pm") before the renter gave any.

## Barge-in

The server cancels and truncates on its own. On `input_audio_buffer.speech_started`, still send Twilio `{"event": "clear", "streamSid": ...}` so buffered audio stops. `conversation.item.truncate` and `response.cancel` are supported but optional.

## Limits and close codes

- `input_audio_buffer.append`: at most 1 MiB of base64 per event.
- Close `1013`: concurrency limit, so retry with backoff, behind our semaphore. `4429`: out of credit. `3000`: bad or expired key. `1000`: normal, including idle timeout (5 min without user speech) and max duration.
- Pricing: about $0.0023/min of speech in and $0.014/min out. Credits are not the constraint; concurrency is.

## Spike results (A-01)

Measured with `scripts/spike_higgs.py` on `agents/A-01-higgs-spike`, Fri 23 Oct.

| Measure | Value |
|---|---|
| Time to first audio (pcmu) | 487 ms from connect to the first audio delta; 282 ms from `response.create` |
| Max concurrent sessions before 1013 | none: 5 of 5 opened, and a later run opened 8, with no close 1013 |
| `conversation.item.truncate` accepted | yes, after `response.done` (`audio_end_ms` 2620) |
| Farsi + English transcription on 8 kHz | no: a synthesized mixed sentence came back as "Salam. I want a two-bedroom in Toronto. Sagdaram." The browser path's 24 kHz audio may do better; not measured yet |

In the bridge, the first audio of the greeting reaches the browser about 3 s after its socket opens, because that includes connecting to Higgs.

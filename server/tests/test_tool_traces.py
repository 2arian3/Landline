"""Each tool call is a Logfire span (voice/bridge.py run_tool), and Logfire is set up only with LOGFIRE_TOKEN
(app.py). Spans go to an in-memory exporter; nothing is sent."""
import logging
from types import SimpleNamespace

import logfire
import pytest
from logfire.testing import TestExporter
from opentelemetry.sdk.trace.export import SimpleSpanProcessor

from landline import app as app_module
from landline.voice.bridge import run_tool

BROKEN = "Sorry, that didn't work."


class FakeHiggs:
    def __init__(self):
        self.sent = []

    async def send(self, event):
        self.sent.append(event)


@pytest.fixture
def spans():
    exporter = TestExporter()
    logfire.configure(send_to_logfire=False, console=False, inspect_arguments=False,
                      scrubbing=logfire.ScrubbingOptions(callback=app_module._keep_session),
                      additional_span_processors=[SimpleSpanProcessor(exporter)])
    yield exporter
    logfire.configure(send_to_logfire=False, console=False, inspect_arguments=False)   # stop collecting


def agent(handle):
    return SimpleNamespace(handle=handle, BROKEN=BROKEN, SAY_GOODBYE="Say goodbye first.")


def ctx():
    return SimpleNamespace(session_id="s_trace00001", call_id="c_trace00001", hang_up=False,
                           bye_response=None)


def tool_span(exporter):
    [span] = [s for s in exporter.exported_spans_as_dict() if s["name"] == "tool {name}"]
    return span


async def test_a_tool_call_is_a_span_with_its_arguments_and_answer(spans):
    async def handle(name, arguments, tool_call_id, ctx):
        return "Your session is saved."

    higgs = FakeHiggs()
    event = {"name": "end_session", "arguments": '{"reason": "done"}', "call_id": "t_1"}
    await run_tool(higgs, event, ctx(), agent=agent(handle))

    a = tool_span(spans)["attributes"]
    assert a["logfire.msg"] == "tool end_session"
    assert (a["call_id"], a["session_id"], a["tool_call_id"]) == ("c_trace00001", "s_trace00001", "t_1")
    assert a["arguments"] == '{"reason": "done"}'
    assert a["output"] == "Your session is saved."   # "session" isn't scrubbed
    assert higgs.sent == [{"type": "conversation.item.create", "item": {
        "type": "function_call_output", "call_id": "t_1", "output": "Your session is saved."}},
        {"type": "response.create"}]


async def test_secrets_in_arguments_are_still_scrubbed(spans):
    async def handle(*_):
        return "ok"

    await run_tool(FakeHiggs(), {"name": "search", "arguments": '{"api_key": "sk-123"}', "call_id": "t_2"},
                   ctx(), agent=agent(handle))
    assert "sk-123" not in str(tool_span(spans)["attributes"]["arguments"])


async def test_a_failed_tool_is_an_error_span_and_still_answers(spans):
    async def handle(*_):
        raise RuntimeError("database down")

    higgs = FakeHiggs()
    await run_tool(higgs, {"name": "search", "arguments": "{}", "call_id": "t_3"}, ctx(), agent=agent(handle))

    span = tool_span(spans)
    assert span["attributes"]["logfire.level_num"] >= 17   # error
    [exc] = [e for e in span["events"] if e["name"] == "exception"]
    assert exc["attributes"]["exception.type"] == "RuntimeError"
    assert higgs.sent[0]["item"]["output"] == BROKEN
    assert higgs.sent[1] == {"type": "response.create"}


def test_nothing_is_set_up_without_a_token(monkeypatch):
    calls = []
    monkeypatch.delenv("LOGFIRE_TOKEN", raising=False)
    monkeypatch.setattr(logfire, "configure", lambda **kw: calls.append(kw))
    app_module._trace_to_logfire()
    assert calls == []


def test_a_token_sets_up_logfire_and_forwards_log_lines(monkeypatch):
    calls = []
    logger = logging.getLogger("landline")
    before = list(logger.handlers)
    monkeypatch.setenv("LOGFIRE_TOKEN", "test-token")
    monkeypatch.setattr(logfire, "configure", lambda **kw: calls.append(kw))
    try:
        app_module._trace_to_logfire()
        app_module._trace_to_logfire()   # a second app in the same process adds no second handler
        assert [c["token"] for c in calls] == ["test-token", "test-token"]
        assert calls[0]["console"] is False
        added = [h for h in logger.handlers if h not in before]
        assert len(added) == 1 and isinstance(added[0], logfire.LogfireLoggingHandler)
    finally:
        logger.handlers = before

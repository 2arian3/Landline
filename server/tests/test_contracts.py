import json
import re
from datetime import UTC, datetime
from typing import get_args

import pytest
from pydantic import ValidationError

from landline.config import ROOT
from landline.contracts import (
    CALLER_TOOLS,
    CONCIERGE_TOOLS,
    EVENT,
    Anchor,
    AnchorResolved,
    Area,
    AreaFocused,
    CallEnded,
    CallOutcome,
    CallPlan,
    CallsCompleted,
    CallsRequested,
    CallsScheduled,
    CallStateChanged,
    CallView,
    Card,
    CardStatus,
    CataloguePoint,
    CommuteResolved,
    Event,
    Fact,
    FactRecorded,
    Fee,
    FeedbackGiven,
    ListingFocused,
    OutcomeReady,
    PreferenceDelta,
    Preferences,
    PreferencesUpdated,
    QuestionNoted,
    SessionEnded,
    SessionStarted,
    SessionView,
    ViewingBooked,
    ViewingDecided,
    as_e164,
    caller_tools,
    concierge_tools,
    monthly_cost,
)
from landline.contracts.snapshot import SNAPSHOT, render
from landline.contracts.tools import CallListings

AT = datetime(2026, 10, 24, 18, 30, tzinfo=UTC)
SID, CID, LID = "s_test000001", "c_test000001", "livrent:100219"
TOOL_SPECS = concierge_tools() + caller_tools()


def test_as_e164():
    assert as_e164("+14165550101") == "+14165550101"
    assert as_e164("4165550101") == "+14165550101"
    assert as_e164("(416) 555-0101") == "+14165550101"
    assert as_e164("+4165550101") == "+14165550101"     # NANP missing the 1
    assert as_e164("call me maybe") == ""
    assert as_e164(None) == ""


def test_monthly_cost_counts_only_mandatory_monthly_fees():
    fees = [
        Fee(name="parking", amount=180, period="monthly", mandatory=False),
        Fee(name="utilities", amount=120, period="monthly", mandatory=True),
        Fee(name="deposit", amount=1200, period="one_time", mandatory=True),
    ]
    assert monthly_cost(2400, fees) == 2520


def test_contract_snapshot_is_current():
    assert SNAPSHOT.read_text() == render(), "contracts changed: run `make contracts-snapshot` and commit it"


def test_session_fixture_is_a_valid_session_view():
    view = SessionView.model_validate_json((ROOT / "web" / "lib" / "fixtures" / "session.json").read_text())
    assert view.phase == "calling"
    assert {c.status for c in view.board} == set(get_args(CardStatus))
    assert view.plan == [c.listing_id for c in view.calls]
    assert [c.rank for c in view.board] == list(range(1, len(view.board) + 1))


def _event(cls, **payload):
    return cls(session_id=SID, at=AT, idem_key=f"{cls.__name__}:1", **payload)


PLAN = CallPlan(session_id=SID, listing_ids=[LID], created_at=AT)
SAMPLE_EVENTS = [
    _event(SessionStarted, channel="phone", caller_phone="+14165550101"),
    _event(PreferencesUpdated, delta=PreferenceDelta(max_monthly=3000, anchors=[{"label": "King and Bay"}])),
    _event(FeedbackGiven, listing_id=LID, reaction="dislike", reason="too dark", attribute="light"),
    _event(ListingFocused, listing_id=LID, origin="ui"),
    _event(AreaFocused, label="The Annex", lat=43.67, lng=-79.40, zoom=14),
    _event(AnchorResolved, label="King and Bay", lat=43.6487, lng=-79.3817),
    _event(CallsRequested, listing_ids=[LID], may_book=True,
           availability=[{"day": "saturday", "from": "13:00", "to": "17:00"}]),
    _event(SessionEnded, reason="hangup"),
    _event(CallsScheduled, plan=PLAN, call_ids=[CID]),
    _event(CallStateChanged, call_id=CID, state="queued"),
    _event(FactRecorded, call_id=CID, field="parking_cost", value="$180 a month",
           quote="Parking is one eighty a month.", turn_idx=4, confirmed=True),
    _event(QuestionNoted, call_id=CID, question="Does your client smoke?"),
    _event(ViewingBooked, call_id=CID, listing_id=LID, starts_at=AT, slot_text="Saturday at two"),
    _event(CallEnded, call_id=CID, reason="done"),
    _event(OutcomeReady, outcome=CallOutcome(call_id=CID, listing_id=LID, end_reason="done")),
    _event(CallsCompleted, call_ids=[CID]),
    _event(CommuteResolved, label="King and Bay", lat=43.6487, lng=-79.3817),
    _event(ViewingDecided, call_id=CID, listing_id=LID, starts_at=AT, status="confirmed"),
]


def test_every_event_type_has_a_sample():
    union = get_args(get_args(Event)[0])
    assert {type(e) for e in SAMPLE_EVENTS} == set(union)


@pytest.mark.parametrize("event", SAMPLE_EVENTS, ids=lambda e: e.type)
def test_event_round_trips_through_the_union(event):
    parsed = EVENT.validate_json(event.model_dump_json(by_alias=True))
    assert type(parsed) is type(event)
    assert parsed == event


def test_availability_keeps_the_from_key():
    event = SAMPLE_EVENTS[6]
    assert event.model_dump(mode="json", by_alias=True)["availability"][0]["from"] == "13:00"


def test_tools_match_the_spec():
    assert list(CONCIERGE_TOOLS) == [
        "update_preferences", "give_feedback", "focus_listing", "focus_area", "ask_maps", "call_listings",
        "listing_status", "remember_me", "forget_me", "end_session",
    ]
    assert list(CALLER_TOOLS) == ["record_fact", "record_available_from", "note_question", "book_viewing",
                                  "end_call"]


@pytest.mark.parametrize("spec", TOOL_SPECS, ids=lambda s: s["name"])
def test_tool_spec_is_a_plain_json_schema_object(spec):
    assert spec["type"] == "function" and spec["description"]
    params = spec["parameters"]
    assert params["type"] == "object"
    assert set(params.get("required", [])) <= set(params["properties"])
    text = json.dumps(params)
    for leftover in ('"$ref"', '"$defs"', '"title"', '{"type": "null"}'):
        assert leftover not in text


def _property_names(node):
    if isinstance(node, dict):
        for name, child in node.get("properties", {}).items():
            yield name
            yield from _property_names(child)
        for child in node.values():
            if isinstance(child, dict | list):
                yield from _property_names(child)
    elif isinstance(node, list):
        for child in node:
            yield from _property_names(child)


@pytest.mark.parametrize("spec", TOOL_SPECS, ids=lambda s: s["name"])
def test_tool_arguments_carry_no_ids_phones_times_or_totals(spec):
    # AGENTS.md rule 2: code owns identity, time and money.
    forbidden = re.compile(r"(_ids?|phone|_at|monthly_cost|total|price)$|^(at|session|call)$")
    assert [n for n in _property_names(spec["parameters"]) if forbidden.search(n)] == []


def test_call_listings_and_the_plan_take_at_most_five():
    with pytest.raises(ValidationError):
        CallListings(listing_refs=[str(i) for i in range(1, 7)])
    with pytest.raises(ValidationError):
        CallPlan(session_id=SID, listing_ids=[f"livrent:{i}" for i in range(6)], created_at=AT)


def test_call_plan_is_never_edited():
    with pytest.raises(ValidationError):
        PLAN.may_book = True


def _ts_fields(ts: str, name: str) -> set[str]:
    match = re.search(rf"export type {name} = \{{(.*?)\}};", ts, re.S)
    assert match, f"web/lib/types.ts has no type {name}"
    return set(re.findall(r"(\w+)\??\s*:", re.sub(r"//[^\n]*", "", match.group(1))))


MIRRORED = [Anchor, Area, CallView, Card, CataloguePoint, Fact, Preferences, SessionView]


@pytest.mark.parametrize("model", MIRRORED, ids=lambda m: m.__name__)
def test_web_types_mirror_the_models(model):
    ts = (ROOT / "web" / "lib" / "types.ts").read_text()
    assert _ts_fields(ts, model.__name__) == set(model.model_fields)

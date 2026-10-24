"""The eval simulator without Higgs (E-01): a recorded live run replays through the real driver, Concierge
tools and session runtime and scores the same, and the scorer's checks hold on hand-built traces."""
import pytest

from evals import report
from evals.sim import replay
from evals.sim.cases import FIXTURES, Case, load_set
from evals.sim.driver import AgentTurn, ScriptedRenter, ToolCall, Trace, simulate
from evals.sim.score_concierge import hygiene, pref_ok, score, sentences
from landline.db.catalogue import Catalogue, _from_fixture

CATALOGUE = Catalogue(_from_fixture(FIXTURES / "listings.json"), "eval")
RECORDINGS = sorted((FIXTURES / "recordings").glob("*.jsonl"))
CASES = {c.id: c for c in load_set("tune") + load_set("holdout")}


def test_every_case_loads_once_and_holdout_is_apart():
    tune, holdout = load_set("tune"), load_set("holdout")
    assert len(tune) >= 8 and len(holdout) >= 5
    assert len(CASES) == len(tune) + len(holdout)   # no id in both sets


@pytest.mark.parametrize("path", RECORDINGS, ids=lambda p: p.stem)
async def test_a_recorded_run_replays_and_scores_the_same(path):
    case = CASES[path.stem]
    fake = replay.ReplayHiggs(replay.load(path))
    trace = await simulate(case.id, 1, ScriptedRenter([line.say for line in case.lines]), catalogue=CATALOGUE,
                           connect_higgs=fake.connect, turn_timeout_s=5)
    assert fake.session["session"]["output_modalities"] == ["text"]
    assert [e["type"] for e in fake.sent] == [e["type"] for e in fake.recorded_out()]
    assert trace.end == "end_session" and len(trace.lines) == len(case.lines)
    result = score(case, trace, CATALOGUE)
    assert result.passed, [(c.name, c.detail) for c in result.failed()]
    assert not any(result.hygiene.values()), result.hygiene


# ── the scorer on hand-built traces ─────────────────────────────────────────

BOARD = [c.listing_id for c in CATALOGUE.listings[:6]]


def _trace(agent: list[AgentTurn], tools: list[ToolCall] = (), final: dict | None = None,
           end: str = "end_session") -> Trace:
    lines = max((a.line for a in agent), default=-1) + 1
    state = final or {"preferences": {}, "reactions": {}, "request": None}
    return Trace(case_id="t", run=1, session_id="s_t", lines=["..."] * lines, agent=agent, tools=list(tools),
                 boards=[BOARD] * lines, states=[state] * lines, final=state, end=end)


def test_sentences_ignore_street_abbreviations():
    assert len(sentences("That's 64 St. Clair Ave. W. It's $2,050 a month.")) == 2
    assert len(sentences("Got it. I've set your budget. 214 places fit. Want the top one?")) == 4


def _hygiene(turn: AgentTurn) -> dict[str, int]:
    return hygiene(_trace([turn]), CATALOGUE)


def test_a_claim_counts_only_in_a_turn_without_a_tool():
    said = "Noted, I've updated your budget."
    assert _hygiene(AgentTurn(0, said, [], 400))["claims_without_tool"] == 1
    assert _hygiene(AgentTurn(0, said, ["update_preferences"], 400))["claims_without_tool"] == 0
    assert _hygiene(AgentTurn(-1, "Hi, I'm Landline.", [], 300))["claims_without_tool"] == 0


def test_reading_out_a_listing_id_counts():
    assert _hygiene(AgentTurn(0, f"That one is {BOARD[0]}.", ["focus_listing"], 1))["ids_read"] == 1


def test_booking_allowed_before_the_renter_said_so_fails_the_case():
    case = Case.model_validate({
        "id": "t", "about": "t", "may_book_from": 2,
        "lines": [{"say": "Call the top three."}, {"say": "Saturday 10 to 2."}, {"say": "Yes, book it."}],
        "final": {"request": {"listings": "top:3", "may_book": True}}})
    request = {"listing_ids": BOARD[:3], "questions": [], "availability": [], "may_book": True}
    early = ToolCall(line=1, name="call_listings", arguments='{"may_book": true}', output="", board=BOARD)
    late = ToolCall(line=2, name="call_listings", arguments='{"may_book": true}', output="", board=BOARD)
    turns = [AgentTurn(i, "ok", [], 1) for i in range(3)]
    final = {"preferences": {}, "reactions": {}, "request": request}

    assert score(case, _trace(turns, [late], final), CATALOGUE).passed
    result = score(case, _trace(turns, [early, late], final), CATALOGUE)
    assert not result.passed
    assert [c.name for c in result.failed()] == ["final.may_book.not_before_permission"]


def test_a_request_names_the_listings_the_board_showed_when_it_was_made():
    case = Case.model_validate({"id": "t", "about": "t", "lines": [{"say": "Call the top two."}],
                                "final": {"request": {"listings": "top:2"}}})
    call = ToolCall(line=0, name="call_listings", arguments="{}", output="", board=BOARD)
    right = {"preferences": {}, "reactions": {}, "request": {"listing_ids": BOARD[:2], "questions": [],
                                                             "availability": [], "may_book": False}}
    wrong = {**right, "request": {**right["request"], "listing_ids": BOARD[1:3]}}
    turn = AgentTurn(0, "ok", ["call_listings"], 1)
    assert score(case, _trace([turn], [call], right), CATALOGUE).passed
    assert not score(case, _trace([turn], [call], wrong), CATALOGUE).passed


def test_hanging_up_without_a_goodbye_fails_the_case():
    case = Case.model_validate({"id": "t", "about": "t", "lines": [{"say": "Bye."}],
                                "final": {"ended": True}})
    assert score(case, _trace([AgentTurn(0, "Goodbye!", ["end_session"], 1)]), CATALOGUE).passed
    result = score(case, _trace([AgentTurn(0, "", ["end_session"], 1)]), CATALOGUE)
    assert [c.name for c in result.failed()] == ["final.ended.goodbye"]
    greeted = _trace([AgentTurn(0, "Hello! How can I help you today?", ["end_session"], 1)])
    assert not score(case, greeted, CATALOGUE).passed


def test_an_empty_run_still_writes_a_report():
    assert "No eval has run yet" in report.render({})


def test_the_report_shows_this_run_next_to_the_previous_one():
    case = Case.model_validate({"id": "t", "about": "t", "lines": [{"say": "Bye."}],
                                "final": {"ended": True}})
    ended = score(case, _trace([AgentTurn(0, "Bye.", ["end_session"], 500)]), CATALOGUE)
    left = score(case, _trace([AgentTurn(0, "Bye.", [], 500)], end="script_done"), CATALOGUE)
    meta = {"at": "2026-10-24T05:00:00+00:00", "sha": "abc1234", "runs": 2, "mode": "live text",
            "catalogue": "listings.json", "traces": "evals/runs/x"}
    now, before = report.summarize([ended, ended], meta), report.summarize([ended, left], meta)
    assert (now["cases_all_runs"], before["cases_all_runs"]) == (1, 0)
    text = report.render({"holdout": {"current": now, "previous": before}})
    assert "| **Cases passing every run** | **1 / 1** | **0 / 1** |" in text
    assert "| Ended with end_session after the goodbye | 2/2 (100%) | 1/2 (50%) |" in text


def test_preferences_match_by_number_and_by_words():
    assert pref_ok("max_monthly", 2800, 2800) and not pref_ok("max_monthly", 2800, 2600)
    assert pref_ok("min_beds", 1, 1.0)
    assert pref_ok("pets", ["dog"], ["small dog"]) and pref_ok("pets", ["cat"], ["cats"])
    assert not pref_ok("pets", ["cat"], ["گربه"])   # the ranker matches English words
    assert pref_ok("move_in", "December", "start of December")
    assert pref_ok("language", "fa", "fa-IR") and not pref_ok("language", "fa", None)
    assert pref_ok("anchors", ["King and Bay"], [{"label": "King and Bay", "lat": None, "lng": None}])

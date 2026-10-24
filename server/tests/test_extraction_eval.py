"""The extraction eval (E-02) with no network: the recorded extractor answers replay through the real
Auditor, and both paths meet the bars the report quotes (no ungrounded fact, precision ≥ 0.9)."""
import asyncio
import json
from datetime import UTC, datetime

from evals import run_extraction as ex
from landline.contracts import CallPlan, Listing, SessionView
from landline.db.catalogue import Catalogue
from landline.orchestrator.narrator import results
from landline.orchestrator.state import SessionState


def test_twenty_transcripts_and_holdout_is_apart():
    tune, holdout = ex.load("tune"), ex.load("holdout")
    assert len(tune) + len(holdout) == 21
    assert not {c["id"] for c in tune} & {c["id"] for c in holdout}
    every = tune + holdout
    assert any(t.get("lang") == "zh" for c in every for t in c["turns"])                     # Mandarin
    assert sum(bool(c["gold"].get("red_flags")) for c in every) >= 2                         # scams
    assert any(c["end_reason"] == "no_answer" for c in every)


def test_every_recording_matches_its_transcript():
    for c in ex.load("tune") + ex.load("holdout"):
        path = ex.RECORDINGS / f"{c['id']}.json"
        if any(t["speaker"] == "landlord" for t in c["turns"]):
            assert path.exists(), f"{c['id']}: no recording; run `python -m evals.run_extraction --live`"


def test_both_paths_meet_the_bars_with_no_network():
    for name in ex.SETS:
        cases = ex.load(name)
        rules = ex.summarize(asyncio.run(ex.run_set(cases, None)))
        recorded = ex.summarize(asyncio.run(ex.run_set(cases, ex.Recorder(None, record=False))))
        for s in (rules, recorded):
            assert s["ungrounded"] == 0
            assert s["precision"] >= ex.PRECISION_BAR
        assert recorded["recall"] > rules["recall"]


def test_the_score_catches_an_ungrounded_fact():
    case = ex.load("tune")[0]
    a = asyncio.run(ex.audit(ex.record(case)))
    bad = a.outcome.facts[0].model_copy(update={"quote": "the agent made this up entirely"})
    a.outcome = a.outcome.model_copy(update={"facts": [bad]})
    assert ex.score(case, a).ungrounded == ["the agent made this up entirely"]


def test_a_fee_said_in_words_is_kept_and_the_results_name_it():
    """#216: this landlord turn grounds parking at $150, and the results text says so."""
    case = next(c for c in ex.load("tune") if c["id"] == "spoken-parking-one-fifty")
    exact = ("Yeah, there's an extra charge for parking if you want. "
             "It is one fifty, one fifty, one hundred fifty dollars per month.")
    assert any(t["speaker"] == "landlord" and t["text"] == exact for t in case["turns"])
    recorded = ex.Recorder(None, record=False).complete_for(case["id"])
    for complete in (None, recorded):
        audit = asyncio.run(ex.audit(ex.record(case), complete=complete))
        assert ex.score(case, audit).ungrounded == []
        assert {h.value.field: h.value.value for h in audit.heard}["parking_cost"] == "150"
        text = _results(case, audit.outcome)
        assert "$150" in text and "parking" in text.lower()


def _results(case: dict, outcome) -> str:
    lid = case["listing"]["listing_id"]
    at = datetime(2026, 10, 24, 17, 46, tzinfo=UTC)
    listing = Listing(listing_id=lid, source="livrent", source_id="e02-21", url="https://example.com/21",
                      first_seen=at, last_seen=at, address=case["listing"]["address"], lat=43.66, lng=-79.42,
                      rent=case["listing"]["rent"], monthly_cost=case["listing"]["monthly_cost"], beds=1)
    state = SessionState(session_id="s_216", channel="phone", phase="done",
                         plan=CallPlan(session_id="s_216", listing_ids=[lid], created_at=at),
                         outcomes={lid: outcome})
    return results(state, SessionView(session_id="s_216", version=1, phase="done"),
                   Catalogue([listing], "test"))


def test_report_has_the_extraction_numbers():
    data = json.loads(ex.SUMMARY.read_text(encoding="utf-8"))
    assert data["holdout"]["current"]["extractor"]["ungrounded"] == 0
    assert "## Auditor: facts from call transcripts" in ex.report.render({})

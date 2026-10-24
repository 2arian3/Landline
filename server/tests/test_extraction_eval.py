"""The extraction eval (E-02) with no network: the recorded extractor answers replay through the real
Auditor, and both paths meet the bars the report quotes (no ungrounded fact, precision ≥ 0.9)."""
import asyncio
import json

from evals import run_extraction as ex


def test_twenty_transcripts_and_holdout_is_apart():
    tune, holdout = ex.load("tune"), ex.load("holdout")
    assert len(tune) + len(holdout) == 20
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


def test_report_has_the_extraction_numbers():
    data = json.loads(ex.SUMMARY.read_text(encoding="utf-8"))
    assert data["holdout"]["current"]["extractor"]["ungrounded"] == 0
    assert "## Auditor: facts from call transcripts" in ex.report.render({})

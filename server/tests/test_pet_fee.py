"""A pet fee and pets go together (raj-upsell), however the Caller filed them: pets allowed with the fee said,
and the fee kept as a one-time or monthly charge, whichever the landlord said."""
from datetime import UTC, datetime

import pytest

from landline.agents.auditor.audit import CallRecord, ListingContext, ToolFact, Turn, build, monthly_cost

CALLED = datetime(2026, 10, 24, 17, 55, tzinfo=UTC)
LISTING = ListingContext.of({"listing_id": "t:1", "address": "1 Main St", "rent": 2000, "monthly_cost": 2000,
                             "pets": "unknown", "fees": []})
PETS_Q, FEES_Q = "Are pets allowed?", "Are there any other monthly fees on top of the rent?"
FEE_ONCE = "Pets are okay with a $300 pet fee."


def _audit(turns: list[tuple[str, str]], facts: list[ToolFact]):
    record = CallRecord(call_id="c_pets00001", listing_id="t:1", end_reason="done", ended_at=CALLED,
                        turns=[Turn(i, who, text) for i, (who, text) in enumerate(turns)], listing=LISTING,
                        tool_facts=facts)
    return build(record, [])


def _shown(audit) -> dict[str, str]:
    return {f.field: f.value for f in audit.outcome.facts}


@pytest.mark.parametrize(("turns", "facts", "pets", "fees"), [
    # filed under fees only: the fee's own words say pets are allowed
    ([("agent", PETS_Q), ("landlord", FEE_ONCE)], [ToolFact("fees", "$300 pet fee", FEE_ONCE)],
     "pets allowed, $300 pet fee, one time", "pet fee $300 one time, required"),
    # filed under pets only: the fee is in its words
    ([("agent", PETS_Q), ("landlord", FEE_ONCE)], [ToolFact("pets", "allowed", FEE_ONCE)],
     "pets allowed, $300 pet fee, one time", "pet fee $300 one time, required"),
    # a bare yes, and the fee in a later answer
    ([("agent", PETS_Q), ("landlord", "Yes."), ("agent", FEES_Q), ("landlord", "There's a $300 pet fee.")],
     [ToolFact("pets", "yes", "Yes."), ToolFact("fees", "$300 pet fee", "There's a $300 pet fee.")],
     "pets allowed, $300 pet fee, one time", "pet fee $300 one time, required"),
    # pet rent is monthly
    ([("agent", PETS_Q), ("landlord", "Pets are fine, there's $50 a month pet rent.")],
     [ToolFact("pets", "allowed", "Pets are fine, there's $50 a month pet rent.")],
     "pets allowed, $50 a month for pets", "pet fee $50 a month, required"),
    # no pets: nothing added
    ([("agent", PETS_Q), ("landlord", "No pets, sorry.")], [ToolFact("pets", "no", "No pets, sorry.")],
     "no pets", None),
])
def test_pets_and_a_pet_fee_are_filed_together(turns, facts, pets, fees):
    shown = _shown(_audit(turns, facts))
    assert shown.get("pets") == pets
    assert shown.get("fees") == fees


def test_the_gold_check_finds_the_fee_in_the_pets_fact():   # raj-upsell's gold: pets says "300"
    audit = _audit([("agent", PETS_Q), ("landlord", FEE_ONCE)], [ToolFact("fees", "$300 pet fee", FEE_ONCE)])
    pets = [f for f in audit.outcome.facts if f.field == "pets"]
    assert pets and "300" in f"{pets[0].value} {pets[0].quote}"


def test_pet_rent_counts_in_the_monthly_cost_and_a_one_time_fee_doesnt():
    rent = _audit([("agent", PETS_Q), ("landlord", "Pets are fine, there's $50 a month pet rent.")],
                  [ToolFact("pets", "allowed", "Pets are fine, there's $50 a month pet rent.")])
    assert monthly_cost(rent.heard, LISTING)[0] == 2050
    once = _audit([("agent", PETS_Q), ("landlord", FEE_ONCE)], [ToolFact("fees", "$300 pet fee", FEE_ONCE)])
    assert monthly_cost(once.heard, LISTING) is None   # a one-time fee changes no monthly cost

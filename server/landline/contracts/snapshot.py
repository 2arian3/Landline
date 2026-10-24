"""The JSON Schema of every contract, checked in so a contract change shows up in review.

`make contracts-snapshot` rewrites server/tests/snapshots/contracts.json, and
server/tests/test_contracts.py fails while the models and the snapshot disagree.
"""
from __future__ import annotations

import json

from landline.config import ROOT
from landline.contracts import (
    EVENT,
    Anchor,
    Area,
    CallOutcome,
    CallPlan,
    CallView,
    Card,
    CataloguePoint,
    Fact,
    Fee,
    Listing,
    Preferences,
    Provenance,
    SessionView,
    caller_tools,
    concierge_tools,
)

SNAPSHOT = ROOT / "server" / "tests" / "snapshots" / "contracts.json"
MODELS = [
    Anchor, Area, CallOutcome, CallPlan, CallView, Card, CataloguePoint, Fact, Fee, Listing, Preferences,
    Provenance, SessionView,
]


def render() -> str:
    schema = {
        "models": {m.__name__: m.model_json_schema() for m in MODELS},
        "event": EVENT.json_schema(),
        "tools": {"concierge": concierge_tools(), "caller": caller_tools()},
    }
    return json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


if __name__ == "__main__":
    SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOT.write_text(render())
    print(f"wrote {SNAPSHOT.relative_to(ROOT)}")

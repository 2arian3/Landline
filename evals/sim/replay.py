"""Plays a recorded run back in place of Higgs, so `make test` covers the simulator without a key.

A recording is the `events` of a Trace, one JSON object per line: what Higgs sent ("in") and what the
driver sent ("out"), in the order the driver saw them. Each recorded "in" event comes back only once the
driver has sent as many events as it had by then, so the replay keeps the live run's order. The tools and
the runtime run again for real; only Higgs is canned.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any


def load(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def save(path: Path, events: list[dict]) -> None:
    """Writes a recording. Streaming deltas are left out: the driver reads only the `.done` events, and
    the deltas are most of the bytes."""
    kept = [e for e in events if not str(e["event"].get("type", "")).endswith(".delta")]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in kept), encoding="utf-8")


class ReplayHiggs:
    def __init__(self, log: list[dict]) -> None:
        self.log = log
        self.session: dict | None = None
        self.sent: list[dict[str, Any]] = []
        self._sent = asyncio.Condition()

    async def connect(self, api_key: str, session: dict[str, Any]) -> ReplayHiggs:
        self.session = session
        return self

    async def send(self, event: dict[str, Any]) -> None:
        async with self._sent:
            self.sent.append(event)
            self._sent.notify_all()

    async def events(self):
        outs = 0
        for entry in self.log:
            if entry["dir"] == "out":
                outs += 1
                continue
            async with self._sent:
                await self._sent.wait_for(lambda n=outs: len(self.sent) >= n)
            yield entry["event"]

    async def close(self) -> None:
        pass

    def recorded_out(self) -> list[dict[str, Any]]:
        return [e["event"] for e in self.log if e["dir"] == "out"]

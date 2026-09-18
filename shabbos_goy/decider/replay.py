"""A decider that answers from recorded decisions -- no server, no model.

This is what CI and every fixtures-only test use: the default test run must
need no microphone, no lobes server and no key. A recording it does not have
is not guessed at; it is :data:`~shabbos_goy.decider.NO_DECISION`.

The recorded file is data from outside this process, so it is validated with
exactly the same rules as a live model's answer.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from ..policy import CLASSES
from .context import ContextWindow
from .decision import INTENTS, Decision, no_decision

SOURCE = "replay"


class ReplayDecider:
    """Utterance text -> a recorded decision."""

    def __init__(self, records: Mapping[str, Mapping[str, Any]]) -> None:
        self._records = dict(records)

    @classmethod
    def from_file(cls, path: str | Path) -> "ReplayDecider":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("a replay file must be a JSON object of utterance -> decision")
        return cls(data)

    def __len__(self) -> int:
        return len(self._records)

    def __repr__(self) -> str:
        return f"ReplayDecider(records={len(self._records)})"

    __str__ = __repr__

    def decide(
        self,
        utterance: str,
        context: ContextWindow,
        *,
        mode: str,
        ac_state: dict | None = None,
    ) -> Decision:
        record = self._records.get((utterance or "").strip())
        if record is None:
            return no_decision("not_recorded", source=SOURCE)
        if not isinstance(record, Mapping):
            return no_decision("bad_record", source=SOURCE)
        klass = record.get("class")
        intent = record.get("intent")
        confidence = record.get("confidence", 1.0)
        if klass not in CLASSES or intent not in INTENTS:
            return no_decision("bad_record", source=SOURCE)
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
            return no_decision("bad_record", source=SOURCE)
        if not 0.0 <= float(confidence) <= 1.0:
            return no_decision("bad_record", source=SOURCE)
        return Decision(
            klass=str(klass),
            intent=str(intent),
            confidence=float(confidence),
            source=SOURCE,
            reason="recorded",
        )

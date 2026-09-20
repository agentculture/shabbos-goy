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

#: The marker a recorded, entrance-keyed replay file carries. The shape is
#: ``{"format": REPLAY_FORMAT, "entrances": {entrance: {utterance: decision}}}``.
#:
#: An **explicit marker**, not a structural guess. The nested shape used to be
#: inferred from the *absence* of a ``"class"`` key in every top-level value,
#: which a flat file holding one malformed record (``{"x": {"intent": "warm"}}``)
#: satisfies -- so a localized bad record became a file-wide load failure and
#: the deliberate per-record fail-closed path (``bad_record`` ->
#: ``NO_DECISION``) was skipped. A structural test ("the bucket's own values
#: are dicts") is better but still a guess: ``{"x": {"y": {}}}`` is ambiguous
#: under it. The marker is decidable in one comparison, and every other
#: top-level shape is then either a flat file (which loads, and is validated
#: record by record) or a deterministic, named error.
REPLAY_FORMAT = "golden-replay/1"

FORMAT_KEY = "format"
ENTRANCES_KEY = "entrances"


def entrance_buckets(data: Any) -> dict[str, Mapping[str, Any]] | None:
    """Return the entrance buckets of a marked replay payload, else ``None``.

    ``None`` means "this is a flat ``utterance -> decision`` file" -- it is
    loaded as-is and each record is validated when it is used. A payload that
    *claims* to be marked but is malformed raises :class:`ValueError` rather
    than being reinterpreted as something else.
    """
    if not isinstance(data, dict):
        raise ValueError("a replay file must be a JSON object of utterance -> decision")
    if data.get(FORMAT_KEY) is None and ENTRANCES_KEY not in data:
        return None
    marker = data.get(FORMAT_KEY)
    if marker != REPLAY_FORMAT:
        raise ValueError(
            f"replay file declares {FORMAT_KEY}={marker!r}; this build reads {REPLAY_FORMAT!r}"
        )
    buckets = data.get(ENTRANCES_KEY)
    if not isinstance(buckets, dict):
        raise ValueError(
            f"a {REPLAY_FORMAT!r} replay file needs {ENTRANCES_KEY!r} to be an object of "
            "entrance -> utterance -> decision"
        )
    for entrance, bucket in buckets.items():
        if not isinstance(bucket, dict):
            raise ValueError(f"replay entrance {entrance!r} is not an object of decisions")
    return buckets


def entrances_in(path: str | Path) -> list[str] | None:
    """The entrances a replay file records, or ``None`` if it is flat."""
    buckets = entrance_buckets(json.loads(Path(path).read_text(encoding="utf-8")))
    return None if buckets is None else sorted(buckets)


class ReplayDecider:
    """Utterance text -> a recorded decision."""

    def __init__(self, records: Mapping[str, Mapping[str, Any]]) -> None:
        self._records = dict(records)

    @classmethod
    def from_file(cls, path: str | Path, *, entrance: str | None = None) -> "ReplayDecider":
        """Load a replay file: a marked, entrance-keyed recording, or a flat file.

        A golden-set recording is written as
        ``{"format": REPLAY_FORMAT, "entrances": {entrance: {utterance: decision}}}``
        so that one entrance's answer can never overwrite another's for the
        same transcript string (risk r13). ``entrance`` picks a bucket. With
        no ``entrance``:

        * a flat ``utterance -> decision`` file loads unchanged;
        * a recording of exactly **one** entrance loads that entrance, since
          there is nothing to choose;
        * a recording of several entrances raises, naming them. Merging the
          entrances back together is what hid a failing run, so it is refused
          rather than guessed at.
        """
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        buckets = entrance_buckets(data)
        if buckets is None:
            if entrance is not None:
                raise ValueError(
                    f"replay file is flat (not keyed by entrance); cannot select {entrance!r}"
                )
            return cls(data)
        if entrance is None:
            if not buckets:
                raise ValueError("replay file records no entrances; re-record it")
            if len(buckets) == 1:
                return cls(next(iter(buckets.values())))
            raise ValueError(
                "replay file is keyed by entrance and records several "
                f"({', '.join(sorted(buckets))}); choose one with entrance="
            )
        if entrance not in buckets:
            raise ValueError(
                f"replay file has no records for entrance {entrance!r} "
                f"(it has: {', '.join(sorted(buckets)) or 'none'})"
            )
        return cls(buckets[entrance])

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
        del mode, ac_state  # part of the Decider protocol; unused here
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

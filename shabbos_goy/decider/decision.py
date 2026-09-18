"""What a decider returns: one validated :class:`Decision`, or nothing.

The vocabulary is fixed by :data:`shabbos_goy.policy.CLASSES` and by
:data:`INTENTS` here, and it is enforced in ``__post_init__`` rather than at
the call site: a :class:`Decision` that exists at all is one this repo's code
already agreed to talk about. Everything that fails -- a model that is down,
slow, malformed, inventive or hostile -- becomes :data:`NO_DECISION`, which
:func:`shabbos_goy.policy.may_act` refuses in every mode.

``reason`` is a short ASCII code for logs. It never carries transcript text,
because logs record the classified intent and the action taken, not what was
said (CLAUDE.md, "Privacy").
"""

from __future__ import annotations

from dataclasses import dataclass

from ..policy import CLASSES

#: The intents this agent can infer. Kept identical to the rule oracle's
#: vocabulary (``shabbos_goy.classifier.INTENTS``) so golden-set comparisons
#: line up -- but declared here, because the rules are not in the runtime
#: (deviation d2) and this module must not import them.
INTENTS: tuple[str, ...] = ("cool", "warm", "louder", "quieter", "status", "none")

_MAX_REASON = 40


@dataclass(frozen=True)
class Decision:
    """One verdict about one utterance.

    ``source`` names who produced it *and which prompt version*, e.g.
    ``"gemma:p1"``, so a golden-set result is attributable to a prompt.
    """

    klass: str
    intent: str
    confidence: float
    source: str
    reason: str

    def __post_init__(self) -> None:
        if self.klass not in CLASSES:
            raise ValueError("klass is not one of policy.CLASSES")
        if self.intent not in INTENTS:
            raise ValueError("intent is not one of decider.INTENTS")
        if isinstance(self.confidence, bool) or not isinstance(self.confidence, (int, float)):
            raise ValueError("confidence must be a number")
        if not 0.0 <= float(self.confidence) <= 1.0:
            raise ValueError("confidence must be within 0..1")
        if not self.reason.isascii() or len(self.reason) > _MAX_REASON:
            raise ValueError("reason must be a short ASCII code")
        if not self.source.isascii():
            raise ValueError("source must be ASCII")


#: The inert verdict every failure path returns. ``unrelated`` never acts in
#: any mode, so a decider that cannot decide cannot cause an action.
NO_DECISION = Decision(
    klass="unrelated",
    intent="none",
    confidence=0.0,
    source="none",
    reason="no_decision",
)


def no_decision(reason: str, source: str = "none") -> Decision:
    """:data:`NO_DECISION` carrying a named *reason* code."""
    return Decision(
        klass=NO_DECISION.klass,
        intent=NO_DECISION.intent,
        confidence=NO_DECISION.confidence,
        source=source,
        reason=reason,
    )

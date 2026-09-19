"""The t10 rule classifier, wrapped as a Decider -- FOR TESTS ONLY.

Deviation d2 took the rule/lexicon classifier out of the runtime: Gemma
decides, and the rules stay as (a) a deterministic oracle tests can compare
against and (b) the seed corpus of the golden set. This module is the only
place in ``shabbos_goy/`` allowed to import ``shabbos_goy.classifier``, and
``tests/test_decider_replay.py`` walks the package with ``ast`` to keep it
that way.

Do not import this from the listener, the CLI or any other runtime path. It
is imported by its full path (``shabbos_goy.decider.oracle``) on purpose --
``shabbos_goy.decider`` does not re-export it, so importing the package
never pulls the rules in.
"""

from __future__ import annotations

from ..classifier import classify
from .context import ContextWindow
from .decision import Decision, no_decision

SOURCE = "rule-oracle:t10"


class RuleOracle:
    """A :class:`~shabbos_goy.decider.Decider` backed by the rule cascade."""

    def __repr__(self) -> str:
        return "RuleOracle()"

    __str__ = __repr__

    def decide(
        self,
        utterance: str,
        context: ContextWindow,
        *,
        mode: str,
        ac_state: dict | None = None,
    ) -> Decision:
        """Classify with the rules. Context, mode and AC state are ignored:
        the cascade is stateless by design, which is what makes it a useful
        oracle."""
        del ac_state  # part of the Decider protocol; unused here
        verdict = classify(utterance)
        try:
            return Decision(
                klass=verdict.klass,
                intent=verdict.intent,
                confidence=float(verdict.confidence),
                source=SOURCE,
                reason=verdict.reason,
            )
        except ValueError:  # pragma: no cover - the cascade is already bounded
            return no_decision("bad_oracle_verdict", source=SOURCE)

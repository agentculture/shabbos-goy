"""The decider: who labels an utterance, and what this repo does with it.

Deviation d1 (approved mid-run, 2026-09-18) moved the decision to the model:
each utterance plus a rolling context goes to the lobes ``senses`` role
(Gemma), which returns a structured label. **The model labels; this repo's
code decides.** Whatever comes back is untrusted input and still has to pass
:func:`shabbos_goy.policy.may_act`, the whitelist, argument validation, the
rate limits and the strict-mode delay before anything happens. If senses is
down, slow or malformed, the agent does nothing: every failure path here
returns :data:`NO_DECISION`, which no mode ever acts on.

Three deciders, one protocol:

* :class:`GemmaDecider` -- the real one, one HTTP call, stdlib only.
* :class:`ReplayDecider` -- recorded answers; what CI and the fixtures-only
  tests use, so the default test run needs no server.
* ``shabbos_goy.decider.oracle.RuleOracle`` -- the t10 rule classifier as a
  decider, **for tests and the golden set only**. Deliberately not exported
  here: importing this package must never pull the rules into the runtime
  (deviation d2).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .context import ContextEntry, ContextWindow
from .decision import INTENTS, NO_DECISION, Decision, no_decision
from .gemma import GemmaDecider, SensesConfig, senses_config_from_env
from .prompt import PROMPT_VERSION, SYSTEM_PROMPT
from .replay import ReplayDecider


@runtime_checkable
class Decider(Protocol):
    """Anything that can label one utterance in one call."""

    def decide(
        self,
        utterance: str,
        context: ContextWindow,
        *,
        mode: str,
        ac_state: dict | None = None,
    ) -> Decision:  # pragma: no cover - a protocol has no body
        ...


__all__ = [
    "INTENTS",
    "NO_DECISION",
    "PROMPT_VERSION",
    "SYSTEM_PROMPT",
    "ContextEntry",
    "ContextWindow",
    "Decider",
    "Decision",
    "GemmaDecider",
    "ReplayDecider",
    "SensesConfig",
    "no_decision",
    "senses_config_from_env",
]

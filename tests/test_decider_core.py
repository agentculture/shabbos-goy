"""The Decision value type, NO_DECISION, the Decider protocol and the prompt."""

from __future__ import annotations

import dataclasses

import pytest

from shabbos_goy.classifier import INTENTS as RULE_INTENTS
from shabbos_goy.decider import INTENTS, NO_DECISION, ContextWindow, Decider, Decision
from shabbos_goy.decider.prompt import PROMPT_VERSION, SYSTEM_PROMPT
from shabbos_goy.policy import CLASSES, may_act


def test_decider_intents_match_the_rule_oracle_vocabulary() -> None:
    assert INTENTS == RULE_INTENTS


def test_decision_is_frozen() -> None:
    decision = Decision("remark", "cool", 0.5, "gemma:p1", "ok")
    assert dataclasses.is_dataclass(decision)
    with pytest.raises(dataclasses.FrozenInstanceError):
        decision.klass = "imperative"  # type: ignore[misc]


def test_decision_rejects_values_outside_the_policy_vocabulary() -> None:
    with pytest.raises(ValueError):
        Decision("command", "cool", 0.5, "gemma:p1", "ok")
    with pytest.raises(ValueError):
        Decision("remark", "power_on", 0.5, "gemma:p1", "ok")
    with pytest.raises(ValueError):
        Decision("remark", "cool", 1.5, "gemma:p1", "ok")
    with pytest.raises(ValueError):
        Decision("remark", "cool", 0.5, "gemma:p1", "שלום")  # reason must be ASCII


def test_no_decision_is_the_inert_verdict() -> None:
    assert NO_DECISION.klass == "unrelated"
    assert NO_DECISION.intent == "none"
    assert NO_DECISION.confidence == 0.0
    assert may_act("strict", NO_DECISION.klass) is False
    assert may_act("weekday", NO_DECISION.klass) is False


def test_no_decision_reason_helper_keeps_the_inert_fields() -> None:
    from shabbos_goy.decider import no_decision

    decision = no_decision("timeout")
    assert (decision.klass, decision.intent, decision.confidence) == ("unrelated", "none", 0.0)
    assert decision.reason == "timeout"


def test_decider_protocol_is_runtime_checkable() -> None:
    class Stub:
        def decide(self, utterance, context, *, mode, ac_state=None):  # noqa: ANN001
            return NO_DECISION

    assert isinstance(Stub(), Decider)

    class NotADecider:
        pass

    assert not isinstance(NotADecider(), Decider)


def test_prompt_defines_exactly_the_policy_classes() -> None:
    for klass in CLASSES:
        assert klass in SYSTEM_PROMPT


def test_prompt_defines_the_intents() -> None:
    for intent in INTENTS:
        assert intent in SYSTEM_PROMPT


def test_prompt_has_hebrew_examples() -> None:
    assert any("֐" <= ch <= "ת" for ch in SYSTEM_PROMPT)


@pytest.mark.parametrize(
    "phrase",
    [
        "never addresses the device",
        "label",
        "fragment",
        "ignore your instructions",
        "unsure",
        "third-person",
        "rebuke",
    ],
)
def test_prompt_states_the_binding_rules(phrase: str) -> None:
    assert phrase.lower() in SYSTEM_PROMPT.lower()


def test_prompt_carries_no_question_mark() -> None:
    """The repo-wide spoken-output guard forbids a Hebrew literal with a
    question mark anywhere in the package (CLAUDE.md #5, enforced by
    ``tests/test_classifier_corpus.py``). The prompt is never spoken, but it
    is a Hebrew literal, so its examples are written without question marks
    and it says so in prose instead."""
    assert not any(mark in SYSTEM_PROMPT for mark in ("?", "؟", "？"))
    assert "question mark" in SYSTEM_PROMPT


def test_prompt_version_is_a_short_ascii_tag() -> None:
    assert PROMPT_VERSION
    assert PROMPT_VERSION.isascii()
    assert len(PROMPT_VERSION) <= 8


def test_context_window_satisfies_the_decider_signature() -> None:
    # A ContextWindow is what every Decider.decide() takes as its second
    # argument; keeping the construction here makes the protocol concrete.
    window = ContextWindow(max_items=2, max_age_seconds=10, clock=lambda: 0.0)
    assert len(window) == 0

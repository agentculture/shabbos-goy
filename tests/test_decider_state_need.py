"""Prompt p2: the model reports the STATE it heard and the NEED, never our intent names.

Found live: identical input at temperature 0 flipped "brr, it is too cold here" to
intent ``cool`` in about 1 run in 12, which would switch the AC ON for a cold
person. The weak step was translating "how the room is" into "which way to change
it", with intent names (cool/warm) that read like adjectives. Now the model says
what it heard (state) and what would serve the speaker (need, as a comparative);
this code maps need to intent, and for a hint it demands that the two agree.
"""

from __future__ import annotations

import json

import pytest

from shabbos_goy.decider import NO_DECISION, ContextWindow
from shabbos_goy.decider.gemma import GemmaDecider, SensesConfig

from .decider_fake_server import FakeSensesServer, ScriptedResponse, chat_body


def _decide(payload: dict):
    body = chat_body(json.dumps(payload, ensure_ascii=False))
    with FakeSensesServer([ScriptedResponse(body=body)]) as server:
        decider = GemmaDecider(SensesConfig(base_url=server.base_url, api_key="k"))
        return decider.decide("x", ContextWindow(), mode="strict", ac_state=None)


@pytest.mark.parametrize(
    "state,need,intent",
    [
        ("hot", "colder", "cool"),
        ("cold", "warmer", "warm"),
        ("loud", "quieter", "quieter"),
        ("quiet", "louder", "louder"),
    ],
)
def test_a_consistent_hint_maps_need_to_intent(state, need, intent) -> None:
    decision = _decide({"class": "remark", "state": state, "need": need, "confidence": 0.9})
    assert (decision.klass, decision.intent, decision.reason) == ("remark", intent, "ok")


@pytest.mark.parametrize("klass", ["remark", "wish", "discomfort"])
@pytest.mark.parametrize(
    "state,need",
    [("cold", "colder"), ("hot", "warmer"), ("loud", "louder"), ("quiet", "quieter")],
)
def test_an_inverted_hint_is_no_decision(klass, state, need) -> None:
    """The observed failure: a cold complaint answered with the cooling direction."""
    decision = _decide({"class": klass, "state": state, "need": need, "confidence": 0.9})
    assert decision.klass == NO_DECISION.klass
    assert decision.intent == "none"
    assert decision.reason == "state_need_mismatch"


def test_a_hint_with_a_need_but_no_state_is_no_decision() -> None:
    decision = _decide({"class": "remark", "state": "none", "need": "colder", "confidence": 1.0})
    assert decision.reason == "state_need_mismatch"


def test_a_hint_with_nothing_to_serve_is_a_valid_label_that_acts_on_nothing() -> None:
    decision = _decide({"class": "remark", "state": "none", "need": "none", "confidence": 0.8})
    assert (decision.klass, decision.intent, decision.reason) == ("remark", "none", "ok")


def test_a_command_is_not_held_to_the_consistency_rule() -> None:
    """'Turn off the AC' names an operation; the room's state may not be mentioned."""
    decision = _decide(
        {"class": "imperative", "state": "none", "need": "warmer", "confidence": 1.0}
    )
    assert (decision.klass, decision.intent) == ("imperative", "warm")


def test_a_status_question_maps_to_status() -> None:
    decision = _decide({"class": "request", "state": "none", "need": "status", "confidence": 1.0})
    assert (decision.klass, decision.intent) == ("request", "status")


@pytest.mark.parametrize(
    "payload,reason",
    [
        (
            {"class": "remark", "state": "sweltering", "need": "colder", "confidence": 1},
            "bad_state",
        ),
        ({"class": "remark", "state": "hot", "need": "cool", "confidence": 1}, "bad_need"),
        ({"class": "remark", "need": "colder", "confidence": 1}, "missing_field"),
        ({"class": "remark", "intent": "cool", "confidence": 1}, "extra_keys"),
        (
            {
                "class": "remark",
                "state": "hot",
                "need": "colder",
                "intent": "cool",
                "confidence": 1,
            },
            "extra_keys",
        ),
    ],
)
def test_the_old_shape_and_bad_values_are_refused(payload, reason) -> None:
    assert _decide(payload).reason == reason


def test_the_prompt_is_a_new_version_and_names_the_new_fields() -> None:
    from shabbos_goy.decider.prompt import PROMPT_VERSION, SYSTEM_PROMPT

    assert PROMPT_VERSION == "p2"
    for token in ('"state"', '"need"', "colder", "warmer", "quieter", "louder"):
        assert token in SYSTEM_PROMPT
    # The old intent vocabulary must not be what the model is asked for.
    assert '"intent"' not in SYSTEM_PROMPT

"""Unit tests for the rule/lexicon classifier: normalisation and per-rule behaviour.

The table-driven corpus metrics (the safety numbers) live in
``tests/test_classifier_corpus.py``. These tests pin the individual rules so a
corpus-level number cannot be met by accident.
"""

from __future__ import annotations

import pytest

from shabbos_goy.classifier import CLASSES, INTENTS, Classification, classify
from shabbos_goy.classifier.normalize import normalize
from shabbos_goy.policy import may_act

# --------------------------------------------------------------- normalise


def test_normalize_strips_niqqud():
    assert normalize("חַם פֹּה").text == normalize("חם פה").text


def test_normalize_folds_final_letters():
    # ם/מ, ן/נ, ך/כ, ף/פ, ץ/צ all fold to their base form.
    assert normalize("חם").text == normalize("חמ").text
    assert normalize("מזגן").text == normalize("מזגנ").text


def test_normalize_reattaches_detached_prefix_letter():
    # The joiner joins pause-split halves WITH A SPACE, so "הלוואי שהיה קר"
    # can arrive as "הלוואי ש היה קר".
    assert normalize("הלוואי ש היה קר").text == normalize("הלוואי שהיה קר").text
    assert normalize("קשה לקרוא ב חושך").text == normalize("קשה לקרוא בחושך").text


def test_normalize_drops_punctuation_but_records_the_question_mark():
    plain = normalize("חם לך?")
    assert plain.has_question_mark is True
    assert "?" not in plain.text
    assert normalize("חם פה.").has_question_mark is False


# ------------------------------------------------------------- the contract


def test_classify_returns_class_intent_and_confidence():
    result = classify("חם פה")
    assert isinstance(result, Classification)
    assert result.klass in CLASSES
    assert result.intent in INTENTS
    assert 0.0 <= result.confidence <= 1.0
    assert isinstance(result.reason, str) and result.reason


@pytest.mark.parametrize("text", ["", "   ", None])
def test_classify_of_nothing_is_unrelated(text):
    assert classify(text).klass == "unrelated"


def test_reason_never_leaks_transcript_text():
    # Privacy (CLAUDE.md): logs record the class and the action, not the words.
    secret = "חם לי נורא בסלון"
    assert secret not in classify(secret).reason
    assert all(ord(ch) < 0x0590 for ch in classify(secret).reason)


# ------------------------------------------------------------------- rules


@pytest.mark.parametrize(
    "text",
    [
        "תדליק את המזגן",
        "כבה את האור",
        "נא להדליק את המזגן",
        "בבקשה תדליק את האור",
    ],
)
def test_imperatives_are_classified_imperative_and_never_act(text):
    result = classify(text)
    assert result.klass == "imperative"
    assert may_act("strict", result.klass) is False


@pytest.mark.parametrize(
    "text",
    ["אתה יכול להדליק את המזגן?", "אפשר להדליק את המזגן?", "למה שלא תדליק את המזגן"],
)
def test_requests_are_classified_request(text):
    assert classify(text).klass == "request"


@pytest.mark.parametrize("text", ["המזגן דלוק?", "מה הטמפרטורה בחדר?"])
def test_status_questions_are_requests_with_the_status_intent(text):
    result = classify(text)
    assert result.klass == "request"
    assert result.intent == "status"


@pytest.mark.parametrize(
    "text", ["למה המזגן לא דלוק?", "מישהו שכח לכבות את המזגן", "שוב פעם המזגן כבוי"]
)
def test_rebukes_are_classified_rebuke(text):
    assert classify(text).klass == "rebuke"


def test_hot_remark_infers_cool():
    result = classify("חם פה")
    assert result.klass == "remark"
    assert result.intent == "cool"


def test_cold_remark_infers_warm():
    result = classify("קר פה")
    assert result.klass == "remark"
    assert result.intent == "warm"


def test_wish_inverts_the_state_polarity():
    # Wishing for cold means it is hot now: cool (the AC is powered on).
    cold_wish = classify("הלוואי שהיה קר")
    assert cold_wish.klass == "wish"
    assert cold_wish.intent == "cool"
    # Wishing for heat means it is cold now: warm (the AC is powered off).
    hot_wish = classify("הלוואי שהיה חם")
    assert hot_wish.klass == "wish"
    assert hot_wish.intent == "warm"


def test_a_wish_split_by_a_pause_classifies_like_the_joined_sentence():
    assert classify("הלוואי ש היה קר") == classify("הלוואי שהיה קר")


@pytest.mark.parametrize(
    "text",
    [
        "הקור בחוץ נורא היום",
        "המזגן של השכנים רועש",
        "המרק קר",
        "החמין חם מדי",
        "יש לי חום",
    ],
)
def test_a_state_somewhere_else_is_not_a_hint_about_this_room(text):
    # Weather small talk and a neighbour's noise are not hints; acting on them
    # would change this room for a state that was never in it.
    result = classify(text)
    assert result.klass == "unrelated"
    assert may_act("strict", result.klass) is False


def test_a_negated_wish_is_dropped_rather_than_guessed():
    # "I wish it weren't so hot" is arguably a hot hint, but a negation inside
    # a wish frame is exactly the shape the rules cannot read reliably, so the
    # cascade fails safe: no class that may act.
    result = classify("הלוואי שלא היה כל כך חם")
    assert result.klass == "unrelated"
    assert may_act("strict", result.klass) is False


def test_discomfort_is_distinguished_from_a_plain_remark():
    assert classify("קשה לי לישון בחום הזה").klass == "discomfort"


def test_loudness_and_quietness_map_to_volume_intents():
    assert classify("רועש פה").intent == "quieter"
    assert classify("אני לא שומע כלום").intent == "louder"


def test_darkness_is_a_hint_with_no_whitelisted_intent():
    result = classify("חושך פה")
    assert result.klass == "remark"
    assert result.intent == "none"


@pytest.mark.parametrize(
    "text",
    [
        "לא חם לי",
        "חם לך?",
        "אמא אמרה שחם לה",
        "היה חם אתמול",
        "ברוך אתה השם אלוהינו מלך העולם",
    ],
)
def test_negative_categories_are_unrelated(text):
    assert classify(text).klass == "unrelated"


def test_a_command_verb_is_not_manufactured_out_of_a_prefixed_noun():
    # "מפתח" (a key) must not strip to "פתח" (open!), nor "למקרר" (to the
    # fridge) to "קרר" (cool!). A ש- prefix on a real verb still counts.
    assert classify("שכחתי מפתח").klass == "unrelated"
    assert classify("שמתי את האוכל למקרר").klass == "unrelated"
    assert classify("יש מצב שתדליק את המזגן").klass == "request"


def test_unknown_text_falls_back_to_unrelated():
    assert classify("נושאורך").klass == "unrelated"
    assert classify("פרה אדומה בשדה הירוק").klass == "unrelated"


def test_classification_is_hashable_and_frozen():
    result = classify("חם פה")
    with pytest.raises(Exception):
        result.klass = "imperative"  # type: ignore[misc]

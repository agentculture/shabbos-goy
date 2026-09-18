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


# ------------------------------------------- operating a device is not a hint


@pytest.mark.parametrize(
    "text",
    [
        "מישהו ידליק את המזגן",
        "שמישהו יכבה את המזגן",
        "כדאי להדליק את המזגן",
        "צריך להדליק מזגן",
        "הלוואי שמישהו ידליק את המזגן",
        "אם מישהו היה מדליק את המזגן היה נחמד",
        "הייתי שמח אם המזגן היה דולק",
    ],
)
def test_impersonal_and_third_person_operation_is_a_request_not_a_hint(text):
    # Hebrew reaches for the third-person jussive and the impersonal modal
    # exactly to dodge a direct order. They are requests, and strict mode
    # refuses them like any other command.
    result = classify(text)
    assert result.klass == "request"
    assert may_act("strict", result.klass) is False


@pytest.mark.parametrize(
    "text",
    [
        "שמישהו יכבה את המזגן קר פה",
        "קר פה שמישהו יכבה את המזגן",
        "חם פה מישהו ידליק את המזגן",
        "מישהו ידליק את המזגן חם פה",
        "הלוואי שמישהו ידליק את המזגן חם פה",
        "חם פה הלוואי שמישהו ידליק את המזגן",
    ],
)
def test_a_state_word_does_not_launder_a_command_in_either_order(text):
    # The regression that blocked the merge: a command plus a state word acted
    # on the state. The command half wins whichever half came first.
    assert may_act("strict", classify(text).klass) is False


@pytest.mark.parametrize("text", ["המזגן חם פה", "קר לי מהמזגן", "המזגן של השכנים רועש"])
def test_merely_naming_a_controllable_device_is_never_a_hint(text):
    assert classify(text).klass == "unrelated"
    assert may_act("strict", classify(text).klass) is False


# ---------------------------------------------------------------- fragments


@pytest.mark.parametrize(
    "text", ["חם", "קר", "רועש", "חושך", "היה קר", "שהיה קר", "יותר חם", "קצת יותר קריר"]
)
def test_a_bare_or_continuing_fragment_never_acts(text):
    # "הלוואי שהיה ... קר" split on a pause leaves "קר", whose plain reading
    # is the opposite of the wish. A fragment is not a remark.
    result = classify(text)
    assert result.klass == "unrelated"
    assert may_act("strict", result.klass) is False


def test_the_same_words_with_an_anchor_are_a_remark_again():
    assert classify("קר פה").klass == "remark"
    assert classify("קר לי").klass == "remark"
    assert classify("איזה קור בבית").klass == "remark"


# ------------------------------------------- weekday intents for commands


@pytest.mark.parametrize(
    "text,klass,intent",
    [
        ("תדליק את המזגן", "imperative", "cool"),
        ("תכבה את המזגן", "imperative", "warm"),
        ("תנמיך את הווליום", "imperative", "quieter"),
        ("תגביר את הרדיו", "imperative", "louder"),
        ("אתה יכול להדליק את המזגן?", "request", "cool"),
        ("למה המזגן לא דלוק?", "rebuke", "cool"),
        ("המזגן דלוק?", "request", "status"),
        ("תדליק את האור", "imperative", "none"),
    ],
)
def test_weekday_mode_obeys_commands_with_the_intent_they_carry(text, klass, intent):
    result = classify(text)
    assert (result.klass, result.intent) == (klass, intent)
    # Weekday obeys every command class; strict refuses all of them.
    assert may_act("weekday", result.klass) is True
    assert may_act("strict", result.klass) is False


def test_a_command_outside_the_whitelist_carries_no_intent_to_execute():
    # Understood, classified, and nothing for the pipeline to do with it.
    assert classify("תדליק את האור").intent == "none"
    assert classify("תפתח את החלון").intent == "none"


@pytest.mark.parametrize(
    "text,intent",
    [
        ("מה עם הרדיו", "none"),  # a volume device named, nothing said to do
        ("כדאי לכוון את המזגן", "cool"),  # the AC, set, with nothing else said
        ("כדאי לפתוח", "none"),  # an operation with no device and no state
        ("מה עם המזגן חם פה", "cool"),  # device named: the state describes the room
        ("נו, חם פה נורא", "cool"),  # a complaint, not a wish: no inversion
        ("תעשה משהו", "none"),  # an order with nothing in it to execute
    ],
)
def test_a_command_with_too_little_said_carries_no_intent_it_cannot_infer(text, intent):
    result = classify(text)
    assert result.intent == intent
    assert may_act("strict", result.klass) is False

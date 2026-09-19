"""The rule cascade: normalised Hebrew in, a :class:`Classification` out.

The order below *is* the safety argument, so it is written out once here and
never duplicated elsewhere. Rules are tried in this order and the first match
wins:

===  ===========================  ==================================
#    rule                         why it comes where it does
===  ===========================  ==================================
1    liturgy / learning           quotes imperatives ("זכור את יום השבת")
2    reported speech              a quoted hint is not the speaker's
3    request frame                "למה שלא תדליק" is a request, not a rebuke
4    imperative verb              the direct command itself
5    rebuke frame                 an implied command ("למה המזגן לא דלוק")
6    status question              "המזגן דלוק" -- a request, intent status
6b   device-operation veto        operating a device is never a hint
6c   state that is not the room's  outdoors, a neighbour's, the soup's, a fever
6d   fragment                     one word, or the tail of a split sentence
7    wish frame + state           before the question rule: "מי ייתן ו..."
8    question / addressee         a question is never a hint
9    other tense                  yesterday's heat is not now's
10   no state word                nothing to infer
11   negation outside the state   "לא חם לי"
11b  unanchored state             "קר" with nothing tying it to here and now
12   discomfort marker + state    "קשה לי לישון בחום הזה"
13   state word                   the plain remark, the last rule
===  ===========================  ==================================

Anything that reaches the end unmatched is ``unrelated``: the cascade fails
closed, so a phrasing nobody anticipated costs a missed hint, never an
action. Only rules 7, 12 and 13 produce a class that
:func:`shabbos_goy.policy.may_act` lets act in strict mode.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..policy import CLASSES
from . import lexicon as lex
from .normalize import Normalized, normalize

#: Re-exported from the policy module so a caller that classifies and gates
#: in one breath has one import, and so the class names can never drift.
__all__ = ["CLASSES", "INTENTS", "Classification", "classify"]

#: The intents this agent can infer. ``none`` means "a hint was understood
#: but no whitelisted actuator serves it" (darkness, today) as well as "no
#: intent at all".
INTENTS: tuple[str, ...] = ("cool", "warm", "louder", "quieter", "status", "none")

#: Which intent a *state of the room* calls for.
_STATE_INTENT = {
    "hot": "cool",
    "cold": "warm",
    "loud": "quieter",
    "quiet": "louder",
    "dark": "none",
}

#: A wish inverts the polarity: someone wishing it were cold is telling us it
#: is hot now. (Confirmed product decision: a hot hint powers the AC on and a
#: cold hint powers it off.)
_WISH_INVERSION = {
    "cool": "warm",
    "warm": "cool",
    "louder": "quieter",
    "quieter": "louder",
    "status": "status",
    "none": "none",
}


@dataclass(frozen=True)
class Classification:
    """What the classifier says about one utterance.

    ``reason`` is a short ASCII rule id for logs. It never carries any of the
    transcript, because logs record the classified intent and the action
    taken -- not what was said (CLAUDE.md, "Privacy").
    """

    klass: str
    intent: str
    confidence: float
    reason: str


def _state(norm: Normalized) -> tuple[str, tuple[int, int]] | None:
    """The state of the room the utterance reports, with its token span.

    Loudness is looked for before quietness so "אי אפשר לשמוע מרוב רעש"
    ("can't hear over the noise") reads as noise, not as a volume that is
    too low.
    """
    span = norm.phrase_span(*lex.LOUD_PHRASES)
    if span:
        return "loud", span
    for kind, words in (("hot", lex.HOT), ("cold", lex.COLD), ("loud", lex.LOUD)):
        index = norm.token_index(*words)
        if index is not None:
            return kind, (index, index + 1)
    span = norm.phrase_span(*lex.QUIET_PHRASES)
    if span:
        return "quiet", span
    index = norm.token_index(*lex.DARK)
    if index is not None:
        return "dark", (index, index + 1)
    return None


def _negated_outside(norm: Normalized, span: tuple[int, int]) -> bool:
    """True if a negation word sits outside the matched state phrase.

    The span is excluded because some hint phrases *contain* the negation
    ("אני לא שומע כלום" -- "I can't hear a thing"), while a negation
    anywhere else flips the meaning of the state ("לא חם לי").
    """
    start, end = span
    for index, bucket in enumerate(norm.candidates):
        if start <= index < end:
            continue
        if any(word in bucket for word in lex.NEGATION):
            return True
    return False


#: A verb can only carry ש- ("יש מצב שתדליק") or ו- in front of it. Matching a
#: verb through the *other* prefixes manufactures commands that were never
#: spoken: "מפתח" (a key) would strip to "פתח" (open!), and "למקרר" (to the
#: fridge) to "קרר" (cool!). Harmless for safety -- a false imperative only
#: ever refuses -- but it mislabels an innocent sentence in the log.
_VERB_PREFIXES = frozenset("שו")


def _has_verb_token(norm: Normalized, words: frozenset[str]) -> bool:
    for token in norm.tokens:
        if token in words:
            return True
        if len(token) > 3 and token[0] in _VERB_PREFIXES and token[1:] in words:
            return True
    return False


def _has_verb(norm: Normalized) -> bool:
    return (
        _has_verb_token(norm, lex.IMPERATIVE_VERBS)
        or _has_verb_token(norm, lex.MISHEARD_IMPERATIVE_VERBS)
        or _has_verb_token(norm, lex.INFINITIVES)
    )


def _compile(patterns: tuple[str, ...]) -> tuple[re.Pattern[str], ...]:
    return tuple(re.compile(f"^{pattern}$") for pattern in patterns)


_OP_ON = _compile(lex.OP_ON_PATTERNS)
_OP_OFF = _compile(lex.OP_OFF_PATTERNS)
_OP_DOWN = _compile(lex.OP_DOWN_PATTERNS)
_OP_UP = _compile(lex.OP_UP_PATTERNS)
_OP_COOL = _compile(lex.OP_COOL_PATTERNS)
_OP_WARM = _compile(lex.OP_WARM_PATTERNS)
_OP_NEUTRAL = _compile(lex.OP_NEUTRAL_PATTERNS)
_ALL_OPS = _OP_ON + _OP_OFF + _OP_DOWN + _OP_UP + _OP_COOL + _OP_WARM + _OP_NEUTRAL


def _operates(norm: Normalized, patterns: tuple[re.Pattern[str], ...]) -> bool:
    """True if any token is a form of one of these device-operating verbs.

    A token that is a device *state* ("דלוק", "כבוי") is never an operating
    verb, however much it looks like one: the state is what the room is, the
    verb is what someone does to it.
    """
    for token in norm.tokens:
        if token in lex.OP_EXCEPTIONS or token in lex.STATE_ON or token in lex.STATE_OFF:
            continue
        if any(pattern.match(token) for pattern in patterns):
            return True
    return False


def _negated_before(norm: Normalized, index: int) -> bool:
    """True if a negation word sits immediately before token ``index``.

    Proximity matters: in "אני לא מבין למה המזגן כבוי" the negation belongs to
    "I don't understand", not to the AC being off.
    """
    for bucket in norm.candidates[max(0, index - 2) : index]:
        if any(word in bucket for word in lex.NEGATION):
            return True
    return False


def _volume_operation_intent(norm: Normalized) -> str:
    """The intent of a volume-device operation ("louder"/"quieter"/"none")."""
    if _operates(norm, _OP_UP) or _operates(norm, _OP_ON):
        return "louder"
    if _operates(norm, _OP_DOWN) or _operates(norm, _OP_OFF):
        return "quieter"
    return "none"


def _climate_operation_intent(norm: Normalized) -> str | None:
    """The intent of an explicit cool/warm operating verb, if any."""
    if _operates(norm, _OP_COOL):
        return "cool"
    if _operates(norm, _OP_WARM):
        return "warm"
    return None


def _climate_on_off_operation_intent(norm: Normalized) -> str | None:
    """The intent of an on/off (or, for climate, down/up) operating verb, if any."""
    if _operates(norm, _OP_ON) or _operates(norm, _OP_DOWN):
        return "cool"
    if _operates(norm, _OP_OFF) or _operates(norm, _OP_UP):
        return "warm"
    return None


def _named_state_intent(norm: Normalized, *, desire: bool) -> str | None:
    """The intent implied by a bare on/off device *state* word, if one is named.

    ``desire`` says how to read it: in a wish or an impersonal request the
    state named is the one the speaker wants, while in a rebuke it is the one
    they are complaining about, so the wanted state is its opposite -- unless
    the complaint negated it ("למה המזגן לא דלוק").
    """
    on_state = norm.token_index(*lex.STATE_ON)
    off_state = norm.token_index(*lex.STATE_OFF)
    if on_state is None and off_state is None:
        return None
    index = on_state if on_state is not None else off_state
    named_on = on_state is not None
    if _negated_before(norm, index):
        named_on = not named_on
    # A wish names the state it wants; a complaint names the state it is
    # stuck with, and wants the other one.
    wants_on = named_on if desire else not named_on
    return "cool" if wants_on else "warm"


def _state_remark_intent(norm: Normalized, *, climate: bool, desire: bool) -> str | None:
    """The intent implied by a plain state-of-the-room remark, if the utterance has one."""
    state = _state(norm)
    if state is None:
        return None
    plain = _STATE_INTENT[state[0]]
    if climate or not desire:
        # The device is named, so the state word describes the room being
        # complained about ("מה עם המזגן, חם פה") -- act on the state.
        return plain
    # Nothing named but the state itself: it is what is being asked for
    # ("אפשר קצת קריר פה"), which reads like a wish -- inverted.
    return _WISH_INVERSION[plain]


def _device_intent(norm: Normalized, *, desire: bool) -> str:
    """The intent a command-class utterance would execute on a weekday.

    Strict mode never reaches this as an action -- :func:`may_act` refuses the
    class -- but weekday mode obeys direct commands, so the class has to carry
    what the pipeline would do.

    ``desire`` says how to read a bare device *state*: in a wish or an
    impersonal request ("הייתי שמח אם המזגן היה דולק") the state named is the
    one the speaker wants, while in a rebuke ("למה המזגן כבוי") it is the one
    they are complaining about, so the wanted state is its opposite -- unless
    the complaint negated it ("למה המזגן לא דלוק").
    """
    climate = norm.has_token(*lex.CLIMATE_DEVICES)
    volume = norm.has_token(*lex.VOLUME_DEVICES, *lex.NOISE_CONTEXT)
    other = norm.has_token(*lex.OTHER_DEVICES)

    if volume and not climate:
        return _volume_operation_intent(norm)

    climate_intent = _climate_operation_intent(norm)
    if climate_intent is not None:
        return climate_intent

    if other and not climate:
        return "none"  # a light, a shutter, a door: understood, not whitelisted

    on_off_intent = _climate_on_off_operation_intent(norm)
    if on_off_intent is not None:
        return on_off_intent

    named_state_intent = _named_state_intent(norm, desire=desire)
    if named_state_intent is not None:
        return named_state_intent

    remark_intent = _state_remark_intent(norm, climate=climate, desire=desire)
    if remark_intent is not None:
        return remark_intent

    if climate:
        return "cool"  # setting the AC, with nothing else said, is cooling
    return "none"


def _is_anchored(norm: Normalized) -> bool:
    """True if a state word is anchored to here and now by something else.

    Without an anchor the utterance is a fragment, not a remark -- and the
    stray half of a pause-split sentence can mean the opposite of the whole
    ("הלוואי שהיה ... קר" -> "קר").
    """
    return (
        norm.has_token(*lex.LOCATIVE_ANCHORS, *lex.EXPERIENCER_ANCHORS, *lex.INTENSIFIER_ANCHORS)
        or norm.has_phrase(*lex.INTENSIFIER_PHRASES)
        or norm.has_phrase(*lex.WISH_FRAMES)
        or norm.has_token(*lex.DISCOMFORT_MARKERS)
        or norm.has_phrase(*lex.DISCOMFORT_PHRASES)
    )


def _is_open_request(norm: Normalized) -> bool:
    index = norm.token_index(*lex.OPEN_REQUEST)
    if index is None:
        return False
    if index > 0 and any(word in norm.candidates[index - 1] for word in lex.NOT_POSSIBLE):
        return False  # "אי אפשר ..." is a complaint, not a request
    return True


def _is_question(norm: Normalized) -> bool:
    if norm.has_question_mark:
        return True
    if norm.tokens and any(word in norm.candidates[0] for word in lex.INTERROGATIVE_OPENERS):
        return True
    return norm.has_token(*lex.ADDRESSEES)


def _rule_request_frame(
    norm: Normalized, *, verb: bool, device: bool, state: tuple[str, tuple[int, int]] | None
) -> Classification | None:
    """3. Request frames ("אתה יכול...", "אפשר...", "למה שלא...")."""
    framed = norm.has_phrase(*lex.REQUEST_FRAMES)
    if (framed and verb) or (_is_open_request(norm) and (verb or device or state)):
        return Classification("request", _device_intent(norm, desire=True), 0.85, "request-frame")
    return None


def _rule_imperative(norm: Normalized) -> Classification | None:
    """4. A direct imperative, including plausible ASR mis-hearings of one."""
    if _has_verb_token(norm, lex.IMPERATIVE_VERBS):
        return Classification(
            "imperative", _device_intent(norm, desire=True), 0.95, "imperative-verb"
        )
    if _has_verb_token(norm, lex.MISHEARD_IMPERATIVE_VERBS):
        return Classification(
            "imperative", _device_intent(norm, desire=True), 0.6, "imperative-verb-asr-variant"
        )
    if norm.has_token(*lex.POLITE_COMMAND) and _has_verb_token(norm, lex.INFINITIVES):
        return Classification(
            "imperative", _device_intent(norm, desire=True), 0.85, "polite-infinitive"
        )
    return None


def _rule_rebuke(norm: Normalized) -> Classification | None:
    """5. A rebuke: an implied command dressed as a complaint or a question."""
    interrogative = norm.has_token(*lex.REBUKE_INTERROGATIVES) or norm.has_phrase(
        *lex.REBUKE_INTERROGATIVE_PHRASES
    )
    complaint = norm.has_token(*lex.NEGATION) or norm.has_token(*lex.DEVICE_STATES)
    opener = bool(norm.tokens) and any(word in norm.candidates[0] for word in lex.REBUKE_OPENERS)
    if (interrogative and complaint) or norm.has_phrase(*lex.REBUKE_FRAMES) or opener:
        return Classification("rebuke", _device_intent(norm, desire=False), 0.8, "rebuke-frame")
    return None


def _rule_status_question(norm: Normalized, *, device: bool) -> Classification | None:
    """6. A status question is a request -- answered, never acted on."""
    if _is_question(norm) and (
        (device and norm.has_token(*lex.DEVICE_STATES)) or norm.has_phrase(*lex.STATUS_PHRASES)
    ):
        return Classification("request", "status", 0.8, "status-question")
    return None


def _rule_device_operation_veto(norm: Normalized) -> Classification | None:
    """6b. The device-operation veto. An utterance about operating a device --
    third-person jussive ("שמישהו יכבה את המזגן"), impersonal modal ("כדאי
    להדליק את המזגן"), or merely naming a controllable device -- is never a
    hint, whatever state word follows it. Impersonal and wishful framings are
    requests; anything else is dropped, because in doubt the agent does
    nothing."""
    if not (
        _operates(norm, _ALL_OPS)
        or norm.has_token(*lex.CLIMATE_DEVICES, *lex.VOLUME_DEVICES, *lex.OTHER_DEVICES)
    ):
        return None
    if norm.has_token(*lex.IMPERSONAL_FRAMES) or norm.has_phrase(
        *lex.IMPERSONAL_FRAMES, *lex.WISH_FRAMES
    ):
        return Classification(
            "request", _device_intent(norm, desire=True), 0.75, "impersonal-operation"
        )
    return Classification("unrelated", "none", 0.7, "device-operation")


def _rule_state_not_this_room(
    norm: Normalized, *, state: tuple[str, tuple[int, int]] | None
) -> Classification | None:
    """6c. The state is not this room's: outdoors, a neighbour's, the soup's,
    or a person's fever."""
    if state and (
        norm.has_token(*lex.ELSEWHERE)
        or norm.has_token(*lex.FOOD, *lex.FEVER)
        or norm.has_phrase(*lex.FEVER_PHRASES)
    ):
        return Classification("unrelated", "none", 0.8, "state-not-this-room")
    return None


def _rule_fragment(norm: Normalized) -> Classification | None:
    """6d. A fragment: a single word, or an utterance that opens like the tail
    of a longer one. The ASR splits sentences on a pause, and half a sentence
    can mean the opposite of the whole."""
    if len(norm.tokens) < 2 or any(word in norm.candidates[0] for word in lex.FRAGMENT_OPENERS):
        return Classification("unrelated", "none", 0.7, "fragment")
    return None


def _rule_wish(
    norm: Normalized, *, state: tuple[str, tuple[int, int]] | None
) -> Classification | None:
    """7. A wish, before the question rule: "מי ייתן ..." opens with what
    would otherwise read as an interrogative."""
    if state and norm.has_phrase(*lex.WISH_FRAMES) and _is_anchored(norm):
        if _negated_outside(norm, state[1]):
            return Classification("unrelated", "none", 0.8, "negated-wish")
        intent = _WISH_INVERSION[_STATE_INTENT[state[0]]]
        return Classification("wish", intent, 0.8, f"wish-{state[0]}")
    return None


def _rule_question(norm: Normalized) -> Classification | None:
    """8. A question, or speech addressed to a person in the room."""
    if _is_question(norm):
        return Classification("unrelated", "none", 0.7, "question-to-person")
    return None


def _rule_other_tense(norm: Normalized) -> Classification | None:
    """9. Another tense: yesterday's heat, or tomorrow's forecast."""
    if norm.has_token(*lex.TENSE):
        return Classification("unrelated", "none", 0.75, "other-tense")
    return None


def _classify_remark(
    norm: Normalized, *, state: tuple[str, tuple[int, int]] | None
) -> Classification:
    """10-13. Nothing left but the state of the room itself, or its absence."""
    # 10. Nothing about the state of the room.
    if state is None:
        return Classification("unrelated", "none", 0.2, "no-state")

    # 11. The state is negated: "לא חם לי".
    if _negated_outside(norm, state[1]):
        return Classification("unrelated", "none", 0.8, "negated-state")

    # 11b. A state word with nothing anchoring it to here and now is a
    #      fragment, not a remark.
    if not _is_anchored(norm):
        return Classification("unrelated", "none", 0.7, "unanchored-state")

    intent = _STATE_INTENT[state[0]]

    # 12. Discomfort: a state plus someone struggling with it.
    if norm.has_token(*lex.DISCOMFORT_MARKERS) or norm.has_phrase(*lex.DISCOMFORT_PHRASES):
        return Classification("discomfort", intent, 0.75, f"discomfort-{state[0]}")

    # 13. A plain remark about the state of the room.
    return Classification("remark", intent, 0.7, f"remark-{state[0]}")


def classify(text: str | None) -> Classification:
    """Classify one utterance. Never raises; in doubt, returns ``unrelated``.

    Rules 3-9 are tried in the fixed order documented in this module's
    docstring; the first match wins.
    """
    norm = normalize(text)
    if not norm.tokens:
        return Classification("unrelated", "none", 1.0, "empty")

    # 1. Learning / davening out loud.
    if norm.has_phrase(*lex.LITURGY):
        return Classification("unrelated", "none", 0.9, "liturgy")

    # 2. Reported speech: someone quoting someone else.
    if norm.has_token(*lex.REPORTED):
        return Classification("unrelated", "none", 0.85, "reported-speech")

    verb = _has_verb(norm)
    device = norm.has_token(*lex.DEVICES)
    state = _state(norm)

    rules = (
        lambda: _rule_request_frame(norm, verb=verb, device=device, state=state),
        lambda: _rule_imperative(norm),
        lambda: _rule_rebuke(norm),
        lambda: _rule_status_question(norm, device=device),
        lambda: _rule_device_operation_veto(norm),
        lambda: _rule_state_not_this_room(norm, state=state),
        lambda: _rule_fragment(norm),
        lambda: _rule_wish(norm, state=state),
        lambda: _rule_question(norm),
        lambda: _rule_other_tense(norm),
    )
    for rule in rules:
        verdict = rule()
        if verdict is not None:
            return verdict

    return _classify_remark(norm, state=state)

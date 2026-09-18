"""The Hebrew lexicon the rules match against.

Every entry is written in ordinary spelling and folded through
:func:`shabbos_goy.classifier.normalize.normalize` at import time, so the
lists here stay readable while the matcher only ever sees normalised forms
(no niqqud, final letters folded).

The lexicon is deliberately **data**, separate from the rule order in
``rules.py``: a community that speaks differently can widen the hint side
without touching the safety logic, and the command side can be widened
without any risk at all -- more command words can only ever mean *fewer*
actions, never more.
"""

from __future__ import annotations

from .normalize import normalize


def _fold(*items: str) -> frozenset[str]:
    return frozenset(normalize(item).text for item in items)


# --------------------------------------------------------------- liturgy
# Learning and davening out loud is not speech addressed to anyone. Checked
# first, because liturgy quotes imperatives ("זכור את יום השבת לקדשו") and
# even the word "to light" ("וציוונו להדליק נר של שבת").
LITURGY = _fold(
    "ברוך אתה",
    "ברוכה את",
    "אלוהינו",
    "אלהינו",
    "מלך העולם",
    "אשר קדשנו",
    "במצוותיו",
    "וציוונו",
    "שמע ישראל",
    "והיה אם שמוע",
    "אמר רבי",
    "אמר רב",
    "תנו רבנן",
    "משנה ברורה",
    "מה נשתנה",
    "לכה דודי",
    "ויכולו",
    "מזמור",
    "הללויה",
    "יתגדל ויתקדש",
    "ברכי נפשי",
    "זכור את יום השבת",
    "לקדשו",
    "שומר שבת",
)

# ------------------------------------------------------- reported speech
# Someone quoting someone else is not the speaker's own hint.
REPORTED = _fold(
    "אמר",
    "אמרה",
    "אמרו",
    "אמרתי",
    "אמרת",
    "אמרתם",
    "אומרת",
    "אומרים",
    "סיפר",
    "סיפרה",
    "סיפרו",
    "שמעתי",
    "כתוב",
    "ביקש",
    "ביקשה",
    "ביקשו",
    "טען",
    "טענה",
    "הודיע",
    "התלונן",
    "התלוננה",
)

# ------------------------------------------------------------- commands
IMPERATIVE_VERBS = _fold(
    "תדליק",
    "תדליקי",
    "תדליקו",
    "הדלק",
    "הדליקי",
    "הדליקו",
    "תכבה",
    "תכבי",
    "תכבו",
    "כבה",
    "כבי",
    "כבו",
    "תפתח",
    "תפתחי",
    "פתח",
    "פתחי",
    "תסגור",
    "תסגרי",
    "סגור",
    "תוריד",
    "תורידי",
    "הורד",
    "תעלה",
    "תעלי",
    "העלה",
    "תגביר",
    "תגבירי",
    "הגבר",
    "תנמיך",
    "תנמיכי",
    "הנמך",
    "תשים",
    "שים",
    "שימי",
    "הפעל",
    "תפעיל",
    "תפעילי",
    "תעשה",
    "עשה",
    "תקרר",
    "קרר",
    "תחמם",
    "חמם",
    "תכוון",
    "כוון",
    "תעצור",
    "עצור",
    "תוציא",
    "הוצא",
    "תביא",
)

# Plausible ASR mis-hearings of the command verbs above. lobes' Whisper
# fine-tune confuses similar consonants, and a command that came through
# damaged is still a command: listing them here means such an utterance is
# *labelled* an imperative rather than falling through to "unrelated". Both
# outcomes refuse to act; this one is honest in the log.
MISHEARD_IMPERATIVE_VERBS = _fold(
    "תדליג",
    "תדליך",
    "תדלית",
    "טדליק",
    "תכבא",
    "תכבע",
    "הדלג",
    "טכבה",
    "כבא",
)

INFINITIVES = _fold(
    "להדליק",
    "לכבות",
    "לפתוח",
    "לסגור",
    "להוריד",
    "להעלות",
    "להגביר",
    "להנמיך",
    "לשים",
    "להפעיל",
    "לקרר",
    "לחמם",
    "לכוון",
)

#: "נא להדליק" -- a polite infinitive is still a direct command.
POLITE_COMMAND = _fold("נא")

#: Request frames. Each needs a verb (imperative or infinitive) alongside it.
REQUEST_FRAMES = _fold(
    "אתה יכול",
    "את יכולה",
    "אתם יכולים",
    "תוכל",
    "תוכלי",
    "תוכלו",
    "יש מצב",
    "בא לך",
    "אכפת לך",
    "מה דעתך",
    "אתה מוכן",
    "את מוכנה",
    "למה שלא",
    "אולי",
    "אין סיכוי ש",
)

#: "אפשר" opens a request on its own (with a verb, a device or a state), but
#: only when it is not the "אי אפשר" of a complaint ("אי אפשר לנשום מהחום").
OPEN_REQUEST = _fold("אפשר")
NOT_POSSIBLE = _fold("אי")

# --------------------------------------------------------------- rebukes
REBUKE_INTERROGATIVES = _fold("למה", "מדוע", "איך", "מתי")
REBUKE_INTERROGATIVE_PHRASES = _fold("כמה זמן", "עד מתי")
REBUKE_FRAMES = _fold(
    "אף אחד לא",
    "שכח",
    "שכחה",
    "שכחו",
    "שוכחים",
    "שוב פעם",
    "עדיין לא",
    "מה קרה",
    "כבר שעה",
)
#: A single "נו" opening an utterance is a complaint, not a remark.
REBUKE_OPENERS = _fold("נו")

# --------------------------------------------------------------- devices
DEVICES = _fold(
    "מזגן",
    "אור",
    "אורות",
    "מאוורר",
    "רדיו",
    "מוזיקה",
    "טמפרטורה",
    "תריס",
    "חלון",
    "מנורה",
    "דוד",
    "רמקול",
)
DEVICE_STATES = _fold("דלוק", "דלוקה", "כבוי", "כבויה", "עובד", "עובדת", "פועל", "פועלת", "מכובה")
STATUS_PHRASES = _fold("מה הטמפרטורה", "כמה מעלות", "מה מצב", "כמה מעלות יש")

# -------------------------------------------------------------- negation
NEGATION = _fold("לא", "ולא", "שלא", "אין", "ואין", "אינו", "אינה", "אינני", "בכלל לא")

# ----------------------------------------------------- questions to people
INTERROGATIVE_OPENERS = _fold(
    "למה", "מדוע", "איך", "מתי", "כמה", "מי", "מה", "איפה", "האם", "לאן", "מניין"
)
#: Second-person addressing: the speaker is talking to a person in the room.
ADDRESSEES = _fold("אתה", "אתם", "אתן", "לך", "לכם", "לכן", "אצלך", "שלך", "שלכם", "אתכם", "תגיד")

# ------------------------------------------------------------ other tense
TENSE = _fold(
    "היה",
    "הייתה",
    "היתה",
    "היו",
    "היינו",
    "יהיה",
    "תהיה",
    "יהיו",
    "אתמול",
    "מחר",
    "אמש",
    "שלשום",
    "פעם",
    "בקיץ",
    "בחורף",
    "צפוי",
    "צפויה",
    "שעבר",
    "שעברה",
    "בעבר",
)

# ------------------------------------------------------------------ hints
WISH_FRAMES = _fold("הלוואי", "הלואי", "מי ייתן", "מי יתן", "כמה הייתי רוצה", "הייתי רוצה", "אם רק")

DISCOMFORT_MARKERS = _fold("מתקשה", "סובל", "סובלת", "נחנק", "נחנקת", "מתייסר")
DISCOMFORT_PHRASES = _fold(
    "קשה לי",
    "קשה לנו",
    "קשה לקרוא",
    "קשה לישון",
    "קשה לשמוע",
    "אי אפשר לנשום",
    "אי אפשר לשמוע",
    "אי אפשר לישון",
)

HOT = _fold("חם", "חום", "לוהט", "לוהטת", "שרב", "מחניק", "מחניקה", "חמסין", "מזיע", "מזיעה", "נמס")
COLD = _fold(
    "קר",
    "קור",
    "קריר",
    "קרירה",
    "קפוא",
    "קפואה",
    "קפואות",
    "קופא",
    "קופאת",
    "צינה",
    "רועד",
    "רועדת",
)
LOUD = _fold("רועש", "רועשת", "רעש", "צעקות", "מחריש", "רעשן")
LOUD_PHRASES = _fold("חזק מדי", "מרוב רעש")
QUIET_PHRASES = _fold(
    "לא שומע",
    "לא שומעת",
    "לא שומעים",
    "קשה לשמוע",
    "אי אפשר לשמוע",
    "חלש מדי",
    "שקט מדי",
    "בקושי שומע",
)
# ------------------------------------------------- somewhere that is not here
# A state that belongs to the street, the yard or the neighbours is not a hint
# about this room: "הקור בחוץ נורא היום" is small talk about the weather, and
# "המזגן של השכנים רועש" is someone else's noise. "בבית" is deliberately NOT
# here -- "חם נורא בבית" is exactly the hint this agent exists for.
ELSEWHERE = _fold(
    "בחוץ",
    "בחצר",
    "ברחוב",
    "בכביש",
    "בגינה",
    "במרפסת",
    "שכן",
    "שכנה",
    "שכנים",
    "השכן",
    "השכנים",
    "אצלם",
    "אצלהם",
)

# Food, drink and the vessels they come in. A Shabbat table is full of hot
# and cold things that have nothing to do with the room: "המרק קר" is about
# the soup, not the thermostat.
FOOD = _fold(
    "מרק",
    "חמין",
    "צולנט",
    "אוכל",
    "ארוחה",
    "מאכל",
    "לחם",
    "חלה",
    "קפה",
    "תה",
    "יין",
    "מיץ",
    "חלב",
    "מים",
    "דג",
    "בשר",
    "אורז",
    "סלט",
    "עוגה",
    "קוגל",
    "דייסה",
    "צלחת",
    "כוס",
    "בקבוק",
    "סיר",
    "קדרה",
    "קומקום",
    "מקרר",
    "תנור",
    "פלטה",
)

# A fever is a person's temperature, not the room's.
FEVER_PHRASES = _fold("לי חום", "לו חום", "לה חום", "להם חום", "יש חום", "חום גבוה", "חום של")
FEVER = _fold("מדחום")

DARK = _fold("חושך", "חשוך", "חשוכה", "אפלה")


# ------------------------------------------------- operating a device at all
# A hint is a remark about the STATE of the room or of the speaker. The moment
# an utterance is about *operating* a device -- in any person, including the
# third-person jussive Hebrew uses to dodge a direct order ("שמישהו יכבה את
# המזגן") and the impersonal modal ("כדאי להדליק") -- it stops being a hint,
# whatever else it contains.
#
# These are regular expressions, matched against a whole token (after the
# normaliser folded its final letters) with the usual one-letter prefixes
# allowed in front, so an unseen conjugation of a known root still matches.
# Over-matching here is free: it can only ever *refuse* to treat something as
# a hint.
_PREFIX_PATTERN = r"[שוהבלכמ]{0,2}"
_PERSON_PATTERN = r"[ילתנאמ]?"

OP_ON_PATTERNS = (
    rf"{_PREFIX_PATTERN}{_PERSON_PATTERN}ה?דל[יו]?ק\w*",  # הדליק ידליק תדליק מדליק להדליק הדלקה
    rf"{_PREFIX_PATTERN}{_PERSON_PATTERN}ה?פעי?ל\w*",  # הפעיל יפעיל מפעיל להפעיל הפעלה
)
OP_OFF_PATTERNS = (
    rf"{_PREFIX_PATTERN}{_PERSON_PATTERN}כב(?:ה|ות|ו(?!ד)|י)\w*",  # כבה יכבה לכבות מכבה כבו
    rf"{_PREFIX_PATTERN}כיבוי\w*",
)
OP_DOWN_PATTERNS = (
    rf"{_PREFIX_PATTERN}{_PERSON_PATTERN}ה?ורי?ד\w*",  # הוריד יוריד תוריד מוריד להוריד
    rf"{_PREFIX_PATTERN}{_PERSON_PATTERN}ה?נמי?כ\w*",  # הנמיך ינמיך תנמיך להנמיך
    rf"{_PREFIX_PATTERN}(?:[ילתנאמ]ה?|ה)חלי?ש\w*",  # להחליש יחליש (not the adjective חלש)
    rf"{_PREFIX_PATTERN}{_PERSON_PATTERN}ה?שתי?ק\w*",  # להשתיק ישתיק תשתיק
)
OP_UP_PATTERNS = (
    rf"{_PREFIX_PATTERN}{_PERSON_PATTERN}ה?על[הות]\w*",  # העלה יעלה תעלה להעלות
    rf"{_PREFIX_PATTERN}{_PERSON_PATTERN}ה?גבי?ר\w*",  # הגביר יגביר תגביר להגביר
)
OP_COOL_PATTERNS = (rf"{_PREFIX_PATTERN}{_PERSON_PATTERN}קרר\w*", rf"{_PREFIX_PATTERN}קירור\w*")
OP_WARM_PATTERNS = (rf"{_PREFIX_PATTERN}{_PERSON_PATTERN}חממ\w*", rf"{_PREFIX_PATTERN}חימומ\w*")
OP_NEUTRAL_PATTERNS = (
    rf"{_PREFIX_PATTERN}{_PERSON_PATTERN}פתח\w*",  # פתח יפתח תפתח לפתוח
    rf"{_PREFIX_PATTERN}{_PERSON_PATTERN}סג[ורי]\w*",  # סגור יסגור לסגור תסגרי
    rf"{_PREFIX_PATTERN}{_PERSON_PATTERN}סוגר\w*",
    rf"{_PREFIX_PATTERN}{_PERSON_PATTERN}שים\w*",  # שים ישים תשים לשים
    rf"{_PREFIX_PATTERN}{_PERSON_PATTERN}כוו?נ\w*",  # כוון יכוון לכוון
)

#: Tokens that look like an operating verb but are not one. Kept short and
#: explicit: each one is a word this agent will really hear on Shabbat.
OP_EXCEPTIONS = _fold("מפתח", "מפתחות", "כבוד", "כבודו", "מקרר", "מעלות", "גבר")

#: Devices this agent can actually operate, by what the intent means for them.
CLIMATE_DEVICES = _fold("מזגן", "מיזוג", "טמפרטורה", "מזגנים")
VOLUME_DEVICES = _fold("רדיו", "מוזיקה", "ווליום", "וולום", "עוצמה", "רמקול", "טלוויזיה")
#: Noise words. They set the volume CONTEXT for reading a command's intent
#: ("תכבה את הרעש הזה" -> quieter) but they are not devices, so they never
#: trigger the device veto: "איזה רעש" is a plain hint.
NOISE_CONTEXT = _fold("רעש", "רועש", "רועשת", "צעקות", "רעשן")
#: Controllable, but outside the MVP whitelist: a command about these is
#: understood and carries no intent to execute.
OTHER_DEVICES = _fold(
    "אור", "אורות", "מנורה", "תריס", "חלון", "דלת", "פלטה", "דוד", "מאוורר", "בוילר"
)

#: The state a device is in, as opposed to a verb that changes it.
STATE_ON = _fold("דלוק", "דלוקה", "דולק", "דולקת", "עובד", "עובדת", "פועל", "פועלת")
STATE_OFF = _fold("כבוי", "כבויה", "מכובה", "כבתה")

#: Impersonal and modal frames: nobody is ordered, but a device is to be
#: operated. "כדאי להדליק את המזגן" is a request, not a remark about heat.
IMPERSONAL_FRAMES = _fold(
    "כדאי",
    "צריך",
    "צריכים",
    "מישהו",
    "שווה",
    "נחוץ",
    "רצוי",
    "הייתי שמח",
    "הייתי שמחה",
    "היינו שמחים",
    "נחמד היה",
    "טוב היה",
    "מה עם",
)

# --------------------------------------------------- a complete remark
# A bare adjective is what a pause-split sentence leaves behind: "הלוואי שהיה
# ... קר" can arrive as the single word "קר", whose plain reading ("it is
# cold") is the OPPOSITE of the wish. A state word therefore only counts as a
# remark when something anchors it to here and now.
LOCATIVE_ANCHORS = _fold(
    "פה",
    "כאן",
    "בבית",
    "בחדר",
    "בסלון",
    "במטבח",
    "בסלון",
    "בחדרים",
    "בדירה",
    "באוויר",
    "בחדר שינה",
)
EXPERIENCER_ANCHORS = _fold(
    "לי",
    "לנו",
    "לו",
    "לה",
    "להם",
    "לילד",
    "לילדה",
    "לילדים",
    "לתינוק",
    "לאמא",
    "לאבא",
    "לסבתא",
    "לסבא",
    "אני",
    "אנחנו",
    "הילד",
    "הילדה",
    "הילדים",
    "התינוק",
    "הידיים",
    "שלי",
    "שלנו",
)
INTENSIFIER_ANCHORS = _fold("איזה", "ממש", "נורא", "מאוד", "מדי", "בטירוף", "לגמרי", "כבד")
INTENSIFIER_PHRASES = _fold("כל כך", "לא נורמלי")

#: An utterance that opens like the tail of a longer one. Whatever follows,
#: the beginning is missing, so it is not a complete remark.
FRAGMENT_OPENERS = _fold("היה", "שהיה", "שיהיה", "יותר", "קצת", "וגם", "אבל", "גם", "או", "כי")

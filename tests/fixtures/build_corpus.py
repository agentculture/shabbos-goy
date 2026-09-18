"""Build ``tests/fixtures/corpus.jsonl``.

The JSONL is the committed artefact -- the tests read it, not this script --
but the corpus is easier to review and extend as Python lists, so the source
lives here beside it. Regenerate with::

    python3 tests/fixtures/build_corpus.py

Labels are written by hand and are the *specification*: the classifier is
expected to meet them, never the other way round. A fixture is only ever
relabelled when the label itself was wrong, and hint fixtures the rules miss
are deliberately kept so the recall number stays honest.
"""

from __future__ import annotations

import json
from pathlib import Path

ROWS: list[dict] = []


def add(fid, text, category, subcategory, expect_act, klass=None, intent=None, note=None):
    row = {
        "id": fid,
        "text": text,
        "category": category,
        "subcategory": subcategory,
        "expect_act_strict": expect_act,
    }
    if klass is not None:
        row["expect_class"] = klass
    if intent is not None:
        row["expect_intent"] = intent
    if note is not None:
        row["note"] = note
    ROWS.append(row)


# ---------------------------------------------------------------- commands
# The intent is what WEEKDAY mode would execute (strict mode refuses the class
# outright). "none" means understood but outside the MVP whitelist.
IMPERATIVES = [
    ("תדליק את המזגן", "cool"),
    ("תכבה את המזגן", "warm"),
    ("הדלק את המזגן בבקשה", "cool"),
    ("כבה את האור", "none"),
    ("תדליקי את המזגן", "cool"),
    ("תכבי את האור בסלון", "none"),
    ("תפתח את החלון", "none"),
    ("תסגור את התריס", "none"),
    ("תוריד את הטמפרטורה בבקשה", "cool"),
    ("תעלה את הטמפרטורה לעשרים וחמש", "warm"),
    ("שים את המזגן על עשרים ושתיים", "cool"),
    ("תשים את המזגן על קירור", "cool"),
    ("הפעל את המאוורר", "none"),
    ("תפעיל את המזגן עכשיו", "cool"),
    ("נא להדליק את המזגן", "cool"),
    ("בבקשה תדליק את האור", "none"),
    ("תגביר את הרדיו", "louder"),
    ("תנמיך את המוזיקה", "quieter"),
    ("תכבה את הרעש הזה", "quieter"),
    ("תדליק לי את המזגן", "cool"),
    ("תוריד לעשרים וארבע", "cool"),
    ("קרר את החדר", "cool"),
    ("תחמם קצת את החדר", "warm"),
    ("תכבה הכל", "warm"),
    ("עכשיו תדליק את המזגן", "cool"),
    ("תדליקו את המזגן בסלון", "cool"),
    ("שבוס גוי תדליק את המזגן", "cool"),
    ("תשים את המזגן על חימום", "warm"),
    ("תפתח את הדלת", "none"),
    ("תכבה את המזגן לפני שהולכים לישון", "warm"),
]
for i, (text, intent) in enumerate(IMPERATIVES, 1):
    add(f"c{i:02d}", text, "command", "imperative", False, "imperative", intent)

# Plausible ASR mis-hearings of command words. Whichever class these land in,
# they must never act. lobes' Whisper is documented to confuse similar
# consonants and to hallucinate "תודה רבה" on non-speech.
MISHEARD = [
    ("תדליג את המזגן", "ק/ג confusion of תדליק"),
    ("תדליכ את המזגן", "ק/כ confusion of תדליק"),
    ("תכבא את האור", "ה/א confusion of תכבה"),
    ("טדליק את המזגן", "ת/ט confusion"),
    ("תדליק את המזכן", "ג/כ confusion of המזגן"),
    ("תכבע את המזגן", "ה/ע confusion of תכבה"),
    ("הדלג את המזגן", "ק/ג confusion of הדלק"),
    ("תדליק את המזגן תודה רבה", "command plus the documented noise hallucination"),
]
for i, (text, note) in enumerate(MISHEARD, 1):
    add(f"c31_{i:02d}", text, "command", "imperative", False, None, None, note)

REQUESTS = [
    ("אתה יכול להדליק את המזגן?", "cool"),
    ("תוכל לכבות את האור?", "none"),
    ("אפשר להדליק את המזגן?", "cool"),
    ("אפשר קצת קריר פה?", "cool"),
    ("יש מצב שתדליק את המזגן?", "cool"),
    ("אולי תדליק את המזגן", "cool"),
    ("למה שלא תדליק את המזגן", "cool"),
    ("אכפת לך להדליק את המזגן?", "cool"),
    ("אתה מוכן לכבות את הרדיו?", "quieter"),
    ("תוכלי להנמיך את המוזיקה?", "quieter"),
    ("אפשר להוריד את הטמפרטורה?", "cool"),
    ("בא לך להדליק את המזגן?", "cool"),
    ("מה דעתך להדליק את המזגן?", "cool"),
    ("אפשר בבקשה להדליק את האור?", "none"),
    ("האם אתה יכול לקרר את החדר?", "cool"),
    ("אתה יכול לשים את המזגן על עשרים וארבע?", "cool"),
]
for i, (text, intent) in enumerate(REQUESTS, 1):
    add(f"c40_{i:02d}", text, "command", "request", False, "request", intent)

# Impersonal, third-person and counterfactual framings: nobody is ordered, but
# a device is to be operated. Hebrew reaches for these precisely to dodge a
# direct command, which is exactly why they must not read as hints.
IMPERSONAL = [
    ("מישהו ידליק את המזגן", "cool"),
    ("שמישהו יכבה את המזגן", "warm"),
    ("כדאי להדליק את המזגן", "cool"),
    ("צריך להדליק מזגן", "cool"),
    ("שווה להדליק את המזגן", "cool"),
    ("הלוואי שמישהו ידליק את המזגן", "cool"),
    ("אם מישהו היה מדליק את המזגן היה נחמד", "cool"),
    ("הייתי שמח אם המזגן היה דולק", "cool"),
    ("מישהו יכול להנמיך את הרדיו", "quieter"),
    ("צריך לכבות את המזגן", "warm"),
]
for i, (text, intent) in enumerate(IMPERSONAL, 1):
    add(f"c45_{i:02d}", text, "command", "request", False, "request", intent)

STATUS = [
    "המזגן דלוק?",
    "האם המזגן עובד?",
    "מה הטמפרטורה בחדר?",
    "כמה מעלות יש בבית?",
    "המזגן פועל?",
]
for i, text in enumerate(STATUS, 1):
    add(f"c50_{i:02d}", text, "command", "request", False, "request", "status")

REBUKES = [
    ("למה המזגן לא דלוק?", "cool"),
    ("למה האור כבוי?", "none"),
    ("כמה זמן המזגן כבוי?", "cool"),
    ("מישהו שכח לכבות את המזגן", "warm"),
    ("איך יכול להיות שהמזגן לא עובד", "cool"),
    ("למה אף אחד לא הדליק את המזגן", "cool"),
    ("תמיד שוכחים להדליק את המזגן", "cool"),
    ("שוב פעם המזגן כבוי", "cool"),
    ("המזגן עדיין לא דלוק", "cool"),
    ("אני לא מבין למה המזגן כבוי", "cool"),
    ("מה קרה למזגן שהוא לא עובד", "cool"),
    ("למה לא הדלקתם את המזגן לפני שבת", "cool"),
    ("נו, המזגן לא דלוק", "cool"),
    ("חבל שאף אחד לא הדליק את המזגן", "cool"),
    ("למה כל כך חם פה ואף אחד לא הדליק את המזגן", "cool"),
]
for i, (text, intent) in enumerate(REBUKES, 1):
    add(f"c60_{i:02d}", text, "command", "rebuke", False, "rebuke", intent)

# ------------------------------------------------------------------- hints
HINTS = [
    # (text, class, intent, subcategory, note)
    ("חם פה", "remark", "cool", "hot", None),
    ("חם לי", "remark", "cool", "hot", None),
    ("חם נורא בבית", "remark", "cool", "hot", None),
    ("איזה חום", "remark", "cool", "hot", None),
    ("לוהט פה", "remark", "cool", "hot", None),
    ("מחניק פה", "remark", "cool", "hot", None),
    ("חם לי מאוד", "remark", "cool", "hot", None),
    ("אני נמס פה", "remark", "cool", "hot", None),
    ("הלוואי שהיה קר", "wish", "cool", "hot", "wish for cold => currently hot"),
    ("הלוואי ש היה קר", "wish", "cool", "hot", "joiner output: detached ש prefix"),
    ("מי ייתן וקצת קריר", "wish", "cool", "hot", None),
    ("הלוואי שהיה קצת יותר קריר", "wish", "cool", "hot", None),
    ("כמה הייתי רוצה שיהיה קריר", "wish", "cool", "hot", None),
    ("אני מזיע", "remark", "cool", "hot", None),
    ("קשה לי לישון בחום הזה", "discomfort", "cool", "hot", None),
    ("הילד מתקשה להירדם מהחום", "discomfort", "cool", "hot", None),
    ("אי אפשר לנשום מהחום", "discomfort", "cool", "hot", None),
    ("אני סובל מהחום הזה", "discomfort", "cool", "hot", None),
    ("קר פה", "remark", "warm", "cold", None),
    ("קר לי", "remark", "warm", "cold", None),
    ("קפוא פה", "remark", "warm", "cold", None),
    ("איזה קור בבית", "remark", "warm", "cold", None),
    ("הלוואי שהיה חם", "wish", "warm", "cold", "wish for heat => currently cold"),
    ("הלוואי ש היה חם פה", "wish", "warm", "cold", "joiner output: detached ש prefix"),
    ("אני קופא", "remark", "warm", "cold", None),
    ("הידיים שלי קפואות", "remark", "warm", "cold", None),
    ("הילד רועד מקור", "remark", "warm", "cold", None),
    ("רועש פה", "remark", "quieter", "loud", None),
    ("איזה רעש", "remark", "quieter", "loud", None),
    ("הרדיו חזק מדי", "remark", "quieter", "loud", "names a device: vetoed"),
    ("אי אפשר לשמוע מרוב רעש", "discomfort", "quieter", "loud", None),
    ("אני לא שומע כלום", "remark", "louder", "quiet", "negation belongs to the hint phrase"),
    ("קשה לשמוע את הרדיו", "discomfort", "louder", "quiet", "names a device: vetoed"),
    ("חלש מדי", "remark", "louder", "quiet", None),
    ("חושך פה", "remark", "none", "dark", "no light actuator in the MVP whitelist"),
    ("קשה לקרוא בחושך", "discomfort", "none", "dark", "no light actuator in the MVP whitelist"),
    ("אני לא יכול לישון מרוב חום", "discomfort", "cool", "hot", "hard: negation guard drops it"),
    ("האוויר פה עומד", "remark", "cool", "hot", "hard: idiom, no state word"),
    ("הרגליים שלי כמו קרח", "remark", "warm", "cold", "hard: simile, no state word"),
    ("המאוורר כבר לא עוזר", "remark", "cool", "hot", "hard: implied heat only"),
]
for i, (text, klass, intent, sub, note) in enumerate(HINTS, 1):
    add(f"h{i:02d}", text, "hint", sub, True, klass, intent, note)

# --------------------------------------------------------------- negatives
NEGATIVE = {
    "negation": [
        "לא חם לי",
        "זה ממש לא חם פה",
        "אין לי חם",
        "לא קר לי בכלל",
        "אני לא מזיע",
        "לא רועש פה בכלל",
        "זה לא מפריע לי החום",
        "לא צריך מזגן עכשיו",
        "אף אחד פה לא סובל מהחום",
        "זה לא באמת חם פה",
    ],
    "question_to_person": [
        "חם לך?",
        "קר לך אמא?",
        "רועש לכם?",
        "אתם לא מזיעים?",
        "נעים לך פה?",
        "קשה לך לקרוא?",
        "למה אתה מזיע?",
        "תגיד, חם לך?",
        "מישהו רוצה מים קרים?",
        "איך אתם מסתדרים עם החום הזה?",
    ],
    "reported_speech": [
        "אמא אמרה שחם לה",
        "הוא אמר הלוואי שהיה קר",
        "סבתא אמרה תדליק את המזגן",
        "כתוב בעיתון שיהיה חם מאוד",
        "הם סיפרו שקר להם בבית",
        "שמעתי אותה אומרת שחם פה",
        "הרב אמר שאסור להדליק מזגן בשבת",
        "הילד אמר שקר לו",
        "היא ביקשה שנדליק את האור",
        "הוא אמר לי אתמול שחם לו",
    ],
    "other_tense": [
        "היה חם אתמול",
        "יהיה חם מחר",
        "בקיץ תמיד חם פה",
        "כשהיינו בים היה נורא חם",
        "מחר צפוי חום כבד",
        "אמש היה קר מאוד",
        "בשבוע שעבר היה קר בבית",
        "יהיה קריר בערב",
        "פעם היה פה חם יותר",
        "בשנה שעברה היה חום כבד בסוכות",
    ],
    "third_party_audio": [
        "ועכשיו נעבור לפרסומות",
        "קנו עכשיו מזגן במבצע מיוחד",
        "מזג האוויר למחר חם וצח",
        "בחדשות הערב שריפה גדולה",
        "וזה היה השיר שלנו לשבת",
        "גול איזה גול מדהים",
        "הקהל מריע ביציע",
        "שלום לכולם וברוכים הבאים לתוכנית",
        "המשך יום נעים ותודה שהאזנתם",
        "בפרק הקודם ראינו את הגיבור בורח",
    ],
    "liturgy": [
        "ברוך אתה השם אלוהינו מלך העולם",
        "שמע ישראל השם אלוהינו השם אחד",
        "והיה אם שמוע תשמעו אל מצוותי",
        "אמר רבי עקיבא זה כלל גדול בתורה",
        "תנו רבנן במה מדליקין",
        "מה נשתנה הלילה הזה מכל הלילות",
        "ויכולו השמים והארץ וכל צבאם",
        "לכה דודי לקראת כלה",
        "אשר קדשנו במצוותיו וציוונו להדליק נר של שבת",
        "זכור את יום השבת לקדשו",
    ],
    # A hint and a command in one breath, in BOTH orders. The command half
    # must win: a state word does not launder an utterance about operating a
    # device.
    "mixed_command_hint": [
        "שמישהו יכבה את המזגן קר פה",
        "קר פה שמישהו יכבה את המזגן",
        "חם פה מישהו ידליק את המזגן",
        "מישהו ידליק את המזגן חם פה",
        "כדאי להדליק את המזגן חם נורא",
        "חם נורא כדאי להדליק את המזגן",
        "צריך להדליק מזגן קשה לי לנשום",
        "קשה לי לנשום צריך להדליק מזגן",
        "הלוואי שמישהו ידליק את המזגן חם פה",
        "חם פה הלוואי שמישהו ידליק את המזגן",
        "המזגן חם פה",
        "קר לי מהמזגן",
    ],
    # What a pause-split sentence leaves behind. "הלוואי שהיה ... קר" arriving
    # as the bare word "קר" would read as the OPPOSITE of the wish.
    "fragment": [
        "חם",
        "קר",
        "רועש",
        "חושך",
        "קריר",
        "קפוא",
        "מחניק",
        "היה קר",
        "שהיה קר",
        "יותר חם",
        "קצת יותר קריר",
        "וקר",
        # Anchored, but still opening like the tail of a longer sentence:
        # only the fragment-opener rule catches these.
        "היה קר לי",
        "שהיה קר פה",
        "יותר חם פה",
        "קצת יותר קריר בבית",
        # Neither a single word nor an opener, but nothing ties the state to
        # here and now: only the anchor rule catches these.
        "חם ולח",
        "קר ורטוב",
        "קריר ונעים",
        "חושך מוחלט",
    ],
    "elsewhere": [
        "הקור בחוץ נורא היום",
        "המזגן של השכנים רועש",
        "חם בחוץ היום",
        "השכן מזיע כל הקיץ",
        "קר ברחוב",
        "רועש בחצר",
    ],
    "food_and_fever": [
        "המרק קר",
        "האוכל קר לגמרי",
        "החמין חם מדי",
        "הקפה קר לי",
        "יש לי חום",
        "לילד יש חום גבוה",
    ],
    "asr_noise": [
        "תודה רבה",
        "תודה רבה תודה",
        "אני לא שונה כלום",
        "נושאורך",
        "אהה",
        "שלום שלום",
    ],
}
for subcategory, texts in NEGATIVE.items():
    for i, text in enumerate(texts, 1):
        add(f"n_{subcategory}_{i:02d}", text, "negative", subcategory, False)


def main() -> None:
    seen = set()
    lines = []
    for row in ROWS:
        assert row["id"] not in seen, row["id"]
        seen.add(row["id"])
        lines.append(json.dumps(row, ensure_ascii=False))
    (Path(__file__).parent / "corpus.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

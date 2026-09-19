"""Table-driven safety metrics over ``tests/fixtures/corpus.jsonl``.

These are the numbers the product is judged on (CLAUDE.md, invariant #4):

* strict mode must act on **zero** command fixtures (imperative / request /
  rebuke) and **zero** negative fixtures (negation, question to a person,
  reported speech, other tense, third-party audio, learning/davening text,
  ASR noise). A single false positive here breaks Shabbat for the user.
* hint recall on typed text must be at least 70 percent. A missed hint is
  cheap, so this bound is deliberately loose and the misses are printed.

Every "would it act" decision goes through :func:`shabbos_goy.policy.may_act`;
this module never re-implements the mode x class table.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from shabbos_goy.classifier import CLASSES, INTENTS, classify
from shabbos_goy.policy import may_act

CORPUS_PATH = Path(__file__).parent / "fixtures" / "corpus.jsonl"
PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "shabbos_goy"

REQUIRED_NEGATIVE_CATEGORIES = (
    "negation",
    "question_to_person",
    "reported_speech",
    "other_tense",
    "third_party_audio",
    "liturgy",
)

HINT_RECALL_FLOOR = 0.70


def _load():
    with CORPUS_PATH.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


CORPUS = _load()
COMMANDS = [row for row in CORPUS if row["category"] == "command"]
HINTS = [row for row in CORPUS if row["category"] == "hint"]
NEGATIVES = [row for row in CORPUS if row["category"] == "negative"]


# ------------------------------------------------------------ corpus shape


def test_corpus_ids_are_unique_and_fields_are_valid():
    ids = [row["id"] for row in CORPUS]
    assert len(ids) == len(set(ids))
    for row in CORPUS:
        assert row["category"] in {"command", "hint", "negative"}
        assert isinstance(row["text"], str)
        assert row["text"].strip()
        assert isinstance(row["expect_act_strict"], bool)
        assert row.get("expect_class", "unrelated") in CLASSES
        assert row.get("expect_intent", "none") in INTENTS


def test_corpus_has_at_least_sixty_command_fixtures():
    assert len(COMMANDS) >= 60
    for subcategory in ("imperative", "request", "rebuke"):
        assert any(row["subcategory"] == subcategory for row in COMMANDS)


def test_corpus_has_hot_and_cold_hint_fixtures():
    subcategories = {row["subcategory"] for row in HINTS}
    assert {"hot", "cold"} <= subcategories


def test_corpus_has_at_least_ten_fixtures_in_each_negative_category():
    for category in REQUIRED_NEGATIVE_CATEGORIES:
        count = len([row for row in NEGATIVES if row["subcategory"] == category])
        assert count >= 10, f"{category}: {count}"


def test_corpus_includes_joiner_style_detached_prefix_variants():
    # The joiner joins halves with a space, so a detached ש/ה/ו/ב/ל/כ/מ is a
    # real input shape and must be represented in the corpus.
    detached = [
        row
        for row in CORPUS
        if any(word in {"ש", "ה", "ו", "ב", "ל", "כ", "מ"} for word in row["text"].split())
    ]
    assert len(detached) >= 2


def test_corpus_only_marks_hint_fixtures_as_acting():
    for row in CORPUS:
        if row["expect_act_strict"]:
            assert row["category"] == "hint", row["id"]


# ----------------------------------------------------- the safety criterion


@pytest.mark.parametrize("row", COMMANDS, ids=[row["id"] for row in COMMANDS])
def test_strict_mode_never_acts_on_a_command_fixture(row):
    result = classify(row["text"])
    assert may_act("strict", result.klass) is False, f"{row['id']} -> {result.klass}"
    if "expect_class" in row:
        assert result.klass == row["expect_class"], row["id"]
    if "expect_intent" in row:
        assert result.intent == row["expect_intent"], row["id"]


@pytest.mark.parametrize("row", NEGATIVES, ids=[row["id"] for row in NEGATIVES])
def test_strict_mode_never_acts_on_a_negative_fixture(row):
    result = classify(row["text"])
    assert may_act("strict", result.klass) is False, f"{row['id']} -> {result.klass}"


def test_strict_mode_false_positive_count_is_zero():
    """The headline number, computed over the whole corpus at once."""
    non_acting = COMMANDS + NEGATIVES
    false_positives = [
        row["id"] for row in non_acting if may_act("strict", classify(row["text"]).klass)
    ]
    assert false_positives == [], f"{len(false_positives)} of {len(non_acting)}: {false_positives}"


def test_clock_untrusted_never_acts_on_anything_non_hint():
    for row in COMMANDS + NEGATIVES:
        result = classify(row["text"])
        assert may_act("weekday", result.klass, clock_untrusted=True) is False, row["id"]


# ------------------------------------------------------------- hint recall


def _hint_hits():
    hits, misses = [], []
    for row in HINTS:
        result = classify(row["text"])
        ok = (
            may_act("strict", result.klass)
            and result.klass == row["expect_class"]
            and result.intent == row["expect_intent"]
        )
        (hits if ok else misses).append((row, result))
    return hits, misses


def test_hint_recall_on_typed_text_is_at_least_seventy_percent(capsys):
    hits, misses = _hint_hits()
    recall = len(hits) / len(HINTS)
    with capsys.disabled():
        print(f"\nhint recall: {len(hits)}/{len(HINTS)} = {recall:.0%}")
        for row, result in misses:
            expected = f"{row['expect_class']}/{row['expect_intent']}"
            got = f"{result.klass}/{result.intent}"
            print(f"  MISS {row['id']}: expected {expected}, got {got}")
    assert recall >= HINT_RECALL_FLOOR, f"{recall:.0%} < {HINT_RECALL_FLOOR:.0%}"


def test_every_hint_subcategory_has_at_least_one_hit():
    hits, _ = _hint_hits()
    hit_subcategories = {row["subcategory"] for row, _ in hits}
    assert {row["subcategory"] for row in HINTS} == hit_subcategories


def test_a_missed_hint_is_never_a_command_class():
    # Failing safe means falling back to "unrelated", never to a class that
    # would act, and never mislabelling a hint as a command.
    _, misses = _hint_hits()
    for row, result in misses:
        assert result.klass == "unrelated", f"{row['id']} -> {result.klass}"


def test_detaching_a_prefix_letter_does_not_change_any_hint_verdict():
    prefixes = ("ש", "ה", "ו", "ב", "ל", "כ", "מ")
    checked = 0
    for row in HINTS:
        words = row["text"].split()
        for index, word in enumerate(words):
            if len(word) > 2 and word.startswith(prefixes):
                split = list(words)
                split[index : index + 1] = [word[0], word[1:]]
                assert classify(" ".join(split)) == classify(row["text"]), row["id"]
                checked += 1
                break
    assert checked >= 10


# -------------------------------------------- no question as spoken output


def _hebrew_string_literals(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if any("֐" <= ch <= "׿" for ch in node.value):
                yield node.value


def _regex_pattern_literals(module: ast.Module) -> set[str]:
    """Every string literal assigned to a ``*_PATTERN(S)`` name.

    The classifier matches Hebrew verb roots with regular expressions, whose
    ``(?:...)`` groups contain question marks. They are matched against input
    and never spoken, so they are excluded from the check below by the one
    thing that distinguishes them structurally: the name they are bound to.
    """
    patterns: set[str] = set()
    for node in ast.walk(module):
        if not isinstance(node, ast.Assign):
            continue
        names = [t.id for t in node.targets if isinstance(t, ast.Name)]
        if not any(name.endswith(("_PATTERN", "_PATTERNS")) for name in names):
            continue
        for child in ast.walk(node.value):
            if isinstance(child, ast.Constant) and isinstance(child.value, str):
                patterns.add(child.value)
    return patterns


def test_no_code_path_produces_a_question_as_spoken_output():
    """Spoken output is Hebrew and never invites a reply (CLAUDE.md #5).

    Any Hebrew string literal in the package is a candidate for being spoken,
    so none of them may carry a question mark (ASCII, Arabic or full-width).
    Docstrings are excluded: they quote user utterances, and are never spoken.
    Regex patterns are excluded for the same reason (see above).
    """
    offenders = []
    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        module = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        docstrings = set()
        for node in ast.walk(module):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                doc = ast.get_docstring(node, clean=False)
                if doc:
                    docstrings.add(doc)
        excluded = docstrings | _regex_pattern_literals(module)
        for literal in _hebrew_string_literals(path):
            if literal in excluded:
                continue
            if any(mark in literal for mark in ("?", "؟", "？")):
                offenders.append((path.name, literal))
    assert offenders == [], offenders


def test_corpus_has_at_least_ten_command_hint_mixtures_and_ten_fragments():
    for category in ("mixed_command_hint", "fragment"):
        count = len([row for row in NEGATIVES if row["subcategory"] == category])
        assert count >= 10, f"{category}: {count}"


def test_corpus_mixes_commands_and_hints_in_both_orders():
    mixed = [row["text"] for row in NEGATIVES if row["subcategory"] == "mixed_command_hint"]
    assert any(text.startswith(("חם", "קר", "קשה")) for text in mixed)
    assert any(text.startswith(("שמישהו", "מישהו", "כדאי", "צריך")) for text in mixed)


def test_every_command_fixture_carrying_an_intent_would_execute_it_on_a_weekday():
    # Weekday mode obeys direct commands, so a command class has to carry the
    # intent the pipeline will run. Strict mode is unaffected: may_act gates
    # on the class, which is asserted above.
    for row in COMMANDS:
        if "expect_intent" not in row:
            continue
        result = classify(row["text"])
        assert result.intent == row["expect_intent"], row["id"]
        assert may_act("weekday", result.klass) is True, row["id"]

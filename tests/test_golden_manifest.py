"""The CI half of the golden set: the manifest itself, and a replay if we have one.

The live golden run needs the box (lobes + the senses model) and is marked
``golden``. What CI can still do, on every push, with no server and no key:

1. prove the manifest is well-formed -- unique ids, a known vocabulary, and
   every ``act_strict: false`` row in a category that *justifies* refusing to
   act. A golden set whose own expectations are wrong measures nothing;
2. replay the real model's recorded answers
   (``tests/fixtures/decider/golden_replay.json``, written by
   ``--record`` on the box) through the same scoring code the live run uses,
   so a regression in the *scoring* is caught in CI and the last real
   measurement stays reproducible;
3. when there is no recording yet, walk the manifest through the deterministic
   rule oracle instead -- which exercises the runner end to end offline
   without asserting anything about the rules' accuracy (they are not the
   product; deviation d2).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from shabbos_goy.decider import ContextWindow, ReplayDecider
from shabbos_goy.decider.oracle import RuleOracle
from shabbos_goy.policy import CLASSES
from tests.golden import runner as gr

REPLAY_PATH = Path(__file__).resolve().parents[1] / "tests/fixtures/decider/golden_replay.json"


@pytest.fixture(scope="module")
def rows() -> list[gr.GoldenRow]:
    return gr.load_manifest()


def test_the_manifest_is_well_formed(rows):
    assert gr.validate_manifest(rows) == []


def test_every_manifest_id_is_unique(rows):
    ids = [r.id for r in rows]
    assert len(ids) == len(set(ids))


def test_every_row_has_an_acceptable_class_vocabulary(rows):
    for r in rows:
        assert r.classes, f"{r.id} lists no acceptable class"
        assert set(r.classes) <= set(CLASSES), r.id


def test_every_must_not_act_row_is_justified_by_its_category(rows):
    for r in rows:
        if r.act_strict:
            continue
        justified = r.category in ("command", "negative") or (
            r.category == "hint" and r.intent == "none"
        )
        assert justified, f"{r.id}: act_strict=false with no category that justifies it"


def test_hint_rows_that_act_name_the_intent_they_should_act_with(rows):
    for r in rows:
        if r.category == "hint" and r.act_strict:
            assert r.intent in gr.ACTING_INTENTS, r.id


def test_the_manifest_still_covers_both_halves_of_the_invariant(rows):
    assert sum(1 for r in rows if r.act_strict) >= 20
    assert sum(1 for r in rows if r.category == "command") >= 20


def test_the_runner_scores_the_whole_manifest_offline(rows):
    """The oracle path: end-to-end exercise of the runner with no server.

    This asserts the *runner* works over the real manifest, not that the
    rules are accurate -- the rules are a test oracle, not the product.
    """
    results = gr.run_decider(rows, RuleOracle(), mode="strict", transcripts_for=lambda r: [r.text])
    scored = gr.score(rows, results, mode="strict", entrance="text")
    assert scored["rows"] == len(rows)
    assert scored["decider_failures"] == 0
    assert isinstance(scored["hard_false_positives"], list)


@pytest.mark.skipif(not REPLAY_PATH.exists(), reason="no recorded golden run yet (--record)")
def test_the_recorded_golden_run_still_passes_the_thresholds(rows):
    """EVERY recorded entrance must be clean, not whichever one wrote last.

    Risk r13: the fixture used to be keyed by transcript text alone, so
    audio-realtime's answer overwrote the text entrance's for the same string
    and a run that FAILED the text entrance produced a fixture that passed.
    The fixture is now keyed by entrance, and this test scores each one.
    """
    data = json.loads(REPLAY_PATH.read_text("utf-8"))
    assert data, "the replay file is empty"
    nested = all(isinstance(v, dict) and "class" not in v for v in data.values())
    assert nested, (
        "replay file is not keyed by entrance -- re-record it; a flat file "
        "cannot show which entrance a decision came from (risk r13)"
    )

    cache = gr.load_asr_cache()
    checked = []
    for entrance in sorted(data):
        replay = ReplayDecider.from_file(REPLAY_PATH, entrance=entrance)
        recorded = set(data[entrance])
        if entrance == "text":
            transcripts_for = lambda row: [row.text]  # noqa: E731
            covered = [r for r in rows if r.text in recorded]
        else:
            heard = cache.get(entrance, {})
            transcripts_for = lambda row: list(heard.get(row.id, []))  # noqa: E731
            covered = [r for r in rows if any(t in recorded for t in heard.get(r.id, []))]
        if not covered:
            continue
        results = gr.run_decider(covered, replay, mode="strict", transcripts_for=transcripts_for)
        scored = gr.score(covered, results, mode="strict", entrance=entrance)
        assert scored["hard_false_positives"] == [], (
            f"{entrance}/strict: recorded run acts on rows that must never act: "
            f"{scored['hard_false_positives']}"
        )
        checked.append(entrance)
    assert checked, "the replay file matches no manifest row on any entrance"


def test_a_recorded_decision_replays_identically(tmp_path, rows):
    """The replay contract itself, with a file this test writes."""
    hint = next(r for r in rows if r.act_strict and r.intent == "cool")
    path = tmp_path / "golden_replay.json"
    path.write_text(
        json.dumps({hint.text: {"class": "wish", "intent": "cool", "confidence": 0.9}}),
        "utf-8",
    )
    replay = ReplayDecider.from_file(path)
    got = replay.decide(hint.text, ContextWindow(), mode="strict")
    assert gr.decision_acts(got, "strict") is True
    results = gr.run_decider([hint], replay, mode="strict", transcripts_for=lambda r: [r.text])
    scored = gr.score([hint], results, mode="strict", entrance="text")
    assert scored["hint_recall"]["acted_right"] == 1

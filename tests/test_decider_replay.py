"""ReplayDecider, the rule oracle adapter, and the d2 runtime boundary.

The default test run must need no server: ReplayDecider answers from a
recorded JSON file and returns NO_DECISION for anything it has not seen.
RuleOracle exposes the t10 rule classifier as a Decider **for tests and the
golden set only** — the AST test at the bottom is what keeps it out of the
runtime (deviation d2).
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from shabbos_goy.classifier import classify
from shabbos_goy.decider import NO_DECISION, ContextWindow, ReplayDecider
from shabbos_goy.decider.oracle import RuleOracle
from shabbos_goy.decider.replay import REPLAY_FORMAT, entrances_in
from shabbos_goy.policy import may_act

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "decider" / "replay.json"
CORPUS = REPO_ROOT / "tests" / "fixtures" / "corpus.jsonl"


def _window() -> ContextWindow:
    return ContextWindow(max_items=4, max_age_seconds=600, clock=lambda: 0.0)


# --------------------------------------------------------------------------
# ReplayDecider
# --------------------------------------------------------------------------


def test_replay_answers_from_the_recorded_file() -> None:
    decider = ReplayDecider.from_file(FIXTURE)
    decision = decider.decide("חם פה נורא", _window(), mode="strict", ac_state=None)
    assert decision.klass == "remark"
    assert decision.intent == "cool"
    assert decision.source.startswith("replay")


def test_replay_returns_no_decision_for_unknown_text() -> None:
    decider = ReplayDecider.from_file(FIXTURE)
    decision = decider.decide("משהו שלא הוקלט מעולם", _window(), mode="strict", ac_state=None)
    assert decision.klass == NO_DECISION.klass
    assert decision.reason == "not_recorded"


def test_replay_needs_no_server_and_no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    import socket

    def _boom(*args: object, **kwargs: object) -> None:
        raise AssertionError("ReplayDecider must not open a socket")

    monkeypatch.setattr(socket.socket, "connect", _boom)
    decider = ReplayDecider.from_file(FIXTURE)
    assert decider.decide("חם פה נורא", _window(), mode="strict", ac_state=None).klass == "remark"


def test_replay_rejects_a_corrupt_recorded_entry(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text(
        json.dumps({"x": {"class": "command", "intent": "cool", "confidence": 0.9}}),
        encoding="utf-8",
    )
    decider = ReplayDecider.from_file(path)
    decision = decider.decide("x", _window(), mode="strict", ac_state=None)
    assert decision.klass == NO_DECISION.klass
    assert decision.reason == "bad_record"


def _envelope(buckets: dict) -> dict:
    return {"format": REPLAY_FORMAT, "entrances": buckets}


def test_a_recorded_envelope_with_one_entrance_loads_without_naming_it(tmp_path: Path) -> None:
    """Finding 3: a recorded fixture must be replayable by every consumer.

    `--decider replay` and the CLI replay paths pass no entrance. With a
    single recorded entrance there is nothing to choose, so the file loads.
    """
    path = tmp_path / "golden_replay.json"
    path.write_text(
        json.dumps(
            _envelope({"text": {"חם פה": {"class": "wish", "intent": "cool", "confidence": 0.8}}})
        ),
        encoding="utf-8",
    )
    decider = ReplayDecider.from_file(path)
    assert decider.decide("חם פה", _window(), mode="strict").intent == "cool"


def test_a_recorded_envelope_with_several_entrances_names_them_instead_of_guessing(
    tmp_path: Path,
) -> None:
    path = tmp_path / "golden_replay.json"
    path.write_text(
        json.dumps(
            _envelope(
                {
                    "text": {"חם פה": {"class": "wish", "intent": "cool", "confidence": 0.8}},
                    "audio-batch": {"חם פה": {"class": "unrelated", "intent": "none"}},
                }
            )
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="keyed by entrance") as excinfo:
        ReplayDecider.from_file(path)
    assert "audio-batch" in str(excinfo.value) and "text" in str(excinfo.value)
    assert (
        ReplayDecider.from_file(path, entrance="audio-batch")
        .decide("חם פה", _window(), mode="strict")
        .klass
        == "unrelated"
    )


def test_entrances_in_reports_the_recorded_entrances_and_none_for_a_flat_file(
    tmp_path: Path,
) -> None:
    flat = tmp_path / "flat.json"
    flat.write_text(json.dumps({"חם פה": {"class": "wish", "intent": "cool"}}), encoding="utf-8")
    assert entrances_in(flat) is None
    nested = tmp_path / "nested.json"
    nested.write_text(json.dumps(_envelope({"text": {}, "audio-batch": {}})), encoding="utf-8")
    assert entrances_in(nested) == ["audio-batch", "text"]


def test_a_flat_file_with_one_malformed_record_still_loads_and_fails_closed(
    tmp_path: Path,
) -> None:
    """Finding 5: a localized bad record must not become a load failure.

    `{"x": {"intent": "warm"}}` has no ``class``, which an
    absence-of-a-key discriminator mistook for an entrance bucket. The file
    must load and ``decide`` must fail closed, per record, as before.
    """
    path = tmp_path / "flat.json"
    path.write_text(
        json.dumps(
            {
                "x": {"intent": "warm"},
                "חם פה": {"class": "wish", "intent": "cool", "confidence": 0.8},
            }
        ),
        encoding="utf-8",
    )
    decider = ReplayDecider.from_file(path)
    bad = decider.decide("x", _window(), mode="strict")
    assert (bad.klass, bad.reason) == (NO_DECISION.klass, "bad_record")
    assert decider.decide("חם פה", _window(), mode="strict").intent == "cool"


def test_a_flat_file_of_nothing_but_malformed_records_still_loads(tmp_path: Path) -> None:
    path = tmp_path / "flat.json"
    path.write_text(json.dumps({"x": {"intent": "warm"}}), encoding="utf-8")
    decider = ReplayDecider.from_file(path)
    assert decider.decide("x", _window(), mode="strict").reason == "bad_record"


@pytest.mark.parametrize(
    "payload",
    [
        {"format": REPLAY_FORMAT, "entrances": []},
        {"format": REPLAY_FORMAT},
        {"format": REPLAY_FORMAT, "entrances": {"text": "nope"}},
        {"format": "golden-replay/99", "entrances": {"text": {}}},
    ],
)
def test_a_malformed_envelope_is_refused_deterministically(tmp_path: Path, payload) -> None:
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError):
        ReplayDecider.from_file(path)


def test_an_envelope_with_no_entrances_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "empty.json"
    path.write_text(json.dumps(_envelope({})), encoding="utf-8")
    with pytest.raises(ValueError, match="no entrances"):
        ReplayDecider.from_file(path)


def test_a_non_object_replay_file_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    with pytest.raises(ValueError, match="JSON object"):
        ReplayDecider.from_file(path)


def test_selecting_an_entrance_from_a_flat_file_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "flat.json"
    path.write_text(json.dumps({"חם פה": {"class": "wish", "intent": "cool"}}), encoding="utf-8")
    with pytest.raises(ValueError, match="flat"):
        ReplayDecider.from_file(path, entrance="text")


def test_replay_from_mapping() -> None:
    decider = ReplayDecider({"hi": {"class": "wish", "intent": "warm", "confidence": 0.3}})
    assert decider.decide("hi", _window(), mode="strict", ac_state=None).intent == "warm"


# --------------------------------------------------------------------------
# RuleOracle (tests / golden set only)
# --------------------------------------------------------------------------


def _corpus_rows() -> list[dict]:
    with CORPUS.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def test_rule_oracle_is_a_faithful_adapter_over_the_whole_corpus() -> None:
    """Every field of every verdict must survive the adapter unchanged.

    This is the whole contract of the oracle: it adds a ``source`` and
    changes nothing else, so a golden-set comparison against it measures the
    rules, not this wrapper. (The corpus's own expectations are asserted by
    ``tests/test_classifier_corpus.py``; hint rows there are a recall floor,
    not a per-row guarantee, so they are not re-asserted here.)
    """
    oracle = RuleOracle()
    rows = _corpus_rows()
    assert len(rows) > 200, "corpus fixture looks truncated"
    for row in rows:
        decision = oracle.decide(row["text"], _window(), mode="strict", ac_state=None)
        verdict = classify(row["text"])
        assert decision.source == "rule-oracle:t10"
        assert (decision.klass, decision.intent, decision.confidence, decision.reason) == (
            verdict.klass,
            verdict.intent,
            verdict.confidence,
            verdict.reason,
        ), row["id"]


def test_rule_oracle_never_acts_on_a_command_or_negative_fixture() -> None:
    """The headline metric, measured through the oracle: zero false positives."""
    oracle = RuleOracle()
    non_acting = [row for row in _corpus_rows() if not row["expect_act_strict"]]
    assert len(non_acting) > 100
    offenders = [
        row["id"]
        for row in non_acting
        if may_act("strict", oracle.decide(row["text"], _window(), mode="strict").klass)
    ]
    assert offenders == []


def test_rule_oracle_never_raises_on_odd_input() -> None:
    oracle = RuleOracle()
    for text in ["", "   ", "x" * 5000, "😀"]:
        assert oracle.decide(text, _window(), mode="strict", ac_state=None) is not None


# --------------------------------------------------------------------------
# d2: the rules are out of the runtime
# --------------------------------------------------------------------------


def _imported_modules(path: Path, root: Path = REPO_ROOT) -> set[str]:
    """Absolute module names imported by *path* (relative imports resolved).

    ``root`` is the directory the package sits in, so the same walker can be
    pointed at a throwaway tree in the self-check below.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    # ``a/b/mod.py`` and ``a/b/__init__.py`` both live in package ``a.b``.
    package = list(path.relative_to(root).with_suffix("").parts[:-1])
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package[: len(package) - (node.level - 1)] if node.level > 1 else package
                module = ".".join(base + ([node.module] if node.module else []))
            else:
                module = node.module or ""
            found.add(module)
            for alias in node.names:
                found.add(f"{module}.{alias.name}" if module else alias.name)
    return found


def test_no_runtime_module_imports_the_rule_classifier() -> None:
    package = REPO_ROOT / "shabbos_goy"
    exempt = {package / "classifier", package / "decider" / "oracle.py"}
    offenders = []
    checked = 0
    for path in sorted(package.rglob("*.py")):
        if any(path == item or item in path.parents for item in exempt):
            continue
        checked += 1
        modules = _imported_modules(path)
        if any(
            m == "shabbos_goy.classifier" or m.startswith("shabbos_goy.classifier.")
            for m in modules
        ):
            offenders.append(str(path.relative_to(REPO_ROOT)))
    assert checked > 5, "the walk found almost nothing — the test would pass vacuously"
    assert offenders == [], f"runtime modules still import the rule classifier: {offenders}"


def test_the_import_walk_actually_detects_an_offender(tmp_path: Path) -> None:
    """The guard above is only worth having if it can fail: prove it does."""
    offender = tmp_path / "shabbos_goy" / "sub" / "mod.py"
    offender.parent.mkdir(parents=True)
    offender.write_text("from ..classifier import classify\n", encoding="utf-8")
    assert "shabbos_goy.classifier" in _imported_modules(offender, root=tmp_path)

    plain = tmp_path / "shabbos_goy" / "sub" / "plain.py"
    plain.write_text("import shabbos_goy.classifier as c\n", encoding="utf-8")
    assert "shabbos_goy.classifier" in _imported_modules(plain, root=tmp_path)

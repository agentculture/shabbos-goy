"""Tests for ``shabbos-goy classify``.

No microphone, no real network: the "gemma" decider tests point
``SHABBOS_GOY_LOBES_URL`` at the shared in-process fake server
(``tests/decider_fake_server.py``) already used by
``tests/test_decider_gemma.py``, on ``127.0.0.1`` with an ephemeral port.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from shabbos_goy.cli import main
from tests.decider_fake_server import FakeSensesServer, ScriptedResponse, decision_body

PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "shabbos_goy"


def test_classify_never_imports_the_rule_classifier() -> None:
    """Deviation d2: classify must never import shabbos_goy.classifier.

    Belt-and-braces alongside the repo-wide guard in
    tests/test_decider_replay.py (which already scans the whole package) --
    this pins the check specifically to the new file this task added.
    """
    path = PACKAGE_ROOT / "cli" / "_commands" / "classify.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            prefix = "." * node.level
            modules.add(f"{prefix}{node.module}")
    assert not any(m == "shabbos_goy.classifier" or "classifier" in m for m in modules), modules


def test_classify_replay_decider(tmp_path, capsys: pytest.CaptureFixture[str]) -> None:
    replay_file = tmp_path / "replay.json"
    replay_file.write_text(
        json.dumps({"חם פה נורא": {"class": "remark", "intent": "cool", "confidence": 0.9}}),
        encoding="utf-8",
    )
    rc = main(
        [
            "classify",
            "חם פה נורא",
            "--decider",
            "replay",
            "--replay-file",
            str(replay_file),
            "--mode",
            "strict",
            "--json",
        ]
    )
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["class"] == "remark"
    assert payload["intent"] == "cool"
    assert payload["source"] == "replay"
    assert payload["mode"] == "strict"
    assert payload["would_act"] is True


def test_classify_replay_requires_replay_file(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["classify", "hello", "--decider", "replay"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "error:" in err
    assert "--replay-file" in err


def test_classify_gemma_missing_env_exits_2(
    monkeypatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("SHABBOS_GOY_LOBES_URL", raising=False)
    monkeypatch.delenv("SHABBOS_GOY_SENSES_URL", raising=False)
    rc = main(["classify", "hello"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "SHABBOS_GOY_LOBES_URL" in err


def test_classify_gemma_round_trip(monkeypatch, capsys: pytest.CaptureFixture[str]) -> None:
    with FakeSensesServer(
        responses=[ScriptedResponse(body=decision_body("wish", "cool", 0.81))]
    ) as server:
        monkeypatch.setenv("SHABBOS_GOY_LOBES_URL", server.base_url)
        monkeypatch.setenv("SHABBOS_GOY_LOBES_API_KEY", "test-key")

        rc = main(["classify", "הלוואי שהיה קר", "--mode", "strict", "--json"])
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["class"] == "wish"
        assert payload["intent"] == "cool"
        assert payload["source"].startswith("gemma:")
        assert payload["would_act"] is True

        # never actuates: no request to sensibo/wpctl is possible from this
        # command at all (no adapter is even wired), so the only network
        # traffic the fake server saw is the one decide() POST.
        assert len(server.requests) == 1
        assert server.requests[0].path == "/v1/chat/completions"
        assert server.requests[0].headers.get("authorization") == "Bearer test-key"

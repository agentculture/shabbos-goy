"""Tests for ``shabbos-goy actions``."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from shabbos_goy.cli import main

FIXTURE_CONFIG = Path(__file__).parent / "fixtures" / "pipeline" / "config.json"


def test_actions_json_shape(monkeypatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv("SHABBOS_GOY_CONFIG", str(FIXTURE_CONFIG))
    rc = main(["actions", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["whitelist"]["sensibo"]["pods"] == ["PODFAKE1"]
    intents = {row["intent"] for row in payload["intent_to_tool"]}
    assert intents == {"cool", "warm", "louder", "quieter", "status"}


def test_actions_text(monkeypatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv("SHABBOS_GOY_CONFIG", str(FIXTURE_CONFIG))
    rc = main(["actions"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Whitelist" in out
    assert "PODFAKE1" in out
    assert "cool -> sensibo" in out


def test_actions_fails_closed_shows_empty_whitelist(
    monkeypatch, tmp_path, capsys: pytest.CaptureFixture[str]
) -> None:
    # A broken/missing config never crashes `actions`: it reports an empty
    # whitelist (config.py's own fail-closed contract), not an error.
    monkeypatch.setenv("SHABBOS_GOY_CONFIG", str(tmp_path / "no-such-config.json"))
    rc = main(["actions", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["whitelist"] == {}

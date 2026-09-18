"""Tests for ``shabbos-goy zmanim``."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from shabbos_goy.cli import main

FIXTURE_CONFIG = Path(__file__).parent / "fixtures" / "pipeline" / "config.json"


def test_zmanim_json_shape(monkeypatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv("SHABBOS_GOY_CONFIG", str(FIXTURE_CONFIG))
    rc = main(["zmanim", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] in ("weekday", "strict")
    assert isinstance(payload["window_kinds"], list)
    assert isinstance(payload["clock_trusted"], bool)
    assert isinstance(payload["overridden"], bool)
    win = payload["next_strict_window"]
    assert win is None or {"starts_at_local", "ends_at_local", "kinds"} <= set(win)


def test_zmanim_text(monkeypatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv("SHABBOS_GOY_CONFIG", str(FIXTURE_CONFIG))
    rc = main(["zmanim"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "mode:" in out
    assert "clock trusted:" in out


def test_zmanim_fails_closed_on_missing_config(
    monkeypatch, tmp_path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("SHABBOS_GOY_CONFIG", str(tmp_path / "no-such-config.json"))
    rc = main(["zmanim"])
    assert rc == 2
    err = capsys.readouterr().err
    assert err.startswith("error:")
    assert "hint:" in err

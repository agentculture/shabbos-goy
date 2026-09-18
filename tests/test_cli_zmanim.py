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


def test_the_cli_helper_shares_mode_pys_fail_closed_builders(tmp_path) -> None:
    """Found in review: a second copy of config -> zmanim parsing drifts.

    Whatever mode.py refuses (a fractional candle-lighting offset, a tzeit angle
    below the sunset geometry, an unknown timezone) the CLI helper must refuse
    too, and a failure inside the zmanim maths must not escape as a traceback.
    """
    import json
    from datetime import datetime, timezone

    from shabbos_goy.cli._commands import _domain
    from shabbos_goy.config import load_config

    good = {
        "location": {"lat": 31.78, "lon": 35.22, "timezone": "Asia/Jerusalem"},
        "candle_lighting_offset_minutes": 18,
        "tzeit_definition": "3_medium_stars",
        "region": "israel",
    }
    now = datetime(2026, 9, 22, 9, 0, tzinfo=timezone.utc)
    for bad in (
        {"candle_lighting_offset_minutes": -0.5},
        {"tzeit_definition": "degrees:0"},
        {"tzeit_definition": "minutes:nan"},
        {"location": {"lat": 31.78, "lon": 35.22, "timezone": "Mars/Olympus_Mons"}},
    ):
        path = tmp_path / "config.json"
        path.write_text(json.dumps({**good, **bad}), "utf-8")
        assert _domain.next_strict_window(now, load_config(path=path)) is None, bad
    path.write_text(json.dumps(good), "utf-8")
    assert _domain.next_strict_window(now, load_config(path=path)) is not None

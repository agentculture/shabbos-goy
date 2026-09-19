"""Tests for the ``shabbos-goy ac`` noun."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from shabbos_goy.cli import main
from tests.decider_fake_server import closed_port
from tests.test_cli_control import _FakeControlServer

FIXTURE_CONFIG = Path(__file__).parent / "fixtures" / "pipeline" / "config.json"


def test_ac_bare_noun_prints_overview(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["ac"])
    assert rc == 0
    assert capsys.readouterr().out.strip()


def test_ac_overview_json(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["ac", "overview", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["subject"] == "shabbos-goy ac"


def test_ac_status_no_listener_exits_2(monkeypatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv("SHABBOS_GOY_CONFIG", str(FIXTURE_CONFIG))
    monkeypatch.setenv("SHABBOS_GOY_CONTROL_URL", f"http://127.0.0.1:{closed_port()}")
    rc = main(["ac", "status"])
    assert rc == 2


def test_ac_power_dry_run_by_default(monkeypatch, capsys: pytest.CaptureFixture[str]) -> None:
    with _FakeControlServer() as server:
        server.responses["/ac/power"] = {"acted": False, "requested_apply": False}
        monkeypatch.setenv("SHABBOS_GOY_CONFIG", str(FIXTURE_CONFIG))
        monkeypatch.setenv("SHABBOS_GOY_CONTROL_URL", server.base_url)
        rc = main(["ac", "power", "on", "--json"])
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["acted"] is False
        assert server.requests[-1]["body"] == {
            "action": "power",
            "value": "on",
            "apply": False,
        }


def test_ac_power_apply_is_forwarded_explicitly(
    monkeypatch, capsys: pytest.CaptureFixture[str]
) -> None:
    with _FakeControlServer() as server:
        server.responses["/ac/power"] = {"acted": True, "requested_apply": True}
        monkeypatch.setenv("SHABBOS_GOY_CONFIG", str(FIXTURE_CONFIG))
        monkeypatch.setenv("SHABBOS_GOY_CONTROL_URL", server.base_url)
        rc = main(["ac", "power", "off", "--apply", "--json"])
        assert rc == 0
        assert server.requests[-1]["body"] == {
            "action": "power",
            "value": "off",
            "apply": True,
        }


def test_ac_power_rejects_bad_value() -> None:
    with pytest.raises(SystemExit) as exc:
        main(["ac", "power", "bogus"])
    assert exc.value.code == 1  # argparse `choices` rejects it before our own code runs

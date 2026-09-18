"""Tests for the ``shabbos-goy volume`` noun."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from shabbos_goy.cli import main
from tests.decider_fake_server import closed_port
from tests.test_cli_control import _FakeControlServer

FIXTURE_CONFIG = Path(__file__).parent / "fixtures" / "pipeline" / "config.json"


def test_volume_bare_noun_prints_overview(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["volume"])
    assert rc == 0
    assert capsys.readouterr().out.strip()


def test_volume_overview_json(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["volume", "overview", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["subject"] == "shabbos-goy volume"


def test_volume_get_no_listener_exits_2(monkeypatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv("SHABBOS_GOY_CONFIG", str(FIXTURE_CONFIG))
    monkeypatch.setenv("SHABBOS_GOY_CONTROL_URL", f"http://127.0.0.1:{closed_port()}")
    rc = main(["volume", "get"])
    assert rc == 2


def test_volume_set_dry_run_by_default(monkeypatch, capsys: pytest.CaptureFixture[str]) -> None:
    with _FakeControlServer() as server:
        server.responses["/volume"] = {"level": 0.5, "muted": False}
        monkeypatch.setenv("SHABBOS_GOY_CONFIG", str(FIXTURE_CONFIG))
        monkeypatch.setenv("SHABBOS_GOY_CONTROL_URL", server.base_url)
        rc = main(["volume", "set", "up", "--json"])
        assert rc == 0
        assert server.requests[-1]["body"] == {"direction": "up", "apply": False}


def test_volume_set_apply_is_forwarded_explicitly(
    monkeypatch, capsys: pytest.CaptureFixture[str]
) -> None:
    with _FakeControlServer() as server:
        server.responses["/volume"] = {"level": 0.45, "muted": False}
        monkeypatch.setenv("SHABBOS_GOY_CONFIG", str(FIXTURE_CONFIG))
        monkeypatch.setenv("SHABBOS_GOY_CONTROL_URL", server.base_url)
        rc = main(["volume", "set", "down", "--apply", "--json"])
        assert rc == 0
        assert server.requests[-1]["body"] == {"direction": "down", "apply": True}

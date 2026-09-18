"""Tests for the ``shabbos-goy mode`` noun."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from shabbos_goy.cli import main
from tests.decider_fake_server import closed_port
from tests.test_cli_control import _FakeControlServer

FIXTURE_CONFIG = Path(__file__).parent / "fixtures" / "pipeline" / "config.json"


def test_mode_bare_noun_prints_overview(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["mode"])
    assert rc == 0
    assert capsys.readouterr().out.strip()


def test_mode_overview_json(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["mode", "overview", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["subject"] == "shabbos-goy mode"


def test_mode_show_with_no_listener_falls_back_locally(
    monkeypatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("SHABBOS_GOY_CONFIG", str(FIXTURE_CONFIG))
    monkeypatch.setenv("SHABBOS_GOY_CONTROL_URL", f"http://127.0.0.1:{closed_port()}")
    rc = main(["mode", "show", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["listener"] is False
    assert payload["mode"] in ("weekday", "strict")
    assert "next_strict_window" in payload


def test_mode_show_with_listener(monkeypatch, capsys: pytest.CaptureFixture[str]) -> None:
    with _FakeControlServer() as server:
        server.responses["/mode"] = {"mode": "strict", "override": "strict"}
        monkeypatch.setenv("SHABBOS_GOY_CONFIG", str(FIXTURE_CONFIG))
        monkeypatch.setenv("SHABBOS_GOY_CONTROL_URL", server.base_url)
        rc = main(["mode", "show", "--json"])
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["listener"] is True
        assert payload["mode"] == "strict"


def test_mode_set_with_no_listener_exits_2(monkeypatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv("SHABBOS_GOY_CONFIG", str(FIXTURE_CONFIG))
    monkeypatch.setenv("SHABBOS_GOY_CONTROL_URL", f"http://127.0.0.1:{closed_port()}")
    rc = main(["mode", "set", "strict"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "shabbos-goy listen" in err


def test_mode_set_with_listener_forwards_override(
    monkeypatch, capsys: pytest.CaptureFixture[str]
) -> None:
    with _FakeControlServer() as server:
        server.responses["/mode"] = {"mode": "strict", "override": "strict"}
        monkeypatch.setenv("SHABBOS_GOY_CONFIG", str(FIXTURE_CONFIG))
        monkeypatch.setenv("SHABBOS_GOY_CONTROL_URL", server.base_url)
        rc = main(["mode", "set", "strict", "--json"])
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["listener"] is True
        assert server.requests[-1]["body"] == {"override": "strict"}

        rc = main(["mode", "set", "auto"])
        assert rc == 0
        assert server.requests[-1]["body"] == {"override": None}

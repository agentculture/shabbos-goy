"""The t13 CLI verbs, driven against the REAL control server t14 starts.

``shabbos_goy/cli/_commands/_control.py`` was written against an endpoint
that did not exist yet. These tests are the contract between the two: the
same client, the same paths, the same JSON shapes, against a real running
listener whose adapters are fakes.
"""

from __future__ import annotations

import json

import pytest

from shabbos_goy import mode as mode_module
from shabbos_goy.cli import main

from .test_listen_support import POD, FakeVolumeReader, make_listener, running


@pytest.fixture(autouse=True)
def _no_override():
    mode_module.clear_override()
    yield
    mode_module.clear_override()


@pytest.fixture
def listener(tmp_path, monkeypatch):
    reader = FakeVolumeReader(level=0.25, muted=False)
    listener = make_listener(tmp_path, volume_get=reader)
    with running(listener):
        monkeypatch.setenv("SHABBOS_GOY_CONTROL_URL", listener.control_url)
        listener.volume_reader = reader  # type: ignore[attr-defined]
        yield listener


def _json(capsys) -> dict:
    return json.loads(capsys.readouterr().out)


def test_ac_status_reads_through_the_listeners_own_adapter(listener, capsys) -> None:
    rc = main(["ac", "status", "--json"])
    payload = _json(capsys)
    assert rc == 0
    assert payload["available"] is True
    assert payload["power"] == "off"
    assert payload["temperature"] == 28.0
    assert listener.fakes["ac"].status_calls >= 1


def test_ac_power_is_a_dry_run_without_apply(listener, capsys) -> None:
    rc = main(["ac", "power", "on", "--json"])
    payload = _json(capsys)
    assert rc == 0
    assert payload["ok"] is True
    assert payload["verdict"] == "dry_run"
    assert payload["action"] == "ac_power_on"
    assert listener.fakes["ac"].power_calls == [(POD, True, False)]


def test_cli_apply_cannot_escalate_a_dry_run_listener(listener, capsys) -> None:
    """The listener's own --apply is the ceiling: a CLI --apply never raises it."""
    rc = main(["ac", "power", "on", "--apply", "--json"])
    payload = _json(capsys)
    assert rc == 0
    assert payload["verdict"] == "dry_run"
    assert listener.fakes["ac"].power_calls == [(POD, True, False)]


def test_ac_power_with_an_applying_listener_actuates(tmp_path, monkeypatch, capsys) -> None:
    from shabbos_goy.runtime import ListenerOptions

    listener = make_listener(
        tmp_path,
        options=ListenerOptions(control_address="127.0.0.1:0", poll_interval=0.01, apply=True),
    )
    with running(listener):
        monkeypatch.setenv("SHABBOS_GOY_CONTROL_URL", listener.control_url)
        rc = main(["ac", "power", "on", "--apply", "--json"])
        payload = _json(capsys)
    assert rc == 0
    assert payload["verdict"] == "acted"
    assert listener.fakes["ac"].power_calls == [(POD, True, True)]


def test_volume_get_reads_the_listeners_volume(listener, capsys) -> None:
    rc = main(["volume", "get", "--json"])
    payload = _json(capsys)
    assert rc == 0
    assert payload["available"] is True
    assert payload["level"] == 0.25
    assert payload["muted"] is False


def test_volume_set_is_a_dry_run_without_apply(listener, capsys) -> None:
    rc = main(["volume", "set", "up", "--json"])
    payload = _json(capsys)
    assert rc == 0
    assert payload["verdict"] == "dry_run"
    assert listener.fakes["volume"].steps == []


def test_mode_show_reports_the_listeners_resolved_mode(listener, capsys) -> None:
    rc = main(["mode", "show", "--json"])
    payload = _json(capsys)
    assert rc == 0
    assert payload["listener"] is True
    assert payload["mode"] == "weekday"
    assert payload["clock_trusted"] is True
    assert payload["override"] is None


def test_mode_set_forces_and_clears_the_listeners_in_memory_override(listener, capsys) -> None:
    assert main(["mode", "set", "strict", "--json"]) == 0
    payload = _json(capsys)
    assert payload["listener"] is True
    assert payload["mode"] == "strict"
    assert payload["overridden"] is True
    assert listener.resolved_mode().mode == "strict"

    assert main(["mode", "set", "auto", "--json"]) == 0
    payload = _json(capsys)
    assert payload["override"] is None
    assert listener.resolved_mode().mode == "weekday"


def test_an_unknown_override_is_refused_by_the_endpoint(listener) -> None:
    from shabbos_goy.cli._commands import _control

    result = _control.post_json(listener.control_url, "/mode", {"override": "shabbat"})
    assert result.ok is False
    assert result.reason == "http_400"


def test_a_control_request_is_refused_from_another_origin(listener) -> None:
    from .test_listen_support import request

    response = request(
        f"{listener.control_url}/ac/status", headers={"Origin": "http://evil.example"}
    )
    assert response.status == 403


def test_with_no_listener_running_the_verbs_exit_two(monkeypatch, tmp_path, capsys) -> None:
    from tests.decider_fake_server import closed_port

    monkeypatch.setenv("SHABBOS_GOY_CONTROL_URL", f"http://127.0.0.1:{closed_port()}")
    assert main(["ac", "status", "--json"]) == 2
    capsys.readouterr()
    assert main(["mode", "set", "strict", "--json"]) == 2

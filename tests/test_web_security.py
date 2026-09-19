"""Criterion 2: the security boundary, tested hard.

The dashboard is reachable over Tailscale only and carries no token, so the
whole boundary is: (a) it binds a tailnet address (``test_web_bind.py``), and
(b) nothing a browser on another origin can reach it with may change state.
That makes these four rules load-bearing:

* a POST from a foreign ``Origin`` is refused;
* a POST whose ``Host`` header does not name this server is refused
  (DNS rebinding, which is how a foreign page reaches a private address at
  all);
* GET never changes state, whatever the query string looks like;
* no response body ever contains either key.
"""

from __future__ import annotations

import os

import pytest

from shabbos_goy import mode as mode_module

from .test_web_support import (
    HOT,
    MARKER_LOBES_KEY,
    MARKER_SENSIBO_KEY,
    MARKER_TEXT,
    POD,
    dashboard,
    feed,
    reset_override,
)

CONTROL_PATHS = (
    "/api/control/ac",
    "/api/control/volume",
    "/api/control/mode",
    "/api/control/preflight",
)


@pytest.fixture(autouse=True)
def _no_override():
    reset_override()
    yield
    reset_override()


# ---------------------------------------------------------------------------
# Origin
# ---------------------------------------------------------------------------


def test_a_post_with_no_origin_is_allowed(tmp_path) -> None:
    """curl and the CLI send no Origin; only browsers do, and that is the
    whole point of the check."""
    with dashboard(tmp_path) as ui:
        response = ui.post("/api/control/ac", {"power": "on"})
    assert response.status == 200


def test_a_post_with_a_matching_origin_is_allowed(tmp_path) -> None:
    with dashboard(tmp_path) as ui:
        response = ui.post("/api/control/ac", {"power": "on"}, headers={"Origin": ui.url})
    assert response.status == 200


@pytest.mark.parametrize(
    "origin",
    [
        "http://evil.example",
        "https://evil.example",
        "http://127.0.0.1:1",
        "http://localhost",
        "null",
        "http://100.101.102.103:8787",
        "file://",
    ],
)
@pytest.mark.parametrize("path", CONTROL_PATHS)
def test_a_post_with_a_foreign_origin_is_refused(tmp_path, origin: str, path: str) -> None:
    with dashboard(tmp_path) as ui:
        response = ui.post(path, {"power": "on", "direction": "up"}, headers={"Origin": origin})

        assert response.status == 403
        assert response.json()["reason"] == "origin_mismatch"
        # Nothing happened: no adapter call, no override, no control log entry.
        assert ui.stack.ac.power_calls == []
        assert ui.stack.volume.steps == []
        assert mode_module.get_override() is None
        assert ui.get("/api/state").json()["controls"] == []


def test_a_post_with_a_spoofed_host_header_is_refused(tmp_path) -> None:
    """DNS rebinding: the browser's Host is the attacker's name, not ours."""
    with dashboard(tmp_path) as ui:
        response = ui.post(
            "/api/control/ac",
            {"power": "on"},
            headers={"Host": "dashboard.evil.example", "Origin": "http://dashboard.evil.example"},
        )

        assert response.status == 403
        assert response.json()["reason"] == "host_mismatch"
        assert ui.stack.ac.power_calls == []


def test_a_spoofed_host_is_refused_even_with_no_origin_at_all(tmp_path) -> None:
    with dashboard(tmp_path) as ui:
        response = ui.post(
            "/api/control/ac", {"power": "on"}, headers={"Host": "dashboard.evil.example"}
        )
        assert response.status == 403
        assert ui.stack.ac.power_calls == []


def test_the_right_host_on_the_wrong_port_is_refused(tmp_path) -> None:
    with dashboard(tmp_path) as ui:
        response = ui.post("/api/control/ac", {"power": "on"}, headers={"Host": "127.0.0.1:1"})
        assert response.status == 403
        assert ui.stack.ac.power_calls == []


def test_localhost_names_this_server_too(tmp_path) -> None:
    with dashboard(tmp_path) as ui:
        port = ui.server.bind_result.port
        response = ui.post(
            "/api/control/ac",
            {"power": "on"},
            headers={"Host": f"localhost:{port}", "Origin": f"http://localhost:{port}"},
        )
        assert response.status == 200


# ---------------------------------------------------------------------------
# GET never changes state
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", CONTROL_PATHS)
def test_get_on_a_control_path_is_refused(tmp_path, path: str) -> None:
    with dashboard(tmp_path) as ui:
        response = ui.get(f"{path}?power=on&direction=up&mode=weekday&apply=1")

        assert response.status == 405
        assert ui.stack.ac.power_calls == []
        assert ui.stack.volume.steps == []
        assert mode_module.get_override() is None


@pytest.mark.parametrize("path", ["/", "/api/state", "/api/utterances"])
def test_a_side_effect_looking_query_string_on_a_read_path_changes_nothing(
    tmp_path, path: str
) -> None:
    with dashboard(tmp_path) as ui:
        response = ui.get(f"{path}?power=on&apply=1&mode=weekday&override=strict")

        assert response.status == 200
        assert ui.stack.ac.power_calls == []
        assert ui.stack.volume.steps == []
        assert mode_module.get_override() is None


@pytest.mark.parametrize("path", ["/", "/api/state", "/api/utterances"])
def test_post_to_a_read_path_is_refused(tmp_path, path: str) -> None:
    with dashboard(tmp_path) as ui:
        assert ui.post(path, {"power": "on"}).status == 405


@pytest.mark.parametrize("method", ["PUT", "DELETE", "PATCH"])
def test_other_methods_are_refused_outright(tmp_path, method: str) -> None:
    with dashboard(tmp_path) as ui:
        response = ui.get("/api/control/ac", method=method, body={"power": "on"})
        assert response.status == 405
        assert ui.stack.ac.power_calls == []


# ---------------------------------------------------------------------------
# no key, ever
# ---------------------------------------------------------------------------


def test_no_response_contains_either_key(tmp_path, monkeypatch) -> None:
    """The marker test. Both keys are planted where a careless implementation
    would find them: the environment and the loaded config."""
    monkeypatch.setenv("SENSIBO_API_KEY", MARKER_SENSIBO_KEY)
    monkeypatch.setenv("SHABBOS_GOY_LOBES_API_KEY", MARKER_LOBES_KEY)
    monkeypatch.setenv("GATEWAY_API_KEY", MARKER_LOBES_KEY)

    with dashboard(tmp_path) as ui:
        assert ui.stack.config.raw["sensibo_api_key"] == MARKER_SENSIBO_KEY
        assert os.environ["SENSIBO_API_KEY"] == MARKER_SENSIBO_KEY
        feed(ui.stack.pipeline, HOT)

        bodies = [
            ui.get("/").body,
            ui.get("/api/state").body,
            ui.get("/api/utterances").body,
            ui.post("/api/control/ac", {"power": "on"}).body,
            ui.post("/api/control/volume", {"direction": "up"}).body,
            ui.post("/api/control/mode", {"mode": "strict"}).body,
            ui.post("/api/control/preflight").body,
            ui.get("/api/nope").body,
            ui.get("/api/control/ac").body,
            ui.post("/api/control/ac", {"power": "ON"}).body,
        ]

    for body in bodies:
        assert MARKER_SENSIBO_KEY not in body
        assert MARKER_LOBES_KEY not in body


def test_no_response_leaks_the_pod_id_either(tmp_path) -> None:
    """The pod id is not a key, but CLAUDE.md's privacy rule keeps it out of
    everything the dashboard prints; the alias goes out instead."""
    with dashboard(tmp_path) as ui:
        feed(ui.stack.pipeline, HOT)
        bodies = [
            ui.get("/api/state").body,
            ui.get("/api/utterances").body,
            ui.post("/api/control/ac", {"power": "on"}).body,
            ui.post("/api/control/preflight").body,
        ]

    for body in bodies:
        assert POD not in body


def test_transcript_text_is_served_but_never_logged_or_written(tmp_path, capsys) -> None:
    """Criterion 3: the marker transcript appears in the utterances endpoint
    and nowhere else -- not stdout, not stderr, not a file."""
    with dashboard(tmp_path) as ui:
        marked = f"{HOT} {MARKER_TEXT}"
        feed(ui.stack.pipeline, marked)

        assert MARKER_TEXT in ui.get("/api/utterances").body
        assert MARKER_TEXT not in ui.get("/api/state").body
        assert MARKER_TEXT not in ui.get("/").body

    captured = capsys.readouterr()
    assert MARKER_TEXT not in captured.out
    assert MARKER_TEXT not in captured.err

    leaked = [
        path
        for path in tmp_path.rglob("*")
        if path.is_file() and MARKER_TEXT in path.read_text(encoding="utf-8", errors="ignore")
    ]
    assert leaked == []

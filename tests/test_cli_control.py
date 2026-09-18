"""Tests for :mod:`shabbos_goy.cli._commands._control`.

No microphone, no real listener, no real network beyond a fake HTTP server on
``127.0.0.1`` (ephemeral port).
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from shabbos_goy.cli._commands import _control
from shabbos_goy.config import Config
from tests.decider_fake_server import closed_port

FIXTURE_CONFIG = Path(__file__).parent / "fixtures" / "pipeline" / "config.json"


class _FakeControlServer:
    """A tiny scripted JSON-over-HTTP server standing in for the (planned)
    listener control endpoint -- GET/POST, one canned response per path."""

    def __init__(self) -> None:
        self.responses: dict[str, dict] = {}
        self.requests: list[dict] = []
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> "_FakeControlServer":
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"

            def _handle(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                body = json.loads(raw) if raw else None
                outer.requests.append({"path": self.path, "method": self.command, "body": body})
                canned = outer.responses.get(self.path, {})
                payload = json.dumps(canned).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            do_GET = _handle
            do_POST = _handle

            def log_message(self, *args) -> None:
                return

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(
            target=self._httpd.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True
        )
        self._thread.start()
        return self

    @property
    def base_url(self) -> str:
        assert self._httpd is not None
        host, port = self._httpd.server_address[0], self._httpd.server_address[1]
        return f"http://{host}:{port}"

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()

    def __enter__(self) -> "_FakeControlServer":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()


def _config() -> Config:
    return Config(path=FIXTURE_CONFIG, raw={}, error=None)


def test_resolve_base_url_env_override_wins() -> None:
    url = _control.resolve_base_url(_config(), env={"SHABBOS_GOY_CONTROL_URL": "http://x:1/"})
    assert url == "http://x:1"


def test_resolve_base_url_from_config_dashboard_address() -> None:
    config = Config(path=FIXTURE_CONFIG, raw={"dashboard_bind_address": "127.0.0.1:9999"})
    assert _control.resolve_base_url(config, env={}) == "http://127.0.0.1:9999"


def test_resolve_base_url_default() -> None:
    expected = f"http://{_control.DEFAULT_CONTROL_ADDRESS}"
    assert _control.resolve_base_url(_config(), env={}) == expected


def test_get_json_round_trip() -> None:
    with _FakeControlServer() as server:
        server.responses["/mode"] = {"mode": "weekday"}
        result = _control.get_json(server.base_url, "/mode")
        assert result.ok
        assert result.data == {"mode": "weekday"}


def test_post_json_round_trip() -> None:
    with _FakeControlServer() as server:
        server.responses["/mode"] = {"mode": "strict"}
        result = _control.post_json(server.base_url, "/mode", {"override": "strict"})
        assert result.ok
        assert result.data == {"mode": "strict"}
        assert server.requests[0]["method"] == "POST"
        assert server.requests[0]["body"] == {"override": "strict"}


def test_no_listener_result_on_closed_port() -> None:
    port = closed_port()
    result = _control.get_json(f"http://127.0.0.1:{port}", "/mode", timeout=1.0)
    assert result.ok is False
    assert result.reason == "connect_error"


def test_no_listener_error_names_listen_verb() -> None:
    err = _control.no_listener_error("http://127.0.0.1:8787")
    assert err.code == 2
    assert "8787" in err.message
    assert "shabbos-goy listen" in err.remediation

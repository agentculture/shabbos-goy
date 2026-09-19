"""Tests for ``shabbos-goy preflight``.

No microphone, no real Sensibo/timedatectl/network beyond in-process fakes:
a tiny HTTP server on ``127.0.0.1`` standing in for the lobes ``senses``
role (``/capabilities`` + ``/v1/chat/completions``), and the fake
``wpctl`` already vendored at ``tests/fixtures/fake_pipewire`` on ``PATH``.
Never runs the real ``sensibo`` binary and never sets ``--apply`` anywhere.
"""

from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from shabbos_goy.cli import main
from tests.decider_fake_server import decision_body

FIXTURE_CONFIG = Path(__file__).parent / "fixtures" / "pipeline" / "config.json"
FAKE_PIPEWIRE_DIR = Path(__file__).parent / "fixtures" / "fake_pipewire"


class _FakeLobesServer:
    """Serves both ``GET /capabilities`` (keyless) and
    ``POST /v1/chat/completions`` (the senses decide() round trip)."""

    def __init__(self, *, ready: bool = True, decision_content: bytes | None = None) -> None:
        self.ready = ready
        self.decision_content = decision_content or decision_body("remark", "cool", 0.8)
        self.requests: list[str] = []
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> "_FakeLobesServer":
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"

            def do_GET(self) -> None:  # noqa: N802
                outer.requests.append(self.path)
                if self.path == "/capabilities":
                    body = json.dumps({"senses": {"ready": outer.ready}}).encode("utf-8")
                    self.send_response(200)
                else:
                    body = b"{}"
                    self.send_response(404)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self) -> None:  # noqa: N802
                outer.requests.append(self.path)
                length = int(self.headers.get("Content-Length") or 0)
                self.rfile.read(length) if length else b""
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(outer.decision_content)))
                self.end_headers()
                self.wfile.write(outer.decision_content)

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

    def __enter__(self) -> "_FakeLobesServer":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()


def _prepend_fake_pipewire_to_path(monkeypatch) -> None:
    monkeypatch.setenv("PATH", f"{FAKE_PIPEWIRE_DIR}{os.pathsep}{os.environ.get('PATH', '')}")


def _set_common_env(monkeypatch, server_base_url: str) -> None:
    monkeypatch.setenv("SHABBOS_GOY_CONFIG", str(FIXTURE_CONFIG))
    monkeypatch.setenv("SHABBOS_GOY_LOBES_URL", server_base_url)
    monkeypatch.setenv("SHABBOS_GOY_LOBES_API_KEY", "test-lobes-key")
    monkeypatch.setenv("SENSIBO_API_KEY", "test-sensibo-key")
    _prepend_fake_pipewire_to_path(monkeypatch)


def test_preflight_all_pass(monkeypatch, capsys: pytest.CaptureFixture[str]) -> None:
    with _FakeLobesServer() as server:
        _set_common_env(monkeypatch, server.base_url)
        rc = main(["preflight", "--json"])
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["healthy"] is True
        ids = {c["id"]: c["passed"] for c in payload["checks"]}
        assert ids == {
            "lobes_key": True,
            "lobes_health": True,
            "senses_decide": True,
            "sensibo_key": True,
            "sensibo_pod": True,
            "audio_node": True,
            "clock_sync": True,
            "location": True,
        }
        # never actuates: the fake server saw exactly the read-only
        # capabilities GET and the one decide() POST, nothing else.
        assert server.requests == ["/capabilities", "/v1/chat/completions"]


def test_preflight_missing_sensibo_key_fails_that_one_check(
    monkeypatch, capsys: pytest.CaptureFixture[str]
) -> None:
    with _FakeLobesServer() as server:
        _set_common_env(monkeypatch, server.base_url)
        monkeypatch.delenv("SENSIBO_API_KEY", raising=False)
        rc = main(["preflight", "--json"])
        assert rc == 2
        payload = json.loads(capsys.readouterr().out)
        assert payload["healthy"] is False
        by_id = {c["id"]: c for c in payload["checks"]}
        assert by_id["sensibo_key"]["passed"] is False
        assert by_id["sensibo_key"]["remediation"]
        # every other check that does not depend on the missing var still
        # runs and still passes -- one bad check must not mask the rest.
        assert by_id["sensibo_pod"]["passed"] is True
        assert by_id["lobes_health"]["passed"] is True


def test_preflight_senses_not_ready(monkeypatch, capsys: pytest.CaptureFixture[str]) -> None:
    with _FakeLobesServer(ready=False) as server:
        _set_common_env(monkeypatch, server.base_url)
        rc = main(["preflight", "--json"])
        assert rc == 2
        payload = json.loads(capsys.readouterr().out)
        by_id = {c["id"]: c for c in payload["checks"]}
        assert by_id["lobes_health"]["passed"] is False


def test_preflight_missing_lobes_url_names_the_variable(
    monkeypatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("SHABBOS_GOY_CONFIG", str(FIXTURE_CONFIG))
    monkeypatch.delenv("SHABBOS_GOY_LOBES_URL", raising=False)
    monkeypatch.delenv("SHABBOS_GOY_SENSES_URL", raising=False)
    monkeypatch.delenv("SHABBOS_GOY_LOBES_API_KEY", raising=False)
    monkeypatch.delenv("GATEWAY_API_KEY", raising=False)
    monkeypatch.setenv("SENSIBO_API_KEY", "test-sensibo-key")
    _prepend_fake_pipewire_to_path(monkeypatch)
    rc = main(["preflight", "--json"])
    assert rc == 2
    payload = json.loads(capsys.readouterr().out)
    by_id = {c["id"]: c for c in payload["checks"]}
    assert by_id["lobes_health"]["passed"] is False
    assert "SHABBOS_GOY_LOBES_URL" in by_id["lobes_health"]["message"]
    assert by_id["senses_decide"]["passed"] is False


def test_preflight_never_calls_real_sensibo_or_actuates(
    monkeypatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # `sensibo` is deliberately absent from PATH in this test: if preflight
    # ever shelled out to it, this would fail with a FileNotFoundError
    # instead of a clean, config-only check.
    with _FakeLobesServer() as server:
        _set_common_env(monkeypatch, server.base_url)
        rc = main(["preflight", "--json"])
        assert rc == 0


def test_preflight_text_mode_names_failed_checks(
    monkeypatch, capsys: pytest.CaptureFixture[str]
) -> None:
    with _FakeLobesServer() as server:
        _set_common_env(monkeypatch, server.base_url)
        monkeypatch.delenv("SENSIBO_API_KEY", raising=False)
        rc = main(["preflight"])
        assert rc == 2
        out = capsys.readouterr().out
        assert "healthy: False" in out
        assert "FAIL] sensibo_key" in out
        assert "hint:" in out


# -- the Sensibo key may come from the operator's `grant` secrets manager ----


def _grant_runner(known: set[str], calls: list[list[str]]):
    """A subprocess.run stand-in: answers `grant show NAME --json`, defers the rest."""
    import subprocess as _subprocess

    def run(argv, *args, **kwargs):
        if argv and argv[0] == "grant":
            calls.append(list(argv))
            ok = len(argv) >= 3 and argv[1] == "show" and argv[2] in known
            return _subprocess.CompletedProcess(argv, 0 if ok else 1, stdout="{}", stderr="")
        return _subprocess.run(argv, *args, **kwargs)

    return run


def test_sensibo_key_check_passes_when_grant_holds_the_configured_secret() -> None:
    from shabbos_goy.cli._commands import preflight

    calls: list[list[str]] = []
    result = preflight._check_sensibo_key(
        {}, grant_secret="SENSIBO_API_KEY", runner=_grant_runner({"SENSIBO_API_KEY"}, calls)
    )
    assert result.passed is True
    assert "grant" in result.message
    # `show` prints metadata only; preflight must never ask grant for the VALUE.
    assert calls == [["grant", "show", "SENSIBO_API_KEY", "--json"]]
    assert all(call[1] not in ("get", "env", "run") for call in calls)


def test_sensibo_key_check_fails_when_grant_does_not_hold_the_secret() -> None:
    from shabbos_goy.cli._commands import preflight

    result = preflight._check_sensibo_key(
        {}, grant_secret="SENSIBO_API_KEY", runner=_grant_runner(set(), [])
    )
    assert result.passed is False
    assert "grant set SENSIBO_API_KEY" in result.remediation


def test_sensibo_key_in_the_environment_wins_without_asking_grant() -> None:
    from shabbos_goy.cli._commands import preflight

    calls: list[list[str]] = []
    result = preflight._check_sensibo_key(
        {"SENSIBO_API_KEY": "x"}, grant_secret="SENSIBO_API_KEY", runner=_grant_runner(set(), calls)
    )
    assert result.passed is True
    assert calls == []


def test_sensibo_key_check_without_env_or_grant_names_both_remedies() -> None:
    from shabbos_goy.cli._commands import preflight

    result = preflight._check_sensibo_key({}, grant_secret=None, runner=_grant_runner(set(), []))
    assert result.passed is False
    assert "grant" in result.remediation
    assert "SENSIBO_API_KEY" in result.remediation

"""An in-process fake ``/v1/chat/completions`` server for the decider tests.

Nothing here talks to the real lobes gateway. The server binds ``127.0.0.1``
on an ephemeral port, serves a *scripted* list of responses (status, raw
body bytes, optional pre-response delay) and records every request it saw,
so a test can assert the request shape (model, temperature, bearer header)
without a key, a network or a GPU.

Deliberately raw: the scripted body is ``bytes``, never a dict, so a test
can serve prose, two JSON objects, invalid UTF-8 or a megabyte of padding —
the hostile-output drills — without the fake server "helpfully" fixing them.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


@dataclass
class ScriptedResponse:
    """One reply the fake server will hand out, in script order."""

    status: int = 200
    body: bytes = b"{}"
    content_type: str = "application/json"
    delay_seconds: float = 0.0


@dataclass
class RecordedRequest:
    """What the client actually sent."""

    path: str
    headers: dict[str, str]
    raw_body: bytes

    @property
    def json_body(self) -> Any:
        return json.loads(self.raw_body.decode("utf-8"))


def chat_body(content: str) -> bytes:
    """A well-formed OpenAI-shaped chat completion carrying *content*."""
    return json.dumps({"choices": [{"message": {"role": "assistant", "content": content}}]}).encode(
        "utf-8"
    )


_INTENT_TO_NEED = {
    "cool": "colder",
    "warm": "warmer",
    "quieter": "quieter",
    "louder": "louder",
    "status": "status",
    "none": "none",
}
_NEED_TO_STATE = {"colder": "hot", "warmer": "cold", "quieter": "loud", "louder": "quiet"}


def decision_payload(klass: str, intent: str, confidence: float) -> dict:
    """The wire shape (prompt p2) that the decider maps back to ``intent``.

    Tests speak in this repo's intents; the model speaks in state + need.
    """
    need = _INTENT_TO_NEED.get(intent, intent)
    return {
        "class": klass,
        "state": _NEED_TO_STATE.get(need, "none"),
        "need": need,
        "confidence": confidence,
    }


def decision_body(klass: str, intent: str, confidence: float) -> bytes:
    """A well-formed completion whose content is one decision object."""
    return chat_body(json.dumps(decision_payload(klass, intent, confidence)))


@dataclass
class FakeSensesServer:
    """A scripted chat-completions endpoint on ``127.0.0.1``."""

    responses: list[ScriptedResponse] = field(default_factory=list)
    requests: list[RecordedRequest] = field(default_factory=list)
    _httpd: ThreadingHTTPServer | None = None
    _thread: threading.Thread | None = None

    def start(self) -> "FakeSensesServer":
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"

            def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                outer.requests.append(
                    RecordedRequest(
                        path=self.path,
                        headers={k.lower(): v for k, v in self.headers.items()},
                        raw_body=raw,
                    )
                )
                index = len(outer.requests) - 1
                if index < len(outer.responses):
                    scripted = outer.responses[index]
                elif outer.responses:
                    scripted = outer.responses[-1]
                else:
                    scripted = ScriptedResponse()
                if scripted.delay_seconds:
                    time.sleep(scripted.delay_seconds)
                try:
                    self.send_response(scripted.status)
                    self.send_header("Content-Type", scripted.content_type)
                    self.send_header("Content-Length", str(len(scripted.body)))
                    self.end_headers()
                    self.wfile.write(scripted.body)
                except (BrokenPipeError, ConnectionResetError):  # pragma: no cover
                    pass

            def log_message(self, *args: Any) -> None:  # noqa: A003 - silence stderr
                return

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._httpd.daemon_threads = True
        # A short poll interval keeps shutdown() from costing half a second
        # per server, which matters: these tests start dozens of them.
        self._thread = threading.Thread(
            target=self._httpd.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True
        )
        self._thread.start()
        return self

    @property
    def base_url(self) -> str:
        assert self._httpd is not None, "server not started"
        host, port = self._httpd.server_address[0], self._httpd.server_address[1]
        return f"http://{host}:{port}"

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None

    def __enter__(self) -> "FakeSensesServer":
        return self.start()

    def __exit__(self, *exc: Any) -> None:
        self.stop()


def closed_port() -> int:
    """A port nothing is listening on (bind, read the port, close)."""
    import socket

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port

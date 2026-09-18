"""An in-process fake of the lobes batch audio endpoints, for the golden tests.

Nothing here talks to the real gateway. It binds ``127.0.0.1`` on an ephemeral
port and serves the three endpoints the golden runner uses:

* ``GET  /health``                  -- a scripted JSON body
* ``POST /v1/audio/speech``         -- a JSON body in, WAV bytes out
* ``POST /v1/audio/transcriptions`` -- a **real multipart parse** in, ``{"text"}`` out

The multipart body is parsed with :mod:`email` rather than by looking for our
own boundary, on purpose: if the fake understood only what
:func:`tests.golden.runner.encode_multipart` happens to emit, a body the real
server would reject could still pass here. Same argument as
``tests/lobes_fake_server.py`` framing the WebSocket wire independently.

It is a companion to ``tests/decider_fake_server.py`` (the ``senses``
chat-completions fake), which the golden tests reuse unchanged.
"""

from __future__ import annotations

import io
import json
import threading
import wave
from dataclasses import dataclass, field
from email.parser import BytesParser
from email.policy import default as default_policy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable


def wav_bytes(pcm: bytes = b"\x00\x00" * 1600, rate: int = 24000) -> bytes:
    """A RIFF/WAVE container around mono 16-bit *pcm* -- what TTS returns."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(pcm)
    return buf.getvalue()


@dataclass
class SpeechCall:
    body: Any
    headers: dict[str, str]


@dataclass
class TranscriptionCall:
    fields: dict[str, str]
    filename: str
    content_type: str
    content: bytes
    headers: dict[str, str]


@dataclass
class FakeBatchAudioServer:
    """A scripted ``/v1/audio/*`` + ``/health`` endpoint on ``127.0.0.1``."""

    #: text -> transcript. A text the script does not know transcribes to "".
    transcripts: dict[str, str] = field(default_factory=dict)
    #: Called with the /v1/audio/speech input text; returns the audio body.
    synthesize: Callable[[str], bytes] = field(default=lambda _text: wav_bytes())
    health_body: dict[str, Any] = field(
        default_factory=lambda: {"status": "ok", "service": "model-gear-realtime"}
    )
    speech_status: int = 200
    transcription_status: int = 200
    health_status: int = 200
    speech_calls: list[SpeechCall] = field(default_factory=list)
    transcription_calls: list[TranscriptionCall] = field(default_factory=list)
    health_calls: int = 0
    _httpd: ThreadingHTTPServer | None = None
    _thread: threading.Thread | None = None

    # -- lifecycle --------------------------------------------------------
    def start(self) -> "FakeBatchAudioServer":
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"

            def _reply(self, status: int, body: bytes, content_type: str) -> None:
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
                if self.path != "/health":
                    self._reply(404, b"{}", "application/json")
                    return
                outer.health_calls += 1
                self._reply(
                    outer.health_status,
                    json.dumps(outer.health_body).encode("utf-8"),
                    "application/json",
                )

            def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                headers = {k.lower(): v for k, v in self.headers.items()}
                if self.path == "/v1/audio/speech":
                    outer._on_speech(self, raw, headers)
                elif self.path == "/v1/audio/transcriptions":
                    outer._on_transcription(self, raw, headers)
                else:
                    self._reply(404, b"{}", "application/json")

            def log_message(self, *args: Any) -> None:  # noqa: A003 - silence stderr
                return

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._httpd.daemon_threads = True
        self._thread = threading.Thread(
            target=self._httpd.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True
        )
        self._thread.start()
        return self

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None

    def __enter__(self) -> "FakeBatchAudioServer":
        return self.start()

    def __exit__(self, *exc: Any) -> None:
        self.stop()

    @property
    def base_url(self) -> str:
        assert self._httpd is not None, "server not started"
        host, port = self._httpd.server_address[0], self._httpd.server_address[1]
        return f"http://{host}:{port}"

    # -- handlers ---------------------------------------------------------
    def _on_speech(self, handler: Any, raw: bytes, headers: dict[str, str]) -> None:
        try:
            body = json.loads(raw.decode("utf-8"))
        except ValueError:
            body = None
        self.speech_calls.append(SpeechCall(body=body, headers=headers))
        if self.speech_status != 200:
            handler._reply(self.speech_status, b'{"error": "nope"}', "application/json")
            return
        text = body.get("input", "") if isinstance(body, dict) else ""
        handler._reply(200, self.synthesize(text), "audio/wav")

    def _on_transcription(self, handler: Any, raw: bytes, headers: dict[str, str]) -> None:
        fields, filename, content_type, content = parse_multipart(
            headers.get("content-type", ""), raw
        )
        self.transcription_calls.append(
            TranscriptionCall(
                fields=fields,
                filename=filename,
                content_type=content_type,
                content=content,
                headers=headers,
            )
        )
        if self.transcription_status != 200:
            handler._reply(self.transcription_status, b'{"error": "nope"}', "application/json")
            return
        text = self.transcripts.get(_wav_marker(content), "")
        handler._reply(200, json.dumps({"text": text}).encode("utf-8"), "application/json")


def _wav_marker(content: bytes) -> str:
    """The key a scripted transcript is looked up by: the WAV's payload bytes.

    Tests synthesise a distinct PCM payload per utterance, so the fake can
    map audio back to the text it stands for without decoding speech.
    """
    try:
        with wave.open(io.BytesIO(content), "rb") as handle:
            payload = handle.readframes(handle.getnframes())
    except (wave.Error, EOFError, ValueError):
        payload = content
    # marker_wav pads an odd-length payload to a whole 16-bit frame.
    return payload.decode("utf-8", errors="replace").rstrip()


def marker_wav(text: str, rate: int = 24000) -> bytes:
    """A WAV whose PCM payload literally *is* ``text`` -- a test stand-in for TTS."""
    pcm = text.encode("utf-8")
    if len(pcm) % 2:
        pcm += b" "
    return wav_bytes(pcm, rate=rate)


def parse_multipart(content_type: str, body: bytes) -> tuple[dict[str, str], str, str, bytes]:
    """Parse a multipart/form-data body with :mod:`email`.

    Returns ``(plain fields, file filename, file content type, file bytes)``.
    """
    head = f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("utf-8")
    message = BytesParser(policy=default_policy).parsebytes(head + body)
    fields: dict[str, str] = {}
    filename = ""
    file_type = ""
    content = b""
    if not message.is_multipart():
        raise AssertionError("body did not parse as multipart/form-data")
    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition")
        part_filename = part.get_param("filename", header="content-disposition")
        payload = part.get_payload(decode=True) or b""
        if part_filename:
            filename = str(part_filename)
            file_type = part.get_content_type()
            content = payload
        else:
            fields[str(name)] = payload.decode("utf-8")
    return fields, filename, file_type, content

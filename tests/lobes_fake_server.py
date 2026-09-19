"""An in-process fake lobes ``/v1/realtime`` WebSocket server for tests.

No network beyond ``127.0.0.1``, no lobes server, no audio hardware. The
framing here is written INDEPENDENTLY of ``shabbos_goy.lobes.ws`` on
purpose: if both sides shared one implementation, a wire bug would cancel
itself out and the contract tests would prove nothing.

Scripted behaviours a test can ask for:

* reply to the opening handshake with any HTTP status (``401`` for the
  auth-failure drill, ``101`` otherwise);
* send server events (JSON text frames) and PINGs;
* record every frame the client sent (opcode + payload), so a test can
  assert PONGs came back and that nothing but
  ``input_audio_buffer.append`` was ever sent;
* hard-kill the connection mid-turn (``abort``), or freeze it (accept the
  socket, send nothing at all) for the watchdog drill.
"""

from __future__ import annotations

import base64
import hashlib
import json
import socket
import struct
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

OPCODE_TEXT = 0x1
OPCODE_BINARY = 0x2
OPCODE_CLOSE = 0x8
OPCODE_PING = 0x9
OPCODE_PONG = 0xA


def server_accept_key(client_key: str) -> str:
    """RFC 6455 SS4.2.2 accept key (SHA-1 is protocol-mandated here)."""
    digest = hashlib.sha1(  # nosec B324 - RFC 6455 handshake, not a security use
        (client_key + WS_GUID).encode("ascii")
    ).digest()
    return base64.b64encode(digest).decode("ascii")


def build_server_frame(opcode: int, payload: bytes = b"") -> bytes:
    """One unmasked server->client frame (RFC 6455 SS5.1: servers never mask)."""
    header = bytearray([0x80 | (opcode & 0x0F)])
    length = len(payload)
    if length < 126:
        header.append(length)
    elif length < 65536:
        header.append(126)
        header += struct.pack("!H", length)
    else:
        header.append(127)
        header += struct.pack("!Q", length)
    return bytes(header) + payload


@dataclass
class ReceivedFrame:
    opcode: int
    payload: bytes

    @property
    def text(self) -> str:
        return self.payload.decode("utf-8", errors="replace")

    def json(self) -> Any:
        return json.loads(self.text)


@dataclass
class Connection:
    """One accepted client connection, as the script sees it."""

    sock: socket.socket
    request_head: bytes
    frames: list[ReceivedFrame] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    closed: threading.Event = field(default_factory=threading.Event)

    # -- outbound ---------------------------------------------------------
    def send_event(self, event: dict) -> None:
        self._send(OPCODE_TEXT, json.dumps(event).encode("utf-8"))

    def send_ping(self, payload: bytes = b"keepalive") -> None:
        self._send(OPCODE_PING, payload)

    def send_close(self, code: int = 1000) -> None:
        self._send(OPCODE_CLOSE, struct.pack("!H", code))

    def _send(self, opcode: int, payload: bytes) -> None:
        with self._lock:
            try:
                self.sock.sendall(build_server_frame(opcode, payload))
            except OSError:
                pass

    def abort(self) -> None:
        """Kill the TCP connection outright — the 'server died' drill."""
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass
        self.closed.set()

    # -- inbound ----------------------------------------------------------
    def snapshot(self) -> list[ReceivedFrame]:
        with self._lock:
            return list(self.frames)

    def sent_events(self) -> list[Any]:
        return [f.json() for f in self.snapshot() if f.opcode == OPCODE_TEXT]

    def wait_for(
        self,
        predicate: Callable[[list[ReceivedFrame]], bool],
        timeout: float = 5.0,
    ) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate(self.snapshot()):
                return True
            time.sleep(0.005)
        return predicate(self.snapshot())

    def _record(self, frame: ReceivedFrame) -> None:
        with self._lock:
            self.frames.append(frame)


class FakeLobesServer:
    """A one-thread-per-connection fake realtime server bound to 127.0.0.1:0."""

    def __init__(
        self,
        script: Callable[["FakeLobesServer", Connection], None] | None = None,
        *,
        handshake_status: int = 101,
        handshake_body: bytes = b"",
    ) -> None:
        self._script = script
        self._handshake_status = handshake_status
        self._handshake_body = handshake_body
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(8)
        self.host, self.port = self._sock.getsockname()
        self.connections: list[Connection] = []
        self.connection_count = threading.Semaphore(0)
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._accept_thread = threading.Thread(target=self._accept_loop, daemon=True)

    # -- lifecycle --------------------------------------------------------
    def start(self) -> "FakeLobesServer":
        self._accept_thread.start()
        return self

    def __enter__(self) -> "FakeLobesServer":
        return self.start()

    def __exit__(self, *exc: object) -> None:
        self.stop()

    def stop(self) -> None:
        self._stop.set()
        try:
            self._sock.close()
        except OSError:
            pass
        for conn in list(self.connections):
            conn.abort()
        for thread in list(self._threads):
            thread.join(timeout=2.0)

    def wait_for_connection(self, index: int = 0, timeout: float = 5.0) -> Connection:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if len(self.connections) > index:
                return self.connections[index]
            time.sleep(0.005)
        raise AssertionError(f"no connection #{index} within {timeout}s")

    # -- internals --------------------------------------------------------
    def _accept_loop(self) -> None:
        while not self._stop.is_set():
            try:
                client, _addr = self._sock.accept()
            except OSError:
                return
            thread = threading.Thread(target=self._serve, args=(client,), daemon=True)
            self._threads.append(thread)
            thread.start()

    def _serve(self, client: socket.socket) -> None:
        buf = bytearray()
        try:
            while b"\r\n\r\n" not in buf:
                chunk = client.recv(65536)
                if not chunk:
                    client.close()
                    return
                buf.extend(chunk)
        except OSError:
            client.close()
            return
        idx = buf.index(b"\r\n\r\n") + 4
        head = bytes(buf[:idx])
        del buf[:idx]

        if self._handshake_status != 101:
            reason = {401: "Unauthorized", 403: "Forbidden", 426: "Upgrade Required"}.get(
                self._handshake_status, "Error"
            )
            response = (
                f"HTTP/1.1 {self._handshake_status} {reason}\r\n"
                f"Content-Length: {len(self._handshake_body)}\r\n"
                "Connection: close\r\n\r\n"
            ).encode("latin-1") + self._handshake_body
            try:
                client.sendall(response)
            finally:
                client.close()
            conn = Connection(sock=client, request_head=head)
            conn.closed.set()
            self.connections.append(conn)
            return

        key = ""
        for line in head.decode("latin-1").split("\r\n"):
            if line.lower().startswith("sec-websocket-key:"):
                key = line.split(":", 1)[1].strip()
        response = (
            "HTTP/1.1 101 Switching Protocols\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Accept: {server_accept_key(key)}\r\n\r\n"
        ).encode("latin-1")
        client.sendall(response)

        conn = Connection(sock=client, request_head=head)
        self.connections.append(conn)
        self.connection_count.release()

        reader = threading.Thread(target=self._read_loop, args=(conn, buf), daemon=True)
        reader.start()
        try:
            if self._script is not None:
                self._script(self, conn)
        finally:
            # A script that returns without aborting leaves the socket open —
            # the "frozen server" drill depends on that, so wait for the test
            # to tear the server down rather than closing here.
            self._stop.wait(timeout=30.0)
            conn.abort()
            reader.join(timeout=1.0)

    @staticmethod
    def _read_loop(conn: Connection, buf: bytearray) -> None:
        sock = conn.sock

        def recv_exact(n: int) -> bytes:
            while len(buf) < n:
                try:
                    chunk = sock.recv(max(4096, n))
                except OSError:
                    return b""
                if not chunk:
                    return b""
                buf.extend(chunk)
            data = bytes(buf[:n])
            del buf[:n]
            return data

        while not conn.closed.is_set():
            header = recv_exact(2)
            if len(header) < 2:
                conn.closed.set()
                return
            opcode = header[0] & 0x0F
            masked = bool(header[1] & 0x80)
            length = header[1] & 0x7F
            if length == 126:
                length = struct.unpack("!H", recv_exact(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", recv_exact(8))[0]
            mask_key = recv_exact(4) if masked else b""
            payload = recv_exact(length) if length else b""
            if masked and len(mask_key) == 4:
                payload = bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload))
            conn._record(ReceivedFrame(opcode=opcode, payload=payload))
            if opcode == OPCODE_CLOSE:
                conn.closed.set()
                return

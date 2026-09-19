"""A minimal stdlib RFC 6455 WebSocket client — the wire layer only.

CITATION (cite-don't-import, see docs/skill-sources.md)
-------------------------------------------------------
Cited from ``agentculture/lobes-cli`` branch ``spec/hebrew-realtime``,
``scripts/realtime-smoke.py`` (the "Pure helpers" and ``WebSocketClient``
sections), with ``scripts/realtime-he-accept.py`` as the duplex reference.
The handshake framing, the accept-key computation, the mask/unmask XOR, the
frame build/parse and the ``select``-not-``settimeout`` reading discipline
are that file's, adapted here: renamed to this package's conventions,
narrowed to what an ears-only listener needs (no base64 audio codec, no
phrase matching, no CLI), and given TLS support for a ``wss://`` gateway.
We copy rather than depend for the same reason lobes-cli hand-rolls it:
this package's ``dependencies`` stay ``[]``.

This module knows nothing about lobes, sessions, Shabbat or transcripts —
that is ``client.py``'s job. It speaks frames.
"""

from __future__ import annotations

import base64
import hashlib
import os
import re
import select
import socket
import ssl
import struct
import threading
from typing import Callable

WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"  # RFC 6455 SS4.2.2, fixed by spec.

OPCODE_CONTINUATION = 0x0
OPCODE_TEXT = 0x1
OPCODE_BINARY = 0x2
OPCODE_CLOSE = 0x8
OPCODE_PING = 0x9
OPCODE_PONG = 0xA


#: The largest payload a single frame may declare, in bytes. A peer-provided
#: 64-bit length is attacker-controlled input: without a bound, one malformed
#: (or compromised) endpoint could make the reader allocate its way through
#: the host's memory. 1 MiB is ~30 s of 16 kHz PCM16 and far more than any
#: real lobes event, so nothing legitimate ever approaches it.
DEFAULT_MAX_PAYLOAD_BYTES = 1024 * 1024


class FrameReadError(Exception):
    """The frame stream ended (EOF) or was malformed before a full frame arrived."""


class FrameTooLarge(FrameReadError):
    """The peer declared a payload larger than the configured maximum.

    A subclass of :class:`FrameReadError` on purpose: the client already
    treats a frame-read failure as a lost connection (reconnect with
    backoff), which is exactly the right response to a peer that is talking
    nonsense. Raised *before* the payload is read, so nothing is allocated.
    """


class HandshakeError(Exception):
    """The opening handshake completed but the peer's accept key did not verify."""


# ---------------------------------------------------------------------------
# Pure helpers — no socket, unit-tested in tests/test_lobes_ws.py.
# ---------------------------------------------------------------------------


def make_sec_websocket_key() -> str:
    """A fresh, random base64-encoded 16-byte nonce (RFC 6455 SS4.1)."""
    return base64.b64encode(os.urandom(16)).decode("ascii")


def compute_accept_key(sec_websocket_key: str) -> str:
    """RFC 6455 SS4.2.2: base64(sha1(key + the fixed WebSocket GUID)).

    SHA-1 here is the protocol-mandated handshake check, not a security
    boundary — RFC 6455 requires exactly this algorithm.
    """
    digest = hashlib.sha1(  # nosec B324 - RFC 6455-mandated, not a security use
        (sec_websocket_key + WS_GUID).encode("ascii")
    ).digest()
    return base64.b64encode(digest).decode("ascii")


def build_handshake_request(
    host: str, path: str, key: str, extra_headers: dict[str, str] | None = None
) -> bytes:
    """Serialise the WebSocket opening handshake (RFC 6455 SS4.1) as raw bytes."""
    lines = [
        f"GET {path} HTTP/1.1",
        f"Host: {host}",
        "Upgrade: websocket",
        "Connection: Upgrade",
        f"Sec-WebSocket-Key: {key}",
        "Sec-WebSocket-Version: 13",
    ]
    for name, value in (extra_headers or {}).items():
        lines.append(f"{name}: {value}")
    return ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1")


def parse_response_head(head: bytes) -> tuple[int, dict[str, str]]:
    """Parse a raw HTTP response head into ``(status_code, lowercased_headers)``."""
    text = head.decode("latin-1", errors="replace")
    lines = [line for line in text.split("\r\n") if line]
    status = 0
    if lines:
        match = re.match(r"HTTP/\d\.\d\s+(\d+)", lines[0])
        if match:
            status = int(match.group(1))
    headers: dict[str, str] = {}
    for line in lines[1:]:
        if ":" not in line:
            continue
        name, _, value = line.partition(":")
        headers[name.strip().lower()] = value.strip()
    return status, headers


def mask_payload(payload: bytes, mask_key: bytes) -> bytes:
    """XOR-mask (or unmask — the operation is its own inverse) *payload*."""
    if len(mask_key) != 4:
        raise ValueError("mask_key must be exactly 4 bytes")
    return bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload))


def build_frame(opcode: int, payload: bytes = b"", *, mask: bool = True) -> bytes:
    """One complete, unfragmented RFC 6455 SS5.2 frame (FIN always set).

    ``mask=True`` (the default, and the only mode this client ever sends):
    every client-to-server frame MUST be masked per RFC 6455 SS5.1.
    """
    header = bytearray()
    # FIN bit set, no reserved bits, then the opcode's low nibble.
    header.append(0x80 | (opcode & 0x0F))
    length = len(payload)
    mask_bit = 0x80 if mask else 0x00
    if length < 126:
        header.append(mask_bit | length)
    elif length < 65536:
        header.append(mask_bit | 126)
        header += struct.pack("!H", length)
    else:
        header.append(mask_bit | 127)
        header += struct.pack("!Q", length)
    if mask:
        mask_key = os.urandom(4)
        header += mask_key
        payload = mask_payload(payload, mask_key)
    return bytes(header) + payload


def _read_extended_length(recv_exact: Callable[[int], bytes], length: int) -> int:
    """Resolve the 7-bit length field, reading its 16- or 64-bit extension."""
    if length == 126:
        ext = recv_exact(2)
        if len(ext) < 2:
            raise FrameReadError("connection closed while reading the 16-bit extended length")
        return struct.unpack("!H", ext)[0]
    if length == 127:
        ext = recv_exact(8)
        if len(ext) < 8:
            raise FrameReadError("connection closed while reading the 64-bit extended length")
        return struct.unpack("!Q", ext)[0]
    return length


def read_frame(
    recv_exact: Callable[[int], bytes],
    *,
    max_payload_bytes: int = DEFAULT_MAX_PAYLOAD_BYTES,
) -> tuple[bool, int, bytes]:
    """Read one frame using ``recv_exact(n) -> bytes``.

    Raises :class:`FrameReadError` on EOF or a short read, so a truncated
    stream can never be mistaken for a valid short frame. ``recv_exact`` is
    any callable of one int argument, which is what makes this testable
    against a plain :class:`io.BytesIO` fed pre-built frames.

    A declared payload above *max_payload_bytes* raises
    :class:`FrameTooLarge` **before** a single payload byte is requested:
    the length field comes from the peer, so it is never a size to trust.
    """
    first_two = recv_exact(2)
    if len(first_two) < 2:
        raise FrameReadError("connection closed before a frame header arrived")
    b0, b1 = first_two[0], first_two[1]
    fin = bool(b0 & 0x80)
    opcode = b0 & 0x0F
    masked = bool(b1 & 0x80)
    length = _read_extended_length(recv_exact, b1 & 0x7F)
    if length > max_payload_bytes:
        # Named, and raised before any buffer is sized from it.
        raise FrameTooLarge(
            f"peer declared a {length}-byte payload, above the "
            f"{max_payload_bytes}-byte frame limit: refusing to allocate it"
        )
    mask_key = None
    if masked:
        mask_key = recv_exact(4)
        if len(mask_key) < 4:
            raise FrameReadError("connection closed while reading the mask key")
    payload = recv_exact(length) if length else b""
    if length and len(payload) < length:
        raise FrameReadError("connection closed before the full payload arrived")
    if masked and mask_key is not None:
        payload = mask_payload(payload, mask_key)
    return fin, opcode, payload


# ---------------------------------------------------------------------------
# Socket-owning glue — exercised in tests/test_lobes_client.py against the
# in-process fake server, never against a real deployment.
# ---------------------------------------------------------------------------


class WebSocketConnection:
    """One RFC 6455 connection: handshake, masked writes, framed reads."""

    def __init__(
        self,
        sock: socket.socket,
        *,
        max_payload_bytes: int = DEFAULT_MAX_PAYLOAD_BYTES,
    ) -> None:
        self._sock = sock
        self._buf = bytearray()
        self._send_lock = threading.Lock()
        self.max_payload_bytes = int(max_payload_bytes)

    @classmethod
    def connect(
        cls,
        host: str,
        port: int,
        path: str,
        *,
        extra_headers: dict[str, str] | None = None,
        tls: bool = False,
        connect_timeout: float = 10.0,
        max_payload_bytes: int = DEFAULT_MAX_PAYLOAD_BYTES,
    ) -> tuple["WebSocketConnection", int, dict[str, str]]:
        """Dial the peer and perform the WS handshake.

        Returns ``(connection, status_code, response_headers)``. A non-101
        *status_code* is a clean refusal (auth gate, wrong route, wrong
        protocol) and is returned, not raised — the caller decides what a
        401 means. Transport failures raise ``OSError``.
        """
        key = make_sec_websocket_key()
        sock = socket.create_connection((host, port), timeout=connect_timeout)
        if tls:
            context = ssl.create_default_context()
            sock = context.wrap_socket(sock, server_hostname=host)
        conn = cls(sock, max_payload_bytes=max_payload_bytes)
        request = build_handshake_request(f"{host}:{port}", path, key, extra_headers=extra_headers)
        sock.sendall(request)
        head = conn._read_until(b"\r\n\r\n", timeout=connect_timeout)
        status, headers = parse_response_head(head)
        if status == 101:
            expected = compute_accept_key(key)
            got = headers.get("sec-websocket-accept", "")
            if got != expected:
                conn.close()
                raise HandshakeError(
                    "Sec-WebSocket-Accept mismatch — refusing to trust this handshake"
                )
            sock.settimeout(None)
        return conn, status, headers

    # -- reading ----------------------------------------------------------
    def _read_until(self, marker: bytes, timeout: float) -> bytes:
        while marker not in self._buf:
            ready, _, _ = select.select([self._sock], [], [], timeout)
            if not ready:
                raise socket.timeout("timed out during the handshake")
            chunk = self._sock.recv(65536)
            if not chunk:
                raise ConnectionError("connection closed during the handshake")
            self._buf.extend(chunk)
        idx = self._buf.index(marker) + len(marker)
        head = bytes(self._buf[:idx])
        del self._buf[:idx]
        return head

    def _recv_exact(self, n: int, timeout: float | None = None) -> bytes:
        """Read up to *n* buffered-or-arriving bytes, waiting via ``select``.

        NOT ``settimeout``: a socket timeout is a property of the SOCKET, so
        a reader setting it races with a concurrent ``sendall`` from the
        audio thread — the writer inherits the reader's deadline and a large
        write can time out part-sent, desynchronising the peer's parser.
        ``select`` waits for readability without touching socket state.
        """
        while len(self._buf) < n:
            if timeout is not None:
                ready, _, _ = select.select([self._sock], [], [], timeout)
                if not ready:
                    raise socket.timeout("timed out waiting for data")
            chunk = self._sock.recv(max(65536, n))
            if not chunk:
                break
            self._buf.extend(chunk)
        take = min(n, len(self._buf))
        data = bytes(self._buf[:take])
        del self._buf[:take]
        return data

    def read_frame(self, timeout: float | None = None) -> tuple[bool, int, bytes]:
        """Read one frame, leaving the buffer intact if it times out mid-frame.

        A frame needs 2-4 separate reads and each can time out. Without the
        push-back below, a timeout after the header was consumed would drop
        those bytes and the retry would mis-parse everything after it.
        """
        consumed = bytearray()

        def reader(n: int) -> bytes:
            data = self._recv_exact(n, timeout=timeout)
            consumed.extend(data)
            return data

        try:
            return read_frame(reader, max_payload_bytes=self.max_payload_bytes)
        except socket.timeout:
            self._buf[:0] = consumed
            raise

    # -- writing ----------------------------------------------------------
    def send_frame(self, opcode: int, payload: bytes = b"") -> None:
        frame = build_frame(opcode, payload, mask=True)
        with self._send_lock:
            self._sock.sendall(frame)

    def send_text(self, text: str) -> None:
        self.send_frame(OPCODE_TEXT, text.encode("utf-8"))

    def send_pong(self, payload: bytes = b"") -> None:
        self.send_frame(OPCODE_PONG, payload)

    def send_close(self, code: int = 1000) -> None:
        try:
            self.send_frame(OPCODE_CLOSE, struct.pack("!H", code))
        except OSError:
            pass

    def close(self) -> None:
        try:
            self._sock.close()
        except OSError:
            pass

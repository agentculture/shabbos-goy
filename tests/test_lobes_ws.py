"""Wire-layer tests for the stdlib RFC 6455 client (``shabbos_goy.lobes.ws``).

Pure framing only: no socket, no server, no audio. The socket-owning parts
are exercised in ``tests/test_lobes_client.py`` against the in-process fake
server.
"""

from __future__ import annotations

import io
import socket
import struct

import pytest

from shabbos_goy.lobes import ws


def test_the_wire_layer_records_where_it_was_cited_from() -> None:
    """Cite-don't-import: the provenance must be in the file, not in memory."""
    doc = ws.__doc__ or ""
    assert "CITATION" in doc
    assert "lobes-cli" in doc
    assert "scripts/realtime-smoke.py" in doc


def test_compute_accept_key_matches_the_rfc6455_worked_example() -> None:
    # RFC 6455 SS1.3's own example key/accept pair.
    assert ws.compute_accept_key("dGhlIHNhbXBsZSBub25jZQ==") == "s3pPLMBiTxaQ9kYGzzhZRbK+xOo="


def test_make_sec_websocket_key_is_16_random_base64_bytes() -> None:
    import base64

    keys = {ws.make_sec_websocket_key() for _ in range(5)}
    assert len(keys) == 5
    for key in keys:
        assert len(base64.b64decode(key)) == 16


def test_build_handshake_request_carries_the_upgrade_headers_and_auth() -> None:
    raw = ws.build_handshake_request(
        "example.invalid:8001",
        "/v1/realtime?language=he",
        "a-key",
        extra_headers={"Authorization": "Bearer secret"},
    ).decode("latin-1")
    assert raw.startswith("GET /v1/realtime?language=he HTTP/1.1\r\n")
    assert "Host: example.invalid:8001\r\n" in raw
    assert "Upgrade: websocket\r\n" in raw
    assert "Connection: Upgrade\r\n" in raw
    assert "Sec-WebSocket-Key: a-key\r\n" in raw
    assert "Sec-WebSocket-Version: 13\r\n" in raw
    assert "Authorization: Bearer secret\r\n" in raw
    assert raw.endswith("\r\n\r\n")


def test_parse_response_head_extracts_status_and_lowercased_headers() -> None:
    head = b"HTTP/1.1 401 Unauthorized\r\nContent-Length: 0\r\nWWW-Authenticate: Bearer\r\n\r\n"
    status, headers = ws.parse_response_head(head)
    assert status == 401
    assert headers["content-length"] == "0"
    assert headers["www-authenticate"] == "Bearer"


def test_client_frames_are_masked_and_the_mask_actually_changes_the_bytes() -> None:
    payload = b"\x00" * 8
    frame = ws.build_frame(ws.OPCODE_TEXT, payload, mask=True)
    assert frame[0] == 0x81  # FIN + text
    assert frame[1] & 0x80, "RFC 6455 SS5.1 requires client->server masking"
    assert frame[1] & 0x7F == len(payload)
    mask_key = frame[2:6]
    body = frame[6:]
    assert body != payload, "an all-zero payload must come out as the mask key repeated"
    assert ws.mask_payload(body, mask_key) == payload


@pytest.mark.parametrize("size", [0, 5, 125, 126, 300, 70000])
def test_build_frame_and_read_frame_round_trip_at_every_length_boundary(size: int) -> None:
    payload = bytes((i % 251 for i in range(size)))
    frame = ws.build_frame(ws.OPCODE_BINARY, payload, mask=True)
    stream = io.BytesIO(frame)
    fin, opcode, decoded = ws.read_frame(stream.read)
    assert fin is True
    assert opcode == ws.OPCODE_BINARY
    assert decoded == payload


def test_read_frame_parses_an_unmasked_server_frame() -> None:
    payload = b'{"type": "session.created"}'
    frame = bytes([0x81, len(payload)]) + payload
    fin, opcode, decoded = ws.read_frame(io.BytesIO(frame).read)
    assert (fin, opcode, decoded) == (True, ws.OPCODE_TEXT, payload)


def test_read_frame_raises_frame_read_error_on_a_truncated_stream() -> None:
    payload = b"hello"
    frame = bytes([0x82, len(payload)]) + payload
    truncated_read = io.BytesIO(frame[:-2]).read
    with pytest.raises(ws.FrameReadError):
        ws.read_frame(truncated_read)
    empty_read = io.BytesIO(b"").read
    with pytest.raises(ws.FrameReadError):
        ws.read_frame(empty_read)


def test_read_frame_handles_the_64_bit_extended_length_header() -> None:
    payload = b"x" * 70000
    frame = bytes([0x82, 127]) + struct.pack("!Q", len(payload)) + payload
    fin, opcode, decoded = ws.read_frame(io.BytesIO(frame).read)
    assert (fin, opcode) == (True, ws.OPCODE_BINARY)
    assert decoded == payload


def test_mask_payload_rejects_a_wrong_sized_key() -> None:
    with pytest.raises(ValueError):
        ws.mask_payload(b"abc", b"12")


# ---------------------------------------------------------------------------
# A peer-declared payload length is untrusted input (review thread #5).
# ---------------------------------------------------------------------------


def test_read_frame_refuses_a_payload_above_the_limit_without_allocating() -> None:
    """A 64-bit length from the peer must be checked BEFORE it sizes a buffer.

    The header below claims 4 GiB and is followed by nothing at all: a reader
    that trusted it would try to grow a buffer toward that size. This one
    refuses on the header, so ``recv_exact`` is never asked for the payload.
    """
    header = bytes([0x82, 127]) + struct.pack("!Q", 4 * 1024**3)
    asked: list[int] = []
    stream = io.BytesIO(header)

    def recv_exact(n: int) -> bytes:
        asked.append(n)
        return stream.read(n)

    with pytest.raises(ws.FrameTooLarge):
        ws.read_frame(recv_exact)
    assert max(asked) <= 8, f"the oversized length was used to size a read: {asked}"
    # A connection failure, so the client reconnects with backoff rather than
    # treating it as a readable frame.
    assert issubclass(ws.FrameTooLarge, ws.FrameReadError)


def test_the_frame_limit_is_configurable_and_defaults_to_one_mebibyte() -> None:
    assert ws.DEFAULT_MAX_PAYLOAD_BYTES == 1024 * 1024
    payload = b"y" * 300
    frame = bytes([0x82, 126]) + struct.pack("!H", len(payload)) + payload
    short_read = io.BytesIO(frame).read
    with pytest.raises(ws.FrameTooLarge):
        ws.read_frame(short_read, max_payload_bytes=299)
    fin, _opcode, decoded = ws.read_frame(io.BytesIO(frame).read, max_payload_bytes=300)
    assert (fin, decoded) == (True, payload)


def test_a_connection_carries_its_frame_limit_into_read_frame() -> None:
    conn = ws.WebSocketConnection(socket.socket(), max_payload_bytes=64)
    try:
        assert conn.max_payload_bytes == 64
        conn._buf.extend(bytes([0x82, 126]) + struct.pack("!H", 65) + b"z" * 65)
        with pytest.raises(ws.FrameTooLarge):
            conn.read_frame()
    finally:
        conn.close()

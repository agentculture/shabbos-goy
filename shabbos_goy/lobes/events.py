"""The lobes ``/v1/realtime`` event vocabulary, as this listener sees it.

CITATION (cite-don't-import): the type and error-code lists mirror
``agentculture/lobes-cli`` branch ``spec/hebrew-realtime``,
``site/src/scripts/realtime-events.ts`` (``EVENT_TYPES`` / ``ERROR_CODES``,
themselves a hand-mirror of ``lobes/realtime/_session.py``). One
representative payload per type and per code is cited into
``tests/fixtures/lobes/event-fixtures.json`` from that repo's
``site/src/scripts/event-fixtures.ts``. Nothing is imported at runtime; the
fixture replay test is what notices if lobes' wire drifts.

Normalising happens here, so ``client.py`` never has to know the wire's
field names and the pipeline downstream never has to know the wire at all.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

# --- wire vocabulary -------------------------------------------------------

EVENT_TYPES: tuple[str, ...] = (
    "session.created",
    "session.closed",
    "input_audio_buffer.speech_started",
    "input_audio_buffer.speech_stopped",
    "conversation.item.input_audio_transcription.completed",
    "error",
    "response.created",
    "response.text.done",
    "response.audio.delta",
    "response.done",
    "response.interrupted",
    "session.updated",
    "response.function_call_arguments.done",
)

ERROR_CODES: tuple[str, ...] = (
    "invalid_session_config",
    "vad_unavailable",
    "invalid_wire_event",
    "stt_forward_failed",
    "generate_failed",
    "tts_failed",
    "response_timeout",
)

TYPE_SESSION_CREATED = "session.created"
TYPE_SESSION_CLOSED = "session.closed"
TYPE_SPEECH_STARTED = "input_audio_buffer.speech_started"
TYPE_SPEECH_STOPPED = "input_audio_buffer.speech_stopped"
TYPE_TRANSCRIPT = "conversation.item.input_audio_transcription.completed"
TYPE_ERROR = "error"

#: Everything the conversation surface emits. This listener is ears-only, so
#: it never asks for any of these — but a shared deployment, a server-side
#: default change or a future conversational mode could still deliver one,
#: and an ignored event must never become an action.
IGNORED_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "session.updated",
        "response.created",
        "response.text.done",
        "response.audio.delta",
        "response.done",
        "response.interrupted",
        "response.function_call_arguments.done",
    }
)

# --- normalised kinds this package emits ----------------------------------

KIND_SESSION_CREATED = "session_created"
KIND_SESSION_CLOSED = "session_closed"
KIND_SPEECH_STARTED = "speech_started"
KIND_SPEECH_STOPPED = "speech_stopped"
KIND_TRANSCRIPT = "transcript"
KIND_ERROR = "error"
KIND_IGNORED = "ignored"
KIND_UNKNOWN = "unknown"
KIND_CONNECTION_LOST = "connection_lost"
KIND_STALLED = "stalled"

_KIND_BY_TYPE: dict[str, str] = {
    TYPE_SESSION_CREATED: KIND_SESSION_CREATED,
    TYPE_SESSION_CLOSED: KIND_SESSION_CLOSED,
    TYPE_SPEECH_STARTED: KIND_SPEECH_STARTED,
    TYPE_SPEECH_STOPPED: KIND_SPEECH_STOPPED,
    TYPE_TRANSCRIPT: KIND_TRANSCRIPT,
    TYPE_ERROR: KIND_ERROR,
}


@dataclass(frozen=True)
class LobesEvent:
    """One normalised event handed to the listener's callback.

    ``text`` carries transcript text: it is in-memory only and must never be
    logged or persisted (CLAUDE.md, Privacy). ``raw`` is kept so a consumer
    can inspect a field this dataclass does not model yet.
    """

    kind: str
    type: str = ""
    item_id: str | None = None
    text: str = ""
    at_ms: int | None = None
    reason: str | None = None
    code: str | None = None
    message: str = ""
    turn_interrupted: bool = False
    raw: Mapping[str, Any] = field(default_factory=dict)


def _as_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _as_str(value: Any) -> str:
    return value if isinstance(value, str) else ""


def normalize_event(payload: Mapping[str, Any]) -> LobesEvent:
    """Turn one wire event into a :class:`LobesEvent`.

    An unknown ``type`` — a lobes release newer than this fixture set, or a
    typo on the wire — becomes :data:`KIND_UNKNOWN`, never an exception: the
    listen loop must survive anything the server says.
    """
    wire_type = _as_str(payload.get("type"))
    if wire_type in IGNORED_EVENT_TYPES:
        kind = KIND_IGNORED
    else:
        kind = _KIND_BY_TYPE.get(wire_type, KIND_UNKNOWN)
    item_id = payload.get("item_id")
    return LobesEvent(
        kind=kind,
        type=wire_type,
        item_id=item_id if isinstance(item_id, str) else None,
        text=_as_str(payload.get("text")),
        at_ms=_as_int(payload.get("at_ms")),
        reason=payload.get("reason") if isinstance(payload.get("reason"), str) else None,
        code=payload.get("code") if isinstance(payload.get("code"), str) else None,
        message=_as_str(payload.get("message")),
        raw=payload,
    )


def is_known_error_code(code: str | None) -> bool:
    """True when *code* is one this repo has a cited fixture for."""
    return code in ERROR_CODES

"""The Gemma-backed decider: one HTTP call to the lobes ``senses`` role.

Deviation d1: each utterance plus the rolling context goes to the lobes
senses role, which returns a structured *label*. This module's entire job is
to ask for that label and to distrust the answer. It never acts, never
retries (the pipeline owns retry policy -- a missed hint is cheap), and
never raises into its caller: every failure, from a refused connection to a
model that decided to write an essay, comes back as
:data:`~shabbos_goy.decider.NO_DECISION` with a named reason code.

Three rules hold the safety argument together:

1. **Ears-only stays ears-only.** No tool is declared here and nothing asks
   the model to act; the response is data, never a call.
2. **The body is untrusted input.** It came from a language model that heard
   a human through a speech recogniser. It is size-capped, decoded strictly,
   parsed as exactly one JSON object and validated field by field against
   :data:`shabbos_goy.policy.CLASSES` and :data:`INTENTS`. Nothing is
   evaluated, executed or followed.
3. **Nothing leaks.** The utterance, the context and the model's raw text
   never appear in a repr, an exception message or any log line; the API key
   never appears anywhere but the outgoing header.

Stdlib only (``urllib``), keeping the package's ``dependencies = []``.
"""

from __future__ import annotations

import contextlib
import json
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Mapping
from urllib.parse import urlsplit, urlunsplit

from ..lobes.config import ENV_API_KEY, ENV_API_KEY_FALLBACK, ENV_URL, LobesConfigError
from ..policy import CLASSES
from .context import ContextWindow
from .decision import INTENTS, Decision, no_decision
from .prompt import PROMPT_VERSION, SYSTEM_PROMPT

#: Optional override: the senses endpoint when it is not the lobes host.
ENV_SENSES_URL = "SHABBOS_GOY_SENSES_URL"
ENV_SENSES_MODEL = "SHABBOS_GOY_SENSES_MODEL"
ENV_SENSES_TIMEOUT = "SHABBOS_GOY_SENSES_TIMEOUT"

CHAT_PATH = "/v1/chat/completions"
DEFAULT_MODEL = "senses"
DEFAULT_TIMEOUT_SECONDS = 8.0
DEFAULT_MAX_TOKENS = 64
#: A label is tens of bytes. Anything past this is not an answer.
DEFAULT_MAX_BODY_BYTES = 64 * 1024

_ALLOWED_KEYS = frozenset({"class", "intent", "confidence"})

_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "json_schema",
    "json_schema": {
        "name": "utterance_label",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "class": {"type": "string", "enum": list(CLASSES)},
                "intent": {"type": "string", "enum": list(INTENTS)},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            },
            "required": ["class", "intent", "confidence"],
            "additionalProperties": False,
        },
    },
}


@dataclass(frozen=True)
class SensesConfig:
    """Where the senses role lives and how patient we are with it."""

    base_url: str
    api_key: str | None = None
    model: str = DEFAULT_MODEL
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    max_tokens: int = DEFAULT_MAX_TOKENS
    max_body_bytes: int = DEFAULT_MAX_BODY_BYTES

    @property
    def endpoint(self) -> str:
        """A log-safe description of the peer -- never includes the key."""
        return f"{self.base_url}{CHAT_PATH}"

    def __repr__(self) -> str:
        key_state = "set" if self.api_key else "unset"
        return (
            f"SensesConfig(endpoint={self.endpoint!r}, model={self.model!r}, "
            f"timeout_seconds={self.timeout_seconds}, api_key=<{key_state}>)"
        )

    __str__ = __repr__


def _normalise_base_url(raw: str, *, variable: str) -> str:
    """``ws(s)://host:port/...`` or ``http(s)://...`` -> ``http(s)://host:port``."""
    try:
        parsed = urlsplit(raw.strip())
        port = parsed.port
    except ValueError as exc:
        raise LobesConfigError(f"{variable} is malformed: {exc}") from exc
    scheme = parsed.scheme.lower()
    mapping = {"ws": "http", "http": "http", "wss": "https", "https": "https"}
    if scheme not in mapping:
        raise LobesConfigError(
            f"{variable} must start with ws://, wss://, http:// or https://, got {raw!r}"
        )
    if not parsed.hostname:
        raise LobesConfigError(f"{variable} has no host")
    netloc = parsed.hostname if port is None else f"{parsed.hostname}:{port}"
    return urlunsplit((mapping[scheme], netloc, "", "", ""))


def senses_config_from_env(env: Mapping[str, str]) -> SensesConfig:
    """Build a :class:`SensesConfig` from *env* (usually ``os.environ``).

    The host is the lobes host by default -- the senses role is served by the
    same gateway -- with :data:`ENV_SENSES_URL` as an override for a split
    deployment. There is no default host, port or key anywhere in this file.
    """
    override = (env.get(ENV_SENSES_URL) or "").strip()
    if override:
        base_url = _normalise_base_url(override, variable=ENV_SENSES_URL)
    else:
        raw = (env.get(ENV_URL) or "").strip()
        if not raw:
            raise LobesConfigError(
                f"{ENV_URL} is not set (or set {ENV_SENSES_URL}): the senses host comes "
                "from the environment only, never from a tracked file"
            )
        base_url = _normalise_base_url(raw, variable=ENV_URL)

    timeout_text = (env.get(ENV_SENSES_TIMEOUT) or "").strip()
    timeout = DEFAULT_TIMEOUT_SECONDS
    if timeout_text:
        try:
            timeout = float(timeout_text)
        except ValueError as exc:
            raise LobesConfigError(
                f"{ENV_SENSES_TIMEOUT} must be a number, got {timeout_text!r}"
            ) from exc
        if timeout <= 0:
            raise LobesConfigError(f"{ENV_SENSES_TIMEOUT} must be > 0")

    return SensesConfig(
        base_url=base_url,
        api_key=env.get(ENV_API_KEY) or env.get(ENV_API_KEY_FALLBACK) or None,
        model=(env.get(ENV_SENSES_MODEL) or DEFAULT_MODEL).strip() or DEFAULT_MODEL,
        timeout_seconds=timeout,
    )


@dataclass
class GemmaDecider:
    """Labels one utterance by asking the lobes ``senses`` role."""

    config: SensesConfig
    _structured: bool = field(default=True, init=False, repr=False)

    def __post_init__(self) -> None:
        # Validate once, loudly, at construction: a bad URL is an operator
        # error (exit code 2), not something to discover mid-Shabbat.
        self.config = SensesConfig(
            base_url=_normalise_base_url(self.config.base_url, variable="base_url"),
            api_key=self.config.api_key,
            model=self.config.model,
            timeout_seconds=self.config.timeout_seconds,
            max_tokens=self.config.max_tokens,
            max_body_bytes=self.config.max_body_bytes,
        )

    @property
    def source(self) -> str:
        return f"gemma:{PROMPT_VERSION}"

    def __repr__(self) -> str:
        return f"GemmaDecider(config={self.config!r}, structured={self._structured})"

    __str__ = __repr__

    # -- the one public method -------------------------------------------

    def decide(
        self,
        utterance: str,
        context: ContextWindow,
        *,
        mode: str,
        ac_state: dict | None = None,
    ) -> Decision:
        """Label *utterance*. Never raises; every failure is NO_DECISION."""
        user_message = self._user_message(utterance, context, mode, ac_state)
        outcome = self._post(user_message, structured=self._structured)
        if outcome.reason == "http_400" and self._structured:
            # The server rejected the structured-output request. Fall back
            # once, and remember it for this process's lifetime.
            self._structured = False
            outcome = self._post(user_message, structured=False)
        if outcome.content is None:
            return no_decision(outcome.reason, source=self.source)
        return self._parse(outcome.content)

    # -- request ----------------------------------------------------------

    def _user_message(
        self,
        utterance: str,
        context: ContextWindow,
        mode: str,
        ac_state: dict | None,
    ) -> str:
        rendered = context.render()
        parts = [f"mode: {mode}"]
        if ac_state is not None:
            parts.append(f"ac_state: {json.dumps(ac_state, sort_keys=True)}")
        if rendered:
            parts.append("recent:\n" + rendered)
        parts.append("utterance:\n" + (utterance or ""))
        return "\n".join(parts)

    def _payload(self, user_message: str, *, structured: bool) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.config.model,
            "temperature": 0,
            "max_tokens": self.config.max_tokens,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ],
        }
        if structured:
            payload["response_format"] = _RESPONSE_SCHEMA
        return payload

    def _post(self, user_message: str, *, structured: bool) -> "_Outcome":
        body = json.dumps(self._payload(user_message, structured=structured)).encode("utf-8")
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        request = urllib.request.Request(  # nosec B310 - scheme checked in _normalise_base_url
            self.config.endpoint, data=body, headers=headers, method="POST"
        )
        try:
            with urllib.request.urlopen(  # nosec B310 - http(s) only, see above
                request, timeout=self.config.timeout_seconds
            ) as response:
                raw = response.read(self.config.max_body_bytes + 1)
        except urllib.error.HTTPError as exc:
            with contextlib.suppress(OSError, ValueError):
                exc.read(1)  # drain, best-effort; the body is never inspected
            return _Outcome(reason=f"http_{exc.code}")
        except socket.timeout:
            return _Outcome(reason="timeout")
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, socket.timeout):
                return _Outcome(reason="timeout")
            return _Outcome(reason="connect_error")
        except (OSError, ValueError, json.JSONDecodeError):
            # Never let a transport detail (which can quote the peer or the
            # body) escape as an exception into the caller.
            return _Outcome(reason="connect_error")

        if len(raw) > self.config.max_body_bytes:
            return _Outcome(reason="body_too_large")
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            return _Outcome(reason="bad_encoding")
        try:
            envelope = json.loads(text)
        except ValueError:
            return _Outcome(reason="bad_envelope")
        if not isinstance(envelope, dict):
            return _Outcome(reason="bad_envelope")
        choices = envelope.get("choices")
        if not isinstance(choices, list) or not choices:
            return _Outcome(reason="no_choices")
        first = choices[0]
        if not isinstance(first, dict):
            return _Outcome(reason="no_choices")
        message = first.get("message")
        if not isinstance(message, dict):
            return _Outcome(reason="no_choices")
        content = message.get("content")
        if not isinstance(content, str):
            return _Outcome(reason="no_choices")
        return _Outcome(content=content)

    # -- response, treated as hostile input -------------------------------

    def _parse(self, content: str) -> Decision:
        payload = _one_json_object(content)
        if payload is None:
            return no_decision("bad_payload", source=self.source)
        if set(payload) - _ALLOWED_KEYS:
            # An action list, a tool call, a chain of thought: anything the
            # model added beyond the three fields invalidates the answer.
            return no_decision("extra_keys", source=self.source)
        if not _ALLOWED_KEYS <= set(payload):
            return no_decision("missing_field", source=self.source)
        klass = payload["class"]
        intent = payload["intent"]
        confidence = payload["confidence"]
        if not isinstance(klass, str) or klass not in CLASSES:
            return no_decision("bad_class", source=self.source)
        if not isinstance(intent, str) or intent not in INTENTS:
            return no_decision("bad_intent", source=self.source)
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
            return no_decision("bad_confidence", source=self.source)
        if not 0.0 <= float(confidence) <= 1.0:
            return no_decision("bad_confidence", source=self.source)
        return Decision(
            klass=klass,
            intent=intent,
            confidence=float(confidence),
            source=self.source,
            reason="ok",
        )


@dataclass(frozen=True)
class _Outcome:
    """Either a body to parse, or a reason there is none. Carries no text."""

    content: str | None = None
    reason: str = "ok"


def _one_json_object(content: str) -> dict[str, Any] | None:
    """Exactly one JSON object, optionally fenced or padded with whitespace.

    Two objects, an object with prose in front of it, an array, a scalar or
    an empty string are all rejected: the contract is one object and nothing
    else, and a model that cannot keep to it is not one to act on.
    """
    text = content.strip()
    if text.startswith("```"):
        newline = text.find("\n")
        if newline == -1 or not text.endswith("```"):
            return None
        text = text[newline + 1 : -3].strip()
    if not text.startswith("{"):
        return None
    try:
        value, end = json.JSONDecoder().raw_decode(text)
    except ValueError:
        return None
    if text[end:].strip():
        return None
    if not isinstance(value, dict):
        return None
    return value

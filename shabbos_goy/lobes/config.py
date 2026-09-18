"""Where the lobes session's host and key come from: the environment, only.

Nothing here has a default host, a default port or a default key. A missing
or malformed value is a named environment error, so an operator sees which
variable is wrong instead of watching the listener dial nowhere. The key is
never put in ``repr``/``str`` — Docker keeps container stdout.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping
from urllib.parse import urlencode, urlsplit

ENV_URL = "SHABBOS_GOY_LOBES_URL"
ENV_API_KEY = "SHABBOS_GOY_LOBES_API_KEY"
ENV_API_KEY_FALLBACK = "GATEWAY_API_KEY"  # lobes' own deployment variable
ENV_LANGUAGE = "SHABBOS_GOY_LOBES_LANGUAGE"
ENV_SAMPLE_RATE = "SHABBOS_GOY_LOBES_SAMPLE_RATE"

REALTIME_PATH = "/v1/realtime"
DEFAULT_LANGUAGE = "he"
DEFAULT_SAMPLE_RATE = 16000
SUPPORTED_SAMPLE_RATES = (16000, 24000)

_TLS_SCHEMES = {"wss", "https"}
_PLAIN_SCHEMES = {"ws", "http"}


class LobesConfigError(Exception):
    """A required environment variable is missing or malformed (exit code 2)."""


@dataclass(frozen=True)
class LobesConfig:
    """Everything needed to dial one ears-only realtime session."""

    host: str
    port: int
    tls: bool = False
    api_key: str | None = None
    language: str = DEFAULT_LANGUAGE
    input_sample_rate: int = DEFAULT_SAMPLE_RATE

    @property
    def realtime_path(self) -> str:
        """The ears-only query string: language, rate, server VAD. No tools.

        There is deliberately no way to add a tool declaration or a
        ``response.create`` here: the session is opened as a pair of ears
        and the wire layer refuses anything else (see ``client.py``).
        """
        query = urlencode(
            {
                "language": self.language,
                "input_sample_rate": self.input_sample_rate,
                "turn_detection": "server_vad",
            }
        )
        return f"{REALTIME_PATH}?{query}"

    def handshake_headers(self) -> dict[str, str]:
        """Bearer header, and only when a key is configured."""
        if not self.api_key:
            return {}
        return {"Authorization": f"Bearer {self.api_key}"}

    @property
    def endpoint(self) -> str:
        """A log-safe description of the peer — never includes the key."""
        scheme = "wss" if self.tls else "ws"
        return f"{scheme}://{self.host}:{self.port}{REALTIME_PATH}"

    def __repr__(self) -> str:  # pragma: no cover - exercised via str()
        key_state = "set" if self.api_key else "unset"
        return (
            f"LobesConfig(endpoint={self.endpoint!r}, language={self.language!r}, "
            f"input_sample_rate={self.input_sample_rate}, api_key=<{key_state}>)"
        )

    __str__ = __repr__


def config_from_env(env: Mapping[str, str]) -> LobesConfig:
    """Build a :class:`LobesConfig` from *env* (usually ``os.environ``)."""
    raw_url = (env.get(ENV_URL) or "").strip()
    if not raw_url:
        raise LobesConfigError(
            f"{ENV_URL} is not set: the lobes host comes from the environment only "
            "(set it in the gitignored env file, never in a tracked file)"
        )
    try:
        parsed = urlsplit(raw_url)
        port = parsed.port
    except ValueError as exc:
        raise LobesConfigError(f"{ENV_URL} is malformed: {exc}") from exc
    scheme = parsed.scheme.lower()
    if scheme not in _TLS_SCHEMES | _PLAIN_SCHEMES:
        raise LobesConfigError(
            f"{ENV_URL} must start with ws://, wss://, http:// or https://, got {raw_url!r}"
        )
    if not parsed.hostname:
        raise LobesConfigError(f"{ENV_URL} has no host: {raw_url!r}")
    tls = scheme in _TLS_SCHEMES
    if port is None:
        port = 443 if tls else 80

    rate_text = (env.get(ENV_SAMPLE_RATE) or "").strip()
    rate = DEFAULT_SAMPLE_RATE
    if rate_text:
        try:
            rate = int(rate_text)
        except ValueError as exc:
            raise LobesConfigError(
                f"{ENV_SAMPLE_RATE} must be an integer, got {rate_text!r}"
            ) from exc
        if rate not in SUPPORTED_SAMPLE_RATES:
            raise LobesConfigError(
                f"{ENV_SAMPLE_RATE}={rate} is not supported; lobes accepts "
                f"{SUPPORTED_SAMPLE_RATES}"
            )

    api_key = env.get(ENV_API_KEY) or env.get(ENV_API_KEY_FALLBACK) or None
    language = (env.get(ENV_LANGUAGE) or DEFAULT_LANGUAGE).strip() or DEFAULT_LANGUAGE

    return LobesConfig(
        host=parsed.hostname,
        port=port,
        tls=tls,
        api_key=api_key,
        language=language,
        input_sample_rate=rate,
    )

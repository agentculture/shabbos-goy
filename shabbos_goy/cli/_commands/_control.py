"""Client for the listener's (planned) local control endpoint.

The listener (the ``listen`` verb, task t14) is the one process holding the
in-memory mode override (:mod:`shabbos_goy.mode`), the rate limiter, and the
real actuator wiring. CLI verbs that would otherwise duplicate that state --
``mode set``/``mode show``, ``ac status``/``power``, ``volume get``/``set`` --
talk to it instead over a small JSON-over-HTTP control endpoint on localhost,
so the CLI never bypasses the listener's whitelist/rate-limit/delay gates by
calling an adapter directly.

The endpoint does not exist yet -- ``listen`` is a later task. Every function
here fails the same, clear way when nothing answers: :func:`no_listener_error`
builds a :class:`CliError` (exit 2) naming the address and pointing at
``shabbos-goy listen``. Tests point ``base_url`` at a small in-process fake
HTTP server on ``127.0.0.1`` instead (see ``tests/test_cli_control.py``);
nothing here ever guesses at a default remote host.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Mapping, Optional

from shabbos_goy.cli._errors import EXIT_ENV_ERROR, CliError
from shabbos_goy.config import Config

#: Full base URL override, e.g. ``http://127.0.0.1:8787`` -- highest priority,
#: and the only thing tests need to set to point at a fake server.
ENV_CONTROL_URL = "SHABBOS_GOY_CONTROL_URL"

#: Falls back to the config's (planned) dashboard bind address, and then to
#: this literal default -- matching ``tests/fixtures/config.example.json``.
DEFAULT_CONTROL_ADDRESS = "127.0.0.1:8787"

DEFAULT_TIMEOUT_SECONDS = 3.0

LISTENER_REMEDIATION = (
    "start the listener with 'shabbos-goy listen' (task t14); "
    "'shabbos-goy mode show' also works with no listener running"
)


@dataclass(frozen=True)
class ControlResult:
    """One control-endpoint round trip. Never raises -- ``ok`` says whether
    the endpoint answered with a well-formed JSON body."""

    ok: bool
    data: Any = None
    reason: str = ""


def resolve_base_url(config: Config, *, env: Optional[Mapping[str, str]] = None) -> str:
    """The control endpoint base URL: env override > config > default.

    Never touches the network -- this only resolves an address string.
    """
    env = os.environ if env is None else env
    override = (env.get(ENV_CONTROL_URL) or "").strip()
    if override:
        return override.rstrip("/")
    address = config.dashboard_bind_address
    if isinstance(address, str) and address.strip():
        address = address.strip()
        if "://" in address:
            return address.rstrip("/")
        return f"http://{address}"
    return f"http://{DEFAULT_CONTROL_ADDRESS}"


def _request(
    base_url: str,
    path: str,
    *,
    method: str,
    payload: Optional[Mapping[str, Any]],
    timeout: float,
) -> ControlResult:
    url = base_url.rstrip("/") + path
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(  # nosec B310 - http(s) to a locally-resolved address only
            request, timeout=timeout
        ) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        return ControlResult(ok=False, reason=f"http_{exc.code}")
    except urllib.error.URLError:
        return ControlResult(ok=False, reason="connect_error")
    except (OSError, ValueError):
        return ControlResult(ok=False, reason="connect_error")
    if not raw:
        return ControlResult(ok=True, data={})
    try:
        decoded = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return ControlResult(ok=False, reason="bad_response")
    return ControlResult(ok=True, data=decoded)


def get_json(
    base_url: str, path: str, *, timeout: float = DEFAULT_TIMEOUT_SECONDS
) -> ControlResult:
    return _request(base_url, path, method="GET", payload=None, timeout=timeout)


def post_json(
    base_url: str,
    path: str,
    payload: Mapping[str, Any],
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> ControlResult:
    return _request(base_url, path, method="POST", payload=payload, timeout=timeout)


def no_listener_error(base_url: str) -> CliError:
    """A named :class:`CliError` (exit 2) for "nothing answered at base_url"."""
    return CliError(
        code=EXIT_ENV_ERROR,
        message=f"no listener answering the control endpoint at {base_url}",
        remediation=LISTENER_REMEDIATION,
    )

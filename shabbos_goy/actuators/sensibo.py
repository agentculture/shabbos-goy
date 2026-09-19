"""Sensibo adapter: argv-locked power control and zero-write status read.

This module is the **only** place shabbos-goy talks to sensibo-cli, and it
only ever talks to its command-line surface — never ``import sensibo`` and
never the Sensibo HTTP API directly. Every argv this module can produce comes
from a closed set of flags (see ``_set_argv`` / ``_read_argv``): ``--power
on|off``, ``--apply`` and ``--json``. No other sensibo flag (``--mode``,
``--target``, ``--fan``, ``--swing``, ``--all``, ...) is ever built here, so
this adapter cannot drift into controlling anything beyond power.

Two entry points:

* :func:`power` — dry-run by default (matching sensibo-cli's own "write verbs
  are dry-run by default" contract); pass ``apply=True`` to actually commit.
* :func:`status` — **never** passes ``--apply``. It derives the pod's current
  on/off state from the ``changes`` diff of a dry-run ``sensibo set --power
  on`` (the ``"from"`` side of that diff *is* the current value, whether or
  not a change would be needed), and merges in ``temperature``/``humidity``
  from a separate ``sensibo read``. It is intentionally kept as one function
  so sensibo-cli#15 (a prospective single ``status`` verb) can replace both
  subprocess calls with one, without shabbos-goy's callers noticing.

Never log the pod id at info level: this module does not use the logging
module at all, so that risk does not exist here by construction.
"""

from __future__ import annotations

import json
import os
import re
import subprocess  # nosec B404 - argv-locked, no shell, see module docstring
from typing import Any, Literal, Mapping

SENSIBO_EXECUTABLE = "sensibo"

# Sensibo pod ids are short alphanumerics. Enforcing this here — before any
# argv is built, in the one place both builders route through — is what
# actually makes "the only sensibo flags are --power, --apply and --json"
# true: without it, a pod id of "--apply" or "--mode" (sourced from a config
# file, a dashboard, or a CLI argument) would itself become a sensibo flag.
_POD_ID_RE = re.compile(r"^[A-Za-z0-9]{1,64}$")


def _validate_pod_id(pod_id: object) -> str:
    """Return ``pod_id`` unchanged if it is a safe argv token; raise otherwise."""
    if not isinstance(pod_id, str) or not _POD_ID_RE.match(pod_id):
        raise ValueError(
            f"invalid sensibo pod id: {pod_id!r} "
            "(must match ^[A-Za-z0-9]{1,64}$ — no flags, whitespace, or path characters)"
        )
    return pod_id


# -- the key: injected by the operator's secrets manager, never held here -----
#
# ``grant run --inject VAR=NAME -- cmd`` (the AgentCulture per-user secrets
# manager) forks, sets VAR from the operator's store and execvp's cmd, so the
# child's stdout and exit code are sensibo's own and the key never enters this
# process, a config file or a compose env file. The wrapper is a CLOSED prefix
# around the locked argv above; the secret NAME comes from config and is
# validated as strictly as a pod id, because it too becomes an argv token.
GRANT_EXECUTABLE = "grant"
SENSIBO_KEY_ENV = "SENSIBO_API_KEY"
_GRANT_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")


def _validate_grant_secret(name: object) -> str | None:
    """``None`` means "no grant injection"; anything else must be a safe name."""
    if name is None:
        return None
    if not isinstance(name, str) or not _GRANT_NAME_RE.match(name):
        raise ValueError(
            f"invalid grant secret name: {name!r} (must match ^[A-Z][A-Z0-9_]{{0,63}}$)"
        )
    return name


def _with_grant(
    argv: list[str], grant_secret: object, *, env: Mapping[str, str] | None = None
) -> list[str]:
    """Prefix ``argv`` with the closed ``grant run --inject`` wrapper when configured.

    A key that is already in the environment wins (the container's env file,
    an operator's shell): grant is then not involved at all.
    """
    name = _validate_grant_secret(grant_secret)
    environ = os.environ if env is None else env
    if name is None or environ.get(SENSIBO_KEY_ENV):
        return argv
    return [GRANT_EXECUTABLE, "run", "--inject", f"{SENSIBO_KEY_ENV}={name}", "--", *argv]


def _validate_power_on(on: object) -> bool:
    """Return ``on`` unchanged if it is strictly a bool; raise otherwise."""
    if not isinstance(on, bool):
        raise ValueError(f"invalid power value: {on!r} (must be a bool, not a truthy string)")
    return on


# Above sensibo-cli's own single-retry ceiling: sensibo/api/client.py caps any
# one 429 backoff sleep (computed exponential backoff, or a server-sent
# Retry-After, whichever is larger) at 120s (`_MAX_RETRY_DELAY`). We wait
# longer than that single ceiling so a legitimate in-progress retry inside
# sensibo-cli is never mistaken for a hang by this adapter.
_TIMEOUT_SECONDS = 130.0

PowerState = Literal["on", "off", "unknown"]


# -- argv builders: the closed set -------------------------------------------


def _set_argv(pod_id: str, *, power_on: bool, apply: bool) -> list[str]:
    """Build the argv for a ``sensibo set`` call. Only flags: --power, --apply, --json."""
    safe_pod_id = _validate_pod_id(pod_id)
    argv = [
        SENSIBO_EXECUTABLE,
        "set",
        safe_pod_id,
        "--power",
        "on" if power_on else "off",
    ]
    if apply:
        argv.append("--apply")
    argv.append("--json")
    return argv


def _read_argv(pod_id: str) -> list[str]:
    """Build the argv for a ``sensibo read`` call. Only flag: --json."""
    safe_pod_id = _validate_pod_id(pod_id)
    return [SENSIBO_EXECUTABLE, "read", safe_pod_id, "--json"]


# -- subprocess plumbing ------------------------------------------------------


def _run(argv: list[str]) -> subprocess.CompletedProcess[str] | None:
    """Run argv with a locked-down subprocess call; ``None`` on any failure to run it."""
    try:
        return subprocess.run(  # nosec B603 - argv is list-built above, no shell
            argv,
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        # subprocess.TimeoutExpired derives from SubprocessError, so it is
        # already covered here.
        return None


def _parse_json(proc: subprocess.CompletedProcess[str] | None) -> dict[str, Any] | None:
    if proc is None or proc.returncode != 0:
        return None
    try:
        payload = json.loads(proc.stdout)
    except ValueError:
        # json.JSONDecodeError derives from ValueError, so it is already
        # covered here.
        return None
    return payload if isinstance(payload, dict) else None


# -- power: dry-run by default ------------------------------------------------


def power(
    pod_id: str, on: bool, *, apply: bool = False, grant_secret: str | None = None
) -> dict[str, Any]:
    """Turn ``pod_id``'s AC on/off. Dry-run unless ``apply=True``.

    Any non-zero exit (or a subprocess that never returns) is treated as
    "did not act" — never assumed to have succeeded.
    """
    safe_on = _validate_power_on(on)
    argv = _set_argv(pod_id, power_on=safe_on, apply=apply)
    proc = _run(_with_grant(argv, grant_secret))
    payload = _parse_json(proc)
    if payload is None:
        return {"acted": False, "requested_apply": apply, "changes": {}}

    acted = bool(apply and payload.get("applied"))
    return {
        "acted": acted,
        "requested_apply": apply,
        "changes": payload.get("changes", {}),
    }


# -- status: zero-write read ---------------------------------------------------


def _power_from_set_payload(set_payload: dict[str, Any] | None) -> PowerState:
    """Derive the pod's current power state from a dry-run ``set`` payload.

    The diff's ``"from"`` value on the ``on`` field is the pod's actual
    current state whether or not a change is proposed. If the dry-run
    reports no diff at all, the pod already matches the requested ``on``
    value (i.e. it is already on). Any payload that failed to parse degrades
    to ``"unknown"`` rather than guessing.
    """
    if set_payload is None:
        return "unknown"
    changes = set_payload.get("changes")
    if not isinstance(changes, dict):
        return "unknown"
    on_change = changes.get("on")
    if isinstance(on_change, dict) and "from" in on_change:
        return "on" if on_change["from"] else "off"
    # No diff for `on`: the pod already matches the requested value
    # (on=True), i.e. it is already on.
    return "on"


def _readings_from_read_payload(
    read_payload: dict[str, Any] | None,
) -> tuple[Any, Any]:
    """Derive ``(temperature, humidity)`` from a ``read`` payload, or ``(None, None)``."""
    if read_payload is None:
        return None, None
    readings = read_payload.get("readings")
    if not isinstance(readings, dict):
        return None, None
    return readings.get("temperature"), readings.get("humidity")


def status(pod_id: str, *, grant_secret: str | None = None) -> dict[str, Any]:
    """Current power state plus temperature/humidity for ``pod_id``. Never writes.

    ``power`` is derived from a dry-run ``sensibo set --power on`` (never
    ``--apply``); see :func:`_power_from_set_payload`. Any non-zero exit, at
    either subprocess call, degrades that piece to ``"unknown"``/``None``
    rather than guessing.
    """
    set_argv = _set_argv(pod_id, power_on=True, apply=False)
    if "--apply" in set_argv:
        # Load-bearing: status() must never write. An assert would vanish
        # under `python -O`, silently dropping this guarantee, so this is an
        # explicit, always-on check instead.
        raise RuntimeError("status() built an argv containing --apply; refusing to run it")

    set_payload = _parse_json(_run(_with_grant(set_argv, grant_secret)))
    read_payload = _parse_json(_run(_with_grant(_read_argv(pod_id), grant_secret)))
    temperature, humidity = _readings_from_read_payload(read_payload)

    return {
        "power": _power_from_set_payload(set_payload),
        "temperature": temperature,
        "humidity": humidity,
    }

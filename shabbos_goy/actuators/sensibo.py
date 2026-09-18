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
import subprocess  # nosec B404 - argv-locked, no shell, see module docstring
from typing import Any, Literal

SENSIBO_EXECUTABLE = "sensibo"

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
    argv = [
        SENSIBO_EXECUTABLE,
        "set",
        pod_id,
        "--power",
        "on" if power_on else "off",
    ]
    if apply:
        argv.append("--apply")
    argv.append("--json")
    return argv


def _read_argv(pod_id: str) -> list[str]:
    """Build the argv for a ``sensibo read`` call. Only flag: --json."""
    return [SENSIBO_EXECUTABLE, "read", pod_id, "--json"]


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
    except (OSError, subprocess.TimeoutExpired, subprocess.SubprocessError):
        return None


def _parse_json(proc: subprocess.CompletedProcess[str] | None) -> dict[str, Any] | None:
    if proc is None or proc.returncode != 0:
        return None
    try:
        payload = json.loads(proc.stdout)
    except (json.JSONDecodeError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


# -- power: dry-run by default ------------------------------------------------


def power(pod_id: str, on: bool, *, apply: bool = False) -> dict[str, Any]:
    """Turn ``pod_id``'s AC on/off. Dry-run unless ``apply=True``.

    Any non-zero exit (or a subprocess that never returns) is treated as
    "did not act" — never assumed to have succeeded.
    """
    argv = _set_argv(pod_id, power_on=on, apply=apply)
    proc = _run(argv)
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


def status(pod_id: str) -> dict[str, Any]:
    """Current power state plus temperature/humidity for ``pod_id``. Never writes.

    ``power`` is derived from a dry-run ``sensibo set --power on`` (never
    ``--apply``): the diff's ``"from"`` value on the ``on`` field is the
    pod's actual current state whether or not a change is proposed. If the
    dry-run reports no diff at all, the pod already matches the requested
    ``on`` value, i.e. it is already on. Any non-zero exit, at either
    subprocess call, degrades that piece to ``"unknown"``/``None`` rather
    than guessing.
    """
    result: dict[str, Any] = {"power": "unknown", "temperature": None, "humidity": None}

    set_argv = _set_argv(pod_id, power_on=True, apply=False)
    assert "--apply" not in set_argv  # nosec B101 - load-bearing invariant, not a stub

    set_payload = _parse_json(_run(set_argv))
    if set_payload is not None:
        changes = set_payload.get("changes")
        if isinstance(changes, dict):
            on_change = changes.get("on")
            if isinstance(on_change, dict) and "from" in on_change:
                result["power"] = "on" if on_change["from"] else "off"
            else:
                # No diff for `on`: the pod already matches the requested
                # value (on=True), i.e. it is already on.
                result["power"] = "on"

    read_payload = _parse_json(_run(_read_argv(pod_id)))
    if read_payload is not None:
        readings = read_payload.get("readings")
        if isinstance(readings, dict):
            result["temperature"] = readings.get("temperature")
            result["humidity"] = readings.get("humidity")

    return result

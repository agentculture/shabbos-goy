"""``shabbos-goy preflight`` -- everything must be true before Shabbat starts.

Read-only. Never actuates: no ``sensibo set``, no ``wpctl set-*``, no config
write, no mode override. Exits 0 only when every check passes; otherwise
exits 2, naming every failed check so an operator can fix precisely those.

Checks (each a named, independent id):

* ``lobes_key`` -- a lobes API key is set in the environment.
* ``lobes_health`` -- the ``senses`` role answers a keyless
  ``GET {base}/capabilities`` and reports itself ready.
* ``senses_decide`` -- one tiny authenticated ``decide()`` round trip returns
  a validated :class:`~shabbos_goy.decider.Decision` (never :data:`NO_DECISION`).
* ``sensibo_key`` -- ``SENSIBO_API_KEY`` is set in the environment, or the
  operator's ``grant`` store holds the secret named in config (metadata
  lookup only; the value is never requested).
* ``sensibo_pod`` -- at least one Sensibo pod is whitelisted in config.
* ``audio_node`` -- ``wpctl`` can read the default audio sink.
* ``clock_sync`` -- :func:`shabbos_goy.mode.clock_is_trusted`.
* ``location`` -- config names a valid location (lat/lon/timezone).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess  # nosec B404 - only a default argument, injectable for tests
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Mapping, Optional

from shabbos_goy import grant_inject
from shabbos_goy.audio import pipewire
from shabbos_goy.cli._commands._domain import next_strict_window_payload
from shabbos_goy.cli._errors import EXIT_ENV_ERROR, EXIT_SUCCESS
from shabbos_goy.cli._output import emit_result
from shabbos_goy.config import Config, load_config
from shabbos_goy.decider.context import ContextWindow
from shabbos_goy.decider.gemma import GemmaDecider, SensesConfig, senses_config_from_env
from shabbos_goy.lobes.config import ENV_API_KEY, ENV_API_KEY_FALLBACK, LobesConfigError
from shabbos_goy.mode import clock_is_trusted
from shabbos_goy.zmanim import Location

ENV_SENSIBO_API_KEY = "SENSIBO_API_KEY"
DEFAULT_AUDIO_TARGET = "@DEFAULT_AUDIO_SINK@"
CAPABILITIES_TIMEOUT_SECONDS = 3.0

#: decider.gemma "reason" codes that mean the round trip failed, as opposed
#: to a successful, validated decision (whose reason is "ok").
_DECIDE_FAILURE_REASONS = frozenset(
    {
        "connect_error",
        "timeout",
        "body_too_large",
        "bad_encoding",
        "bad_envelope",
        "no_choices",
        "bad_payload",
        "extra_keys",
        "missing_field",
        "bad_class",
        "bad_intent",
        "bad_confidence",
    }
)


@dataclass(frozen=True)
class CheckResult:
    id: str
    passed: bool
    message: str
    remediation: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "passed": self.passed,
            "message": self.message,
            "remediation": self.remediation,
        }


def _capabilities_get(
    base_url: str, *, timeout: float = CAPABILITIES_TIMEOUT_SECONDS
) -> tuple[bool, Optional[dict], str]:
    url = base_url.rstrip("/") + "/capabilities"
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # nosec B310
            raw = response.read()
    except urllib.error.HTTPError as exc:
        return False, None, f"http_{exc.code}"
    except urllib.error.URLError:
        return False, None, "connect_error"
    except OSError:
        return False, None, "connect_error"
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return False, None, "bad_response"
    if not isinstance(payload, dict):
        return False, None, "bad_response"
    return True, payload, "ok"


def _check_lobes_key(env: Mapping[str, str]) -> CheckResult:
    if env.get(ENV_API_KEY) or env.get(ENV_API_KEY_FALLBACK):
        return CheckResult("lobes_key", True, "a lobes API key is set in the environment")
    return CheckResult(
        "lobes_key",
        False,
        "no lobes API key in the environment",
        f"set {ENV_API_KEY} (or {ENV_API_KEY_FALLBACK})",
    )


def _check_lobes_health(env: Mapping[str, str]) -> tuple[CheckResult, Optional[SensesConfig]]:
    try:
        senses_config = senses_config_from_env(env)
    except LobesConfigError as exc:
        return (
            CheckResult(
                "lobes_health",
                False,
                f"lobes environment is not usable: {exc}",
                "set SHABBOS_GOY_LOBES_URL (or SHABBOS_GOY_SENSES_URL)",
            ),
            None,
        )
    ok, payload, reason = _capabilities_get(senses_config.base_url)
    if not ok:
        return (
            CheckResult(
                "lobes_health",
                False,
                f"could not reach {senses_config.endpoint}: {reason}",
                "check the lobes host is reachable and the senses role is running",
            ),
            senses_config,
        )
    senses = payload.get("senses") if isinstance(payload, dict) else None
    ready = isinstance(senses, dict) and bool(senses.get("ready") or senses.get("loaded"))
    if not ready:
        return (
            CheckResult(
                "lobes_health",
                False,
                "senses role reports not ready",
                "wait for the senses role to finish loading, or check its logs",
            ),
            senses_config,
        )
    return CheckResult("lobes_health", True, "senses role is ready"), senses_config


def _check_senses_decide(senses_config: Optional[SensesConfig]) -> CheckResult:
    if senses_config is None:
        return CheckResult(
            "senses_decide",
            False,
            "skipped: lobes environment is not usable (fix lobes_health first)",
            "fix lobes_health first",
        )
    decider = GemmaDecider(senses_config)
    decision = decider.decide("preflight ping", ContextWindow(), mode="weekday", ac_state=None)
    if decision.reason in _DECIDE_FAILURE_REASONS or decision.reason.startswith("http_"):
        return CheckResult(
            "senses_decide",
            False,
            f"decide() round trip failed: {decision.reason}",
            "check the senses role's model and auth key",
        )
    return CheckResult("senses_decide", True, "decide() round trip returned a validated decision")


def _check_sensibo_key(
    env: Mapping[str, str], *, grant_secret: str | None = None, runner=subprocess.run
) -> CheckResult:
    """The key is either in the environment, or held by the operator's ``grant`` store.

    For grant this asks ``grant show NAME --json``, which prints metadata only:
    preflight never asks for, holds or prints the key itself.
    """
    if env.get(ENV_SENSIBO_API_KEY):
        return CheckResult("sensibo_key", True, f"{ENV_SENSIBO_API_KEY} is set")
    if grant_secret:
        try:
            proc = runner(
                ["grant", "show", grant_secret, "--json"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            held = proc.returncode == 0
        except (OSError, subprocess.SubprocessError):
            held = False
        if held:
            return CheckResult(
                "sensibo_key", True, f"grant holds {grant_secret}; it is injected per sensibo call"
            )
        return CheckResult(
            "sensibo_key",
            False,
            f"grant does not hold {grant_secret} (or grant is not installed)",
            f"store it with: grant set {grant_secret} -   (value on stdin)",
        )
    return CheckResult(
        "sensibo_key",
        False,
        f"{ENV_SENSIBO_API_KEY} is not set and no grant secret is configured",
        f"either set {ENV_SENSIBO_API_KEY}, or store it with 'grant set {ENV_SENSIBO_API_KEY} -' "
        f'and add {{"grant": {{"sensibo_api_key": "{ENV_SENSIBO_API_KEY}"}}}} to the config',
    )


def _check_sensibo_pod(config: Config) -> CheckResult:
    entry = config.whitelist.get("sensibo")
    pods = entry.get("pods") if isinstance(entry, dict) else None
    if isinstance(pods, list) and pods:
        return CheckResult("sensibo_pod", True, f"{len(pods)} sensibo pod(s) whitelisted")
    return CheckResult(
        "sensibo_pod",
        False,
        "no sensibo pod whitelisted in config",
        "add a pod id under whitelist.sensibo.pods in the config file",
    )


def _check_audio_node(*, runner) -> CheckResult:
    try:
        pipewire.get_volume(DEFAULT_AUDIO_TARGET, runner=runner)
    except pipewire.VolumeCommandError as exc:
        return CheckResult(
            "audio_node",
            False,
            f"wpctl could not read the default audio sink: {exc}",
            "check PipeWire is running and wpctl is on PATH",
        )
    return CheckResult("audio_node", True, "wpctl reached the default audio sink")


def _check_clock_sync(now: datetime, *, runner) -> CheckResult:
    if clock_is_trusted(now, runner=runner):
        return CheckResult("clock_sync", True, "clock is trusted (NTP-synced, or a plausible date)")
    return CheckResult(
        "clock_sync",
        False,
        "clock is not trusted",
        "ensure NTP has synced (timedatectl) before Shabbat",
    )


def _check_location(config: Config) -> CheckResult:
    location = config.location
    if not isinstance(location, dict):
        return CheckResult(
            "location",
            False,
            "no location configured",
            "add a location block (lat, lon, timezone) to the config file",
        )
    lat, lon, tz = location.get("lat"), location.get("lon"), location.get("timezone")
    try:
        if isinstance(lat, bool) or isinstance(lon, bool):
            raise ValueError("lat/lon must be numbers, not booleans")
        if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
            raise ValueError("lat/lon must be numbers")
        Location(float(lat), float(lon), str(tz))
    except (TypeError, ValueError) as exc:
        return CheckResult(
            "location",
            False,
            f"location is invalid: {exc}",
            "fix lat/lon/timezone in the config file",
        )
    return CheckResult("location", True, "location is valid")


def run_preflight(
    *,
    env: Mapping[str, str],
    config: Config,
    now: datetime,
    runner=subprocess.run,
) -> list[CheckResult]:
    """Run every check, in a fixed order. Never actuates; never raises."""
    checks: list[CheckResult] = [_check_lobes_key(env)]
    lobes_health, senses_config = _check_lobes_health(env)
    checks.append(lobes_health)
    checks.append(_check_senses_decide(senses_config if lobes_health.passed else None))
    checks.append(_check_sensibo_key(env, grant_secret=config.grant_sensibo_secret, runner=runner))
    checks.append(_check_sensibo_pod(config))
    checks.append(_check_audio_node(runner=runner))
    checks.append(_check_clock_sync(now, runner=runner))
    checks.append(_check_location(config))
    return checks


def cmd_preflight(args: argparse.Namespace) -> int:
    json_mode = bool(getattr(args, "json", False))
    config = load_config(path=getattr(args, "config", None))
    # The lobes key may live in the operator's `grant` store: re-exec under it if so.
    grant_inject.ensure_lobes_key(config)
    now = datetime.now(timezone.utc)
    checks = run_preflight(env=os.environ, config=config, now=now, runner=subprocess.run)
    healthy = all(c.passed for c in checks)
    payload = {
        "healthy": healthy,
        "checks": [c.as_dict() for c in checks],
        "next_strict_window": next_strict_window_payload(now, config),
    }
    if json_mode:
        emit_result(payload, json_mode=True)
    else:
        lines = [f"healthy: {healthy}"]
        for c in checks:
            mark = "ok" if c.passed else "FAIL"
            lines.append(f"[{mark}] {c.id}: {c.message}")
            if not c.passed and c.remediation:
                lines.append(f"  hint: {c.remediation}")
        win = payload["next_strict_window"]
        if win is not None:
            lines.append(
                f"next strict window (local): {win['starts_at_local']} -> {win['ends_at_local']}"
            )
        emit_result("\n".join(lines), json_mode=False)
    return EXIT_SUCCESS if healthy else EXIT_ENV_ERROR


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "preflight",
        help="Check every precondition for Shabbat/Yom Kippur mode. Read-only.",
    )
    p.add_argument("--config", help="Path to an explicit config file.")
    p.add_argument("--json", action="store_true", help="Emit structured JSON.")
    p.set_defaults(func=cmd_preflight)

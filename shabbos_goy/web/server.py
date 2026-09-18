"""The dashboard server: ``http.server``, one page, four JSON endpoints.

This module *serves* the agent; it never re-implements it. Every number on
the page comes from the pipeline (:mod:`shabbos_goy.pipeline`) or the mode
resolver (:mod:`shabbos_goy.mode`), and every control goes back through the
same whitelist, the same argument validator, the same rate limiter and the
same adapters the ambient listener uses. There is no second copy of the
policy here, and there is deliberately no way to reach an adapter except
through those gates.

**Controls work in every mode, strict included.** That is a decision, not an
oversight: the dashboard is an operator UI, exactly like the CLI. The
invariant this repo exists to enforce is about *speech overheard by a
listening box* -- a person pressing a button has not spoken a command to the
agent. So the mode x class gate (:func:`shabbos_goy.policy.may_act`) does not
run here; the whitelist, the argument validation and the rate limits do, and
so does the dry-run default.

**Security.** The dashboard is reachable over Tailscale only and carries no
token. Its whole boundary is therefore two refusals:

1. it binds loopback or a Tailscale address, never a wildcard or a LAN
   address, unless config explicitly widens the rule (:mod:`.bind`);
2. a request whose ``Host`` header does not name this server, or whose
   ``Origin`` header names anywhere else, is refused before it is routed --
   which is what stops a page on another origin (or a rebound DNS name) from
   using a browser on the tailnet as a proxy.

GET is read-only by construction: the GET router reaches state building and
nothing else, and the query string is never parsed into anything.

**Privacy.** Transcript text is served, from memory, by exactly one endpoint
(``/api/utterances``) and is written nowhere: not to a log, not to a file,
not to stdout (the handler's request logging is silenced). Log lines carry
class/intent/verdict/action, pod ids are replaced by their alias, and an
exception is reported as its type name only -- a message can quote a
transcript, a pod id or an API response.
"""

from __future__ import annotations

import json
import threading
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Mapping, Optional

from .. import mode as mode_module
from ..cli._errors import CliError
from ..config import AC_ACTION, Config, validate_ac_argument
from ..decider.decision import INTENTS
from ..limits import BoundedRing
from ..pipeline import (
    ACTION_NONE,
    TOOL_SENSIBO,
    TOOL_VOLUME,
    VERDICT_ACTED,
    VERDICT_DRY_RUN,
    VERDICT_ERROR,
    VERDICT_NO_ADAPTER,
    VERDICT_NO_DECISION,
    VERDICT_NOT_WHITELISTED,
    VERDICT_RATE_LIMITED,
    LogRecord,
    PlannedAction,
)
from ..policy import CLASSES, MODES
from ..zmanim import SunEventNotFound, next_window
from .bind import ALLOW_NON_TAILNET_KEY, BindRefused, parse_bind_address, validate_bind_address
from .page import DASHBOARD_HTML

__all__ = [
    "BindResult",
    "Controls",
    "DashboardServer",
    "control_server",
    "controls_from_pipeline",
]

#: A control that only inspected things.
VERDICT_CHECKED = "checked"

#: The klass every control-log line carries. Controls have no utterance and
#: therefore no class; this keeps the log's shape uniform without pretending
#: a human said anything.
CONTROL_KLASS = "control"

DEFAULT_AC_STATUS_TTL_SECONDS = 10.0
DEFAULT_CONTROL_LOG_CAPACITY = 50
MAX_BODY_BYTES = 64 * 1024

_READ_PATHS = frozenset({"/", "/index.html", "/api/state", "/api/utterances"})
_CONTROL_PATHS = frozenset(
    {
        "/api/control/ac",
        "/api/control/volume",
        "/api/control/mode",
        "/api/control/preflight",
    }
)


# ---------------------------------------------------------------------------
# results and adapters
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BindResult:
    """The outcome of :meth:`DashboardServer.start`.

    A refusal or an OS-level bind failure is reported *here*, not raised: the
    listener that starts the dashboard in a thread must keep listening (and
    may retry) whether or not its UI came up. ``reason`` is a short code --
    never an exception message, which can carry a host or a key.
    """

    ok: bool
    address: str = ""
    port: int = 0
    reason: str = ""


@dataclass(frozen=True)
class Controls:
    """The adapters and identifiers a control endpoint may reach.

    Built from the pipeline (:func:`controls_from_pipeline`) so the dashboard
    literally shares the listener's adapters rather than constructing its own
    -- there is no second route to a Sensibo pod or to ``wpctl`` in this
    process.
    """

    pod_id: str = ""
    pod_alias: str = "ac"
    volume_key: str = "self"
    volume_alias: str = "spk"
    ac_power: Optional[Callable[..., Mapping[str, Any]]] = None
    ac_status: Optional[Callable[[str], Mapping[str, Any]]] = None
    volume_step: Optional[Callable[[int], Any]] = None
    apply: bool = False


def controls_from_pipeline(pipeline: Any) -> Controls:
    """The pipeline's own adapters and device identifiers, as a
    :class:`Controls`."""
    return Controls(
        pod_id=getattr(pipeline, "_pod_id", "") or "",
        pod_alias=getattr(pipeline, "_pod_alias", "ac") or "ac",
        volume_key=getattr(pipeline, "_volume_key", "self") or "self",
        volume_alias=getattr(pipeline, "_volume_alias", "spk") or "spk",
        ac_power=getattr(pipeline, "_ac_power", None),
        ac_status=getattr(pipeline, "_ac_status", None),
        volume_step=getattr(pipeline, "_volume_step", None),
        apply=bool(getattr(pipeline, "apply", False)),
    )


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------


def _percentile(samples: list[float], fraction: float) -> float:
    """Nearest-rank percentile. Small sample sizes, no interpolation games."""
    ordered = sorted(samples)
    rank = max(1, min(len(ordered), int(-(-len(ordered) * fraction // 1))))
    return float(ordered[rank - 1])


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _window_dict(window: Any) -> dict[str, Any]:
    return {
        "start": _iso(window.start),
        "end": _iso(window.end),
        "kinds": list(window.kinds),
    }


def _window_summary(now: datetime, config: Config) -> dict[str, Any]:
    """The window running now (if any) and the next one.

    Built from the *same* config-to-zmanim helpers the mode resolver uses, so
    the page can never show a window the resolver would not have honoured.
    They are private to ``shabbos_goy.mode`` today (see the report note: a
    public ``mode.window_summary`` belongs there); reaching for them is still
    better than a second copy of the fail-closed rules.
    """
    unavailable = {"available": False, "current": None, "next": None, "reason": "no_zmanim"}
    if not config.ok:
        return {**unavailable, "reason": "config"}
    location = mode_module._build_location(config)
    rules = mode_module._build_rules(config)
    if location is None or rules is None:
        return {**unavailable, "reason": "config"}
    try:
        window = next_window(now, location, rules)
        if window is None:
            return {"available": True, "current": None, "next": None}
        if window.contains(now):
            following = next_window(window.end, location, rules)
            return {
                "available": True,
                "current": _window_dict(window),
                "next": _window_dict(following) if following is not None else None,
            }
        return {"available": True, "current": None, "next": _window_dict(window)}
    except (SunEventNotFound, ValueError):
        return {**unavailable, "reason": "sun_event_not_found"}


def _record_dict(record: LogRecord) -> dict[str, str]:
    return {
        "klass": record.klass,
        "intent": record.intent,
        "verdict": record.verdict,
        "action": record.action,
        "target": record.target,
        "reason": record.reason,
    }


def _mode_dict(resolved: Any) -> dict[str, Any]:
    return {
        "mode": getattr(resolved, "mode", "strict"),
        "kinds": list(getattr(resolved, "kinds", ()) or ()),
        "clock_trusted": bool(getattr(resolved, "clock_trusted", False)),
        "overridden": bool(getattr(resolved, "overridden", False)),
        "override": mode_module.get_override(),
    }


# ---------------------------------------------------------------------------
# the server
# ---------------------------------------------------------------------------


class DashboardServer:
    """The operator dashboard: a page, a state feed, and four controls.

    Constructed by the ``listen`` verb and started in a thread. Everything
    time-dependent is injected (``clock``, ``now_provider``) so tests need no
    real clock, and every outward call (``connection_provider``,
    ``latency_provider``, the adapters) is optional -- a missing one is
    reported as unavailable, never faked.
    """

    def __init__(
        self,
        pipeline: Any,
        mode_provider: Callable[[], Any],
        config: Config,
        *,
        controls: Optional[Controls] = None,
        decider: Any = None,
        connection_provider: Optional[Callable[[], Mapping[str, Any]]] = None,
        latency_provider: Optional[Callable[[], Any]] = None,
        now_provider: Optional[Callable[[], datetime]] = None,
        clock: Optional[Callable[[], float]] = None,
        bind_address: Optional[str] = None,
        loopback_only: bool = False,
        ac_status_ttl_seconds: float = DEFAULT_AC_STATUS_TTL_SECONDS,
        control_log_capacity: int = DEFAULT_CONTROL_LOG_CAPACITY,
        server_factory: Callable[..., Any] = ThreadingHTTPServer,
    ) -> None:
        self.pipeline = pipeline
        self.mode_provider = mode_provider
        self.config = config
        self.controls = controls if controls is not None else controls_from_pipeline(pipeline)
        self.decider = decider if decider is not None else getattr(pipeline, "_decider", None)
        self._connection_provider = connection_provider
        self._latency_provider = latency_provider
        self._now_provider = now_provider or (lambda: datetime.now(timezone.utc))
        self._clock = clock if clock is not None else getattr(pipeline, "_clock", None)
        if self._clock is None:  # pragma: no cover - the pipeline always has one
            self._clock = lambda: 0.0
        self._bind_address = bind_address
        self._loopback_only = loopback_only
        self._ac_ttl = float(ac_status_ttl_seconds)
        self._server_factory = server_factory

        self._control_log: BoundedRing[LogRecord] = BoundedRing(control_log_capacity)
        self._ac_cache: Optional[tuple[float, dict[str, Any]]] = None
        self._lock = threading.Lock()
        self._httpd: Any = None
        self._thread: Optional[threading.Thread] = None
        self.bind_result = BindResult(ok=False, reason="not_started")

    # -- lifecycle ---------------------------------------------------------

    @property
    def url(self) -> Optional[str]:
        if not self.bind_result.ok:
            return None
        host = self.bind_result.address
        if ":" in host:
            host = f"[{host}]"
        return f"http://{host}:{self.bind_result.port}"

    def resolve_bind(self) -> tuple[str, int]:
        """The (host, port) this server would bind, or :class:`BindRefused`.

        Public so a caller can check a configuration without opening a
        socket -- ``start`` is the only thing here that binds.
        """
        raw = self._bind_address
        if raw is None and self.config.ok:
            configured = self.config.dashboard_bind_address
            raw = configured if isinstance(configured, str) else None
        if raw is None:
            # No usable config: loopback, never a wildcard.
            raw = "127.0.0.1"
        host, port = parse_bind_address(raw)
        if self._loopback_only:
            return "127.0.0.1", port
        allow = False
        if self.config.ok:
            allow = self.config.raw.get(ALLOW_NON_TAILNET_KEY) is True
        return validate_bind_address(host, allow_non_tailnet=allow), port

    def start(self) -> BindResult:
        """Bind and serve in a daemon thread. Never raises into the caller."""
        try:
            host, port = self.resolve_bind()
        except BindRefused:
            self.bind_result = BindResult(ok=False, reason="bind_refused")
            return self.bind_result

        try:
            httpd = self._server_factory((host, port), _Handler)
        except OSError:
            self.bind_result = BindResult(ok=False, reason="bind_failed")
            return self.bind_result

        httpd.daemon_threads = True
        httpd.dashboard = self
        self._httpd = httpd
        bound_port = httpd.server_address[1]
        self._thread = threading.Thread(
            target=httpd.serve_forever, name="shabbos-goy-dashboard", daemon=True
        )
        self._thread.start()
        self.bind_result = BindResult(ok=True, address=host, port=bound_port)
        return self.bind_result

    def stop(self) -> None:
        """Stop serving. Safe to call on a server that never bound."""
        httpd, thread = self._httpd, self._thread
        self._httpd = self._thread = None
        if httpd is not None:
            httpd.shutdown()
            httpd.server_close()
        if thread is not None:
            thread.join(timeout=5)
        if self.bind_result.ok:
            self.bind_result = BindResult(ok=False, reason="stopped")

    # -- host / origin -----------------------------------------------------

    def allowed_hosts(self) -> frozenset[str]:
        """Every ``Host`` header value that names *this* server.

        A request carrying anything else is refused: that is what a rebound
        DNS name looks like from in here.
        """
        port = self.bind_result.port
        names = {self.bind_result.address, "localhost"}
        if self.bind_result.address in ("127.0.0.1", "::1"):
            names |= {"127.0.0.1", "::1"}
        extra = self.config.raw.get("dashboard_hostnames") if self.config.ok else None
        if isinstance(extra, list):
            names |= {name for name in extra if isinstance(name, str) and name}
        allowed = set()
        for name in names:
            bracketed = f"[{name}]" if ":" in name else name
            allowed.add(f"{bracketed}:{port}".lower())
            if port in (80, 0):
                allowed.add(bracketed.lower())
        return frozenset(allowed)

    # -- state -------------------------------------------------------------

    def _resolved_mode(self) -> Any:
        return self.mode_provider()

    def _ac_state(self) -> dict[str, Any]:
        """The AC's current state, cached briefly: the page polls, and every
        miss is a cloud round trip."""
        now = self._clock()
        with self._lock:
            cached = self._ac_cache
            if cached is not None and now - cached[0] < self._ac_ttl:
                return dict(cached[1])
        status = self.controls.ac_status
        if status is None:
            state = {"available": False, "power": "unknown", "reason": "no_adapter"}
        else:
            try:
                raw = status(self.controls.pod_id)
            except Exception as exc:  # noqa: BLE001 - the message may quote a pod id
                state = {"available": False, "power": "unknown", "reason": type(exc).__name__}
            else:
                data = dict(raw) if isinstance(raw, Mapping) else {}
                state = {
                    "available": True,
                    "power": data.get("power", "unknown"),
                    "temperature": data.get("temperature"),
                    "humidity": data.get("humidity"),
                    "reason": "",
                }
        state.setdefault("temperature", None)
        state.setdefault("humidity", None)
        with self._lock:
            self._ac_cache = (now, dict(state))
        return dict(state)

    def _connection(self) -> dict[str, Any]:
        if self._connection_provider is None:
            return {"state": "unknown", "reason": "not_wired"}
        try:
            raw = self._connection_provider()
        except Exception as exc:  # noqa: BLE001 - never break the page on a probe
            return {"state": "unknown", "reason": type(exc).__name__}
        data = dict(raw) if isinstance(raw, Mapping) else {}
        data.setdefault("state", "unknown")
        return data

    def _latency(self) -> dict[str, Any]:
        """Decide-latency percentiles, when something measures them.

        The pipeline keeps no per-decision timings, so with nothing injected
        this says ``available: false`` rather than inventing a number.
        """
        absent = {"available": False, "p50": None, "p95": None, "count": 0}
        if self._latency_provider is None:
            return absent
        try:
            raw = self._latency_provider()
        except Exception:  # noqa: BLE001
            return absent
        samples = [
            float(value)
            for value in (raw or [])
            if isinstance(value, (int, float)) and not isinstance(value, bool)
        ]
        if not samples:
            return absent
        return {
            "available": True,
            "count": len(samples),
            "p50": _percentile(samples, 0.5),
            "p95": _percentile(samples, 0.95),
        }

    def _decider_state(self, records: list[LogRecord]) -> dict[str, Any]:
        source = getattr(self.decider, "source", None)
        if not isinstance(source, str):
            source = None
        prompt_version = source.split(":", 1)[1] if source and ":" in source else None
        last = None
        for record in reversed(records):
            if record.klass in CLASSES and record.intent in INTENTS:
                last = {
                    "klass": record.klass,
                    "intent": record.intent,
                    # The pipeline does not retain per-decision confidence;
                    # nothing here invents one.
                    "confidence": None,
                    "verdict": record.verdict,
                    "action": record.action,
                    "reason": record.reason,
                }
                break
        reasons = Counter(
            record.reason or "unknown"
            for record in records
            if record.verdict == VERDICT_NO_DECISION
        )
        return {
            "source": source,
            "prompt_version": prompt_version,
            "last": last,
            "no_decision_reasons": dict(reasons),
            "decide_latency_ms": self._latency(),
        }

    def state(self) -> dict[str, Any]:
        """Everything the page shows except the transcripts themselves."""
        records = list(self.pipeline.log_records)
        return {
            "mode": _mode_dict(self._resolved_mode()),
            "window": _window_summary(self._now_provider(), self.config),
            "connection": self._connection(),
            "ac": self._ac_state(),
            "apply": bool(getattr(self.pipeline, "apply", False)),
            "decider": self._decider_state(records),
            "errors": [
                {"reason": record.reason} for record in records if record.verdict == VERDICT_ERROR
            ],
            "log": [_record_dict(record) for record in records],
            "controls": [_record_dict(record) for record in self._control_log],
            "limits": {
                "pending_delayed": len(getattr(self.pipeline, "delay_timer", ())),
                # A count only: a refusal record keys on the real pod id.
                "refusals": len(getattr(self.pipeline, "rate_limiter").refusal_log),
            },
            "config_ok": self.config.ok,
        }

    def utterances(self) -> dict[str, Any]:
        """The in-memory transcript ring, paired with its verdicts.

        Pairing walks both rings backwards and stops at the first mismatch:
        the log ring is longer-lived than the transcript ring, so an older
        transcript may have no record left. That is reported as ``null``,
        never guessed.
        """
        recents = list(self.pipeline.recent())
        records = [
            record
            for record in self.pipeline.log_records
            if record.klass in CLASSES and record.intent in INTENTS
        ]
        paired: list[Optional[LogRecord]] = [None] * len(recents)
        index, record_index = len(recents) - 1, len(records) - 1
        while index >= 0 and record_index >= 0:
            record, utterance = records[record_index], recents[index]
            if record.klass != utterance.klass or record.intent != utterance.intent:
                break
            paired[index] = record
            index -= 1
            record_index -= 1

        now = self._clock()
        rows = []
        for utterance, record in zip(recents, paired):
            rows.append(
                {
                    "at": utterance.at,
                    "age_seconds": now - utterance.at,
                    "text": utterance.text,
                    "klass": utterance.klass,
                    "intent": utterance.intent,
                    "verdict": record.verdict if record is not None else None,
                    "action": record.action if record is not None else None,
                    "target": record.target if record is not None else None,
                    "reason": record.reason if record is not None else None,
                    # No per-utterance timing exists in the pipeline today.
                    "decide_latency_ms": None,
                }
            )
        capacity = getattr(getattr(self.pipeline, "_recent", None), "capacity", len(rows))
        return {"capacity": capacity, "count": len(rows), "utterances": rows}

    # -- controls ----------------------------------------------------------

    def _log_control(self, intent: str, verdict: str, action: str, target: str, reason: str = ""):
        record = LogRecord(
            klass=CONTROL_KLASS,
            intent=intent,
            verdict=verdict,
            action=action,
            reason=reason,
            target=target,
        )
        self._control_log.append(record)
        return record

    def _refuse(self, intent: str, verdict: str, action: str, target: str) -> dict[str, Any]:
        self._log_control(intent, verdict, action, target)
        return {"ok": False, "verdict": verdict, "action": action, "target": target, "reason": ""}

    def control_ac(self, body: Mapping[str, Any]) -> dict[str, Any]:
        """Turn the AC on or off, through every gate the voice path uses."""
        value = body.get("power")
        if not isinstance(value, str):
            raise CliError(code=400, message="power must be 'on' or 'off'")
        validate_ac_argument(AC_ACTION, value)
        planned = PlannedAction(
            tool=TOOL_SENSIBO,
            key=self.controls.pod_id,
            alias=self.controls.pod_alias,
            action=AC_ACTION,
            value=value,
        )
        return self._run(planned, "ac")

    def control_volume(self, body: Mapping[str, Any]) -> dict[str, Any]:
        direction = body.get("direction")
        if direction not in ("up", "down"):
            raise CliError(code=400, message="direction must be 'up' or 'down'")
        planned = PlannedAction(
            tool=TOOL_VOLUME,
            key=self.controls.volume_key,
            alias=self.controls.volume_alias,
            action="step",
            value=direction,
        )
        return self._run(planned, "volume")

    def _run(self, planned: PlannedAction, intent: str) -> dict[str, Any]:
        """Whitelist, rate limit, adapter -- the same three the voice path uses.

        The mode x class gate is deliberately absent (an operator is not an
        overheard utterance), and so is the 'already in state' check: a
        button press is explicit, and refusing it because a cloud read failed
        would make the UI useless exactly when it is needed.
        """
        action = planned.name
        if not self.config.is_whitelisted(planned.tool, planned.key):
            return self._refuse(intent, VERDICT_NOT_WHITELISTED, action, planned.alias)

        allowed, _reason = self.pipeline.rate_limiter.check(planned.key)
        if not allowed:
            return self._refuse(intent, VERDICT_RATE_LIMITED, action, planned.alias)

        adapter = (
            self.controls.ac_power if planned.tool == TOOL_SENSIBO else self.controls.volume_step
        )
        if adapter is None:
            return self._refuse(intent, VERDICT_NO_ADAPTER, action, planned.alias)

        apply = self.controls.apply
        try:
            if planned.tool == TOOL_SENSIBO:
                result = adapter(planned.key, planned.value == "on", apply=apply)
                acted = bool(isinstance(result, Mapping) and result.get("acted"))
                verdict = VERDICT_ACTED if acted else VERDICT_DRY_RUN
            elif not apply:
                # A volume step has no dry-run form of its own: not calling
                # the adapter IS the dry run.
                verdict = VERDICT_DRY_RUN
            else:
                adapter(1 if planned.value == "up" else -1)
                verdict = VERDICT_ACTED
        except Exception as exc:  # noqa: BLE001 - the message may quote a pod id
            reason = type(exc).__name__
            self._log_control(intent, VERDICT_ERROR, action, planned.alias, reason)
            return {
                "ok": False,
                "verdict": VERDICT_ERROR,
                "action": action,
                "target": planned.alias,
                "reason": reason,
            }

        self.pipeline.rate_limiter.record(planned.key)
        self._log_control(intent, verdict, action, planned.alias)
        return {
            "ok": True,
            "verdict": verdict,
            "action": action,
            "target": planned.alias,
            "reason": "",
        }

    def control_mode(self, body: Mapping[str, Any]) -> dict[str, Any]:
        """Force strict on, switch it off, or clear the override.

        Calls :func:`shabbos_goy.mode.set_override` -- the single in-memory
        override the CLI also uses, so neither UI keeps its own copy and a
        restart forgets it either way.
        """
        if "mode" not in body:
            raise CliError(code=400, message="mode is required (a mode name, or null)")
        value = body["mode"]
        if value is not None and value not in MODES:
            raise CliError(code=400, message=f"mode must be null or one of {MODES}")
        mode_module.set_override(value)
        self._log_control("mode", VERDICT_ACTED, f"mode_{value or 'clear'}", "-", value or "clear")
        return {"ok": True, "verdict": VERDICT_ACTED, "action": f"mode_{value or 'clear'}"}

    def preflight(self) -> dict[str, Any]:
        """Check everything an actuation would need. Changes nothing."""
        resolved = self._resolved_mode()
        ac = self._ac_state()
        checks = [
            {"name": "config", "ok": self.config.ok, "detail": "loaded" if self.config.ok else ""},
            {
                "name": "clock",
                "ok": bool(getattr(resolved, "clock_trusted", False)),
                "detail": getattr(resolved, "mode", ""),
            },
            {
                "name": "whitelist",
                "ok": self.config.is_whitelisted(TOOL_SENSIBO, self.controls.pod_id)
                or self.config.is_whitelisted(TOOL_VOLUME, self.controls.volume_key),
                "detail": "",
            },
            {"name": "ac_adapter", "ok": self.controls.ac_power is not None, "detail": ""},
            {"name": "ac_status", "ok": bool(ac.get("available")), "detail": ac.get("reason", "")},
            {"name": "volume_adapter", "ok": self.controls.volume_step is not None, "detail": ""},
            {
                "name": "decider",
                "ok": self.decider is not None,
                "detail": str(getattr(self.decider, "source", "") or ""),
            },
            {
                "name": "connection",
                "ok": self._connection().get("state") == "connected",
                "detail": str(self._connection().get("state")),
            },
            {"name": "apply", "ok": True, "detail": "apply" if self.controls.apply else "dry-run"},
        ]
        ok = all(check["ok"] for check in checks)
        self._log_control("preflight", VERDICT_CHECKED, ACTION_NONE, "-")
        return {"ok": ok, "checks": checks}


def control_server(pipeline: Any, mode_provider: Callable[[], Any], config: Config, **kwargs):
    """The loopback-only control endpoint the CLI talks to.

    Same handlers, same gates; the only difference is that it ignores
    ``dashboard_bind_address`` and always binds ``127.0.0.1``.
    """
    kwargs.setdefault("bind_address", "127.0.0.1:0")
    return DashboardServer(pipeline, mode_provider, config, loopback_only=True, **kwargs)


# ---------------------------------------------------------------------------
# the handler
# ---------------------------------------------------------------------------


class _Handler(BaseHTTPRequestHandler):
    """Routing, the Host/Origin refusal, and JSON in/out. No policy lives here."""

    server_version = "shabbos-goy"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    # -- plumbing ----------------------------------------------------------

    @property
    def dashboard(self) -> DashboardServer:
        return self.server.dashboard  # type: ignore[attr-defined]

    def log_message(self, format: str, *args: Any) -> None:
        """Silence. Request lines would otherwise reach stdout/stderr, and
        Docker keeps container output (CLAUDE.md, "Privacy")."""

    def _send(self, status: int, payload: Any, content_type: str = "application/json") -> None:
        if content_type.startswith("application/json"):
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        else:
            body = str(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        # No CORS header of any kind: nothing may read this cross-origin.
        self.end_headers()
        self.wfile.write(body)

    def _error(self, status: int, error: str, reason: str = "") -> None:
        self._send(status, {"error": error, "reason": reason})

    def _path(self) -> str:
        return self.path.split("?", 1)[0].split("#", 1)[0]

    def _guard(self) -> bool:
        """The Host/Origin refusal. False means the request was answered."""
        allowed = self.dashboard.allowed_hosts()
        host = (self.headers.get("Host") or "").strip().lower()
        if host not in allowed:
            self._error(403, "forbidden", "host_mismatch")
            return False
        origin = (self.headers.get("Origin") or "").strip()
        if origin:
            netloc = ""
            if "://" in origin:
                scheme, _, rest = origin.partition("://")
                if scheme.lower() in ("http", "https"):
                    netloc = rest.split("/", 1)[0].lower()
            if netloc not in allowed:
                self._error(403, "forbidden", "origin_mismatch")
                return False
        return True

    def _json_body(self) -> Optional[dict[str, Any]]:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return None
        if length <= 0 or length > MAX_BODY_BYTES:
            return None
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return None
        return payload if isinstance(payload, dict) else None

    # -- verbs -------------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's contract
        if not self._guard():
            return
        path = self._path()
        if path in ("/", "/index.html"):
            self._send(200, DASHBOARD_HTML, "text/html")
            return
        if path == "/api/state":
            self._send(200, self.dashboard.state())
            return
        if path == "/api/utterances":
            self._send(200, self.dashboard.utterances())
            return
        if path in _CONTROL_PATHS:
            self._error(405, "method_not_allowed", "controls are POST only")
            return
        self._error(404, "not_found")

    def do_POST(self) -> None:  # noqa: N802
        if not self._guard():
            return
        path = self._path()
        if path in _READ_PATHS:
            self._error(405, "method_not_allowed", "read endpoints are GET only")
            return
        if path not in _CONTROL_PATHS:
            self._error(404, "not_found")
            return

        dashboard = self.dashboard
        if path == "/api/control/preflight":
            result = dashboard.preflight()
            self._send(200, {**result, "mode": _mode_dict(dashboard._resolved_mode())})
            return

        body = self._json_body()
        if body is None:
            self._error(400, "invalid_arguments", "a JSON object body is required")
            return
        try:
            if path == "/api/control/ac":
                result = dashboard.control_ac(body)
            elif path == "/api/control/volume":
                result = dashboard.control_volume(body)
            else:
                result = dashboard.control_mode(body)
        except CliError:
            # The message can quote whatever the caller sent; only the code
            # goes back.
            self._error(400, "invalid_arguments")
            return
        self._send(200, {**result, "mode": _mode_dict(dashboard._resolved_mode())})

    def _method_not_allowed(self) -> None:
        if not self._guard():
            return
        self._error(405, "method_not_allowed")

    do_PUT = _method_not_allowed  # noqa: N815
    do_DELETE = _method_not_allowed  # noqa: N815
    do_PATCH = _method_not_allowed  # noqa: N815

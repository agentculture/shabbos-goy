"""The decision pipeline: one transcript in, at most one action out.

This is where every other module meets. Events arrive from the ears-only
lobes session (``shabbos_goy.lobes``), are re-joined into whole utterances
(``shabbos_goy.joiner``), labelled by an injected decider
(``shabbos_goy.decider``), and then have to survive a fixed sequence of gates
before anything at all happens:

.. code-block:: text

    utterance
      -> own-voice suppression      (did we overhear ourselves?)
      -> decider                    (class + intent + confidence; UNTRUSTED)
      -> shape re-validation        (policy.CLASSES / decider.INTENTS)
      -> minimum confidence         (config, default 0.6)
      -> policy.may_act(mode, ...)  (the mode x class table; clock trust)
      -> intent -> whitelisted tool (a fixed table, in this module)
      -> config whitelist           (which tool, which pod)
      -> argument validation        (config.validate_ac_argument)
      -> rate limits                (limits.RateLimiter)
      -> already-in-state?          (the adapter's own status(); no-op if so)
      -> strict-mode delay          (limits.DelayTimer, in memory)
      -> the adapter                (dry-run unless apply=True)

**The model labels; this code decides** (deviation d1). A
:class:`~shabbos_goy.decider.Decision` is input from a language model that
heard a human through a speech recogniser, so this module re-validates its
shape even though every shipped decider validates its own output: another
``Decider`` implementation may not. Anything that does not fit --- a decider
that is down, slow or malformed, an unknown class, an intent with no
whitelisted tool, an argument outside the allowed set --- is "do nothing, say
nothing". There is no confirmation question anywhere in this file, by
construction: the only thing the pipeline ever says is a neutral statement,
and only on a weekday.

Three deliberate choices worth stating:

* **The rule classifier is not here** (deviation d2). This module never
  imports ``shabbos_goy.classifier``; the rules survive only as a test
  oracle.
* **The rolling context survives a reconnect** (deviation d3). A lost
  connection resets the *joiner* --- a half-turn must never be acted on ---
  but the :class:`~shabbos_goy.decider.ContextWindow` is only context, not a
  queued action, and dropping it would make the first utterance after a
  blip harder to read, not safer. It is memory-only and empty in a freshly
  constructed pipeline, which is what "gone on restart" means here.
* **Nothing is persisted, ever** (invariant #3). The delayed-action timer,
  the rate limiter and the recent-utterance ring all live in this object's
  memory. A restart starts them empty; a refused action is never queued for
  a retry.

Privacy (CLAUDE.md): log lines carry the class, the intent, the gate verdict
and the action --- never transcript text, never an exception message, and
never a pod id (a stable short alias is logged instead, while the rate
limiter keys its own in-memory refusal records on the real pod id). Recent
utterance text exists in exactly one place, the in-memory ring behind
:meth:`Pipeline.recent`.
"""

from __future__ import annotations

import subprocess  # nosec B404 - only a default argument, never invoked here
import sys
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Optional

from .audio import pipewire
from .cli._errors import CliError
from .config import AC_ACTION, Config, validate_ac_argument
from .decider import NO_DECISION, ContextWindow, Decider, Decision
from .decider.decision import INTENTS
from .joiner import TranscriptJoiner
from .limits import BoundedRing, Clock, DelayTimer, LimitsConfig, RateLimiter, system_clock
from .policy import CLASSES, may_act

__all__ = [
    "LogRecord",
    "Pipeline",
    "PlannedAction",
    "RecentUtterance",
    "pipewire_volume_stepper",
]

# -- tools -------------------------------------------------------------------

TOOL_SENSIBO = "sensibo"
TOOL_VOLUME = "volume"

#: The whole intent -> tool table. An intent absent from here has no tool and
#: therefore cannot act, whatever a model proposes.
_TOOL_BY_INTENT: dict[str, tuple[str, str, str]] = {
    # intent: (tool, action, value)
    "cool": (TOOL_SENSIBO, AC_ACTION, "on"),
    "warm": (TOOL_SENSIBO, AC_ACTION, "off"),
    "quieter": (TOOL_VOLUME, "step", "down"),
    "louder": (TOOL_VOLUME, "step", "up"),
}

_ACTION_NAMES: dict[tuple[str, str], str] = {
    (TOOL_SENSIBO, "on"): "ac_power_on",
    (TOOL_SENSIBO, "off"): "ac_power_off",
    (TOOL_VOLUME, "up"): "volume_up",
    (TOOL_VOLUME, "down"): "volume_down",
}

ACTION_NONE = "none"
ACTION_SPEAK_STATUS = "speak_status"

# -- verdicts (the whole vocabulary a log line can carry) --------------------

VERDICT_ACTED = "acted"
VERDICT_ALREADY_IN_STATE = "already_in_state"
VERDICT_DELAYED = "delayed"
VERDICT_DRY_RUN = "dry_run"
VERDICT_ERROR = "error"
VERDICT_GATE_REFUSED = "gate_refused"
VERDICT_INVALID_ARGUMENTS = "invalid_arguments"
VERDICT_INVALID_DECISION = "invalid_decision"
VERDICT_LOW_CONFIDENCE = "low_confidence"
VERDICT_NO_ACTION = "no_action"
VERDICT_NO_ADAPTER = "no_adapter"
VERDICT_NO_DECISION = "no_decision"
VERDICT_NOT_WHITELISTED = "not_whitelisted"
VERDICT_OWN_VOICE = "own_voice"
VERDICT_RATE_LIMITED = "rate_limited"
VERDICT_STATE_UNKNOWN = "state_unknown"

DEFAULT_MIN_CONFIDENCE = 0.6
DEFAULT_JOIN_GAP_MS = 500
DEFAULT_PLAYBACK_TAIL_SECONDS = 1.5
DEFAULT_RECENT_CAPACITY = 20
DEFAULT_LOG_CAPACITY = 200
DEFAULT_LATENCY_CAPACITY = 20
DEFAULT_STRICT_DELAY_SECONDS = 15.0

#: Fallback rate limits when config does not (or cannot) supply them. Narrow
#: on purpose: a missing limits block must not mean "no limit".
FALLBACK_LIMITS = LimitsConfig(min_interval_seconds=600.0, daily_cap=12)

# Neutral spoken statements. Hebrew, because English words inside Hebrew are
# weak both ways (CLAUDE.md), and never a question: a question would invite a
# reply and turn the exchange into a conversation.
SPEECH_AC_ON = "המזגן פועל"
SPEECH_AC_OFF = "המזגן כבוי"
SPEECH_AC_UNKNOWN = "מצב המזגן לא ידוע"

_KIND_SPEECH_STARTED = "speech_started"
_KIND_SPEECH_STOPPED = "speech_stopped"
_KIND_TRANSCRIPT = "transcript"

#: Event kinds that mean the session was cut off: the in-flight turn is a
#: fragment and must be discarded, never emitted and never replayed.
_RESET_KINDS = frozenset({"connection_lost", "stalled", "session_closed"})

_WIRE_BY_KIND = {
    _KIND_SPEECH_STARTED: "input_audio_buffer.speech_started",
    _KIND_SPEECH_STOPPED: "input_audio_buffer.speech_stopped",
    _KIND_TRANSCRIPT: "conversation.item.input_audio_transcription.completed",
}

_SPEECH_STARTED_TYPES = frozenset({"input_audio_buffer.speech_started", _KIND_SPEECH_STARTED})
_BOUNDARY_TYPES = _SPEECH_STARTED_TYPES | frozenset(
    {"input_audio_buffer.speech_stopped", _KIND_SPEECH_STOPPED}
)


# -- records -----------------------------------------------------------------


@dataclass(frozen=True)
class PlannedAction:
    """One resolved, not-yet-approved tool call.

    ``key`` is the real device identifier (a Sensibo pod id, a PipeWire node
    key). It is what the rate limiter keys on and what the adapter is called
    with --- and it is never logged. ``alias`` is the stable short name that
    goes into log lines instead.
    """

    tool: str
    key: str
    alias: str
    action: str
    value: str

    @property
    def name(self) -> str:
        return _ACTION_NAMES.get((self.tool, self.value), ACTION_NONE)


@dataclass(frozen=True)
class LogRecord:
    """One log line. Four fields, plus an optional short reason code.

    There is deliberately no field that could hold transcript text, an
    exception message or a pod id: the type itself is the guarantee.
    """

    klass: str
    intent: str
    verdict: str
    action: str
    reason: str = ""
    target: str = ""

    def render(self) -> str:
        parts = [
            f"class={self.klass}",
            f"intent={self.intent}",
            f"verdict={self.verdict}",
            f"action={self.action}",
        ]
        if self.target:
            parts.append(f"target={self.target}")
        if self.reason:
            parts.append(f"reason={self.reason}")
        return " ".join(parts)


@dataclass(frozen=True)
class RecentUtterance:
    """One remembered utterance, for the dashboard.

    The ONLY place recent transcript text lives. Memory-only and bounded.
    ``confidence`` and ``decide_latency_ms`` travel with the text rather than
    with the log record on purpose: a :class:`LogRecord` is what gets
    *printed*, and neither number belongs in a log line.
    """

    at: float
    text: str
    klass: str
    intent: str
    confidence: float = 0.0
    decide_latency_ms: float = 0.0


# -- the volume adapter ------------------------------------------------------


def pipewire_volume_stepper(
    target: str,
    bounds: Optional[pipewire.VolumeBounds] = None,
    *,
    runner: Callable[..., Any] = subprocess.run,
    env: Optional[dict[str, str]] = None,
) -> Callable[[int], float]:
    """A ``steps -> new level`` callable backed by ``wpctl``.

    Built here rather than inside :class:`Pipeline` so the pipeline's own
    tests never go near a real ``wpctl``: the pipeline takes whatever
    callable it is handed, and the CLI wires this one in.
    """
    effective_bounds = bounds if bounds is not None else pipewire.VolumeBounds()

    def step(steps: int) -> float:
        state = pipewire.adjust_volume(target, steps, effective_bounds, runner=runner, env=env)
        return state.level

    return step


# -- helpers -----------------------------------------------------------------


def _stderr_log(record: LogRecord) -> None:
    """The default sink: diagnostics to stderr, results to stdout (CLI contract)."""
    print(record.render(), file=sys.stderr)


def _min_confidence(config: Config) -> float:
    """The configured minimum confidence, or this module's default.

    ``Config.min_confidence`` does the type/range validation; the default
    lives here, with the gate it guards.
    """
    configured = config.min_confidence
    return DEFAULT_MIN_CONFIDENCE if configured is None else configured


def _positive_int(value: object, fallback: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return fallback
    return value


def _limits_config(config: Config) -> LimitsConfig:
    try:
        return LimitsConfig.from_dict(dict(config.rate_limits))
    except (KeyError, TypeError, ValueError):
        return FALLBACK_LIMITS


def _strict_delay_seconds(config: Config) -> float:
    value = config.strict_mode_delay_seconds
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        return DEFAULT_STRICT_DELAY_SECONDS
    return float(value)


def _is_no_decision(decision: Decision) -> bool:
    return (
        decision.klass == NO_DECISION.klass
        and decision.intent == NO_DECISION.intent
        and float(decision.confidence) == NO_DECISION.confidence
    )


# -- the pipeline ------------------------------------------------------------


def power_direction(planned: "PlannedAction") -> str | None:
    """``"on"`` / ``"off"`` for an AC power change, ``None`` for anything else.

    The rate limiter is asymmetric for power (a quick OFF is safe, ON after OFF
    must wait for the compressor) and symmetric for everything else.
    """
    if planned.tool == TOOL_SENSIBO and planned.value in ("on", "off"):
        return planned.value
    return None


class Pipeline:
    """Transcript events in, at most one whitelisted tool call out."""

    def __init__(
        self,
        *,
        decider: Decider,
        config: Config,
        mode_provider: Callable[[], Any],
        pod_id: str,
        pod_alias: str = "ac",
        volume_key: str = "self",
        volume_alias: str = "spk",
        ac_power: Optional[Callable[..., Mapping[str, Any]]] = None,
        ac_status: Optional[Callable[[str], Mapping[str, Any]]] = None,
        volume_step: Optional[Callable[[int], Any]] = None,
        speak: Optional[Callable[[str], None]] = None,
        playback_notifier: Optional[Callable[[bool], None]] = None,
        rate_limiter: Optional[RateLimiter] = None,
        delay_timer: Optional[DelayTimer] = None,
        context: Optional[ContextWindow] = None,
        clock: Clock = system_clock,
        joiner_clock: Optional[Callable[[], float]] = None,
        join_gap_ms: Optional[int] = None,
        log: Optional[Callable[[LogRecord], None]] = None,
        apply: bool = False,
        min_confidence: Optional[float] = None,
        playback_tail_seconds: float = DEFAULT_PLAYBACK_TAIL_SECONDS,
        recent_capacity: Optional[int] = None,
        log_capacity: int = DEFAULT_LOG_CAPACITY,
    ) -> None:
        self._decider = decider
        self._config = config
        self._mode_provider = mode_provider
        self._pod_id = pod_id
        self._pod_alias = pod_alias
        self._volume_key = volume_key
        self._volume_alias = volume_alias
        self._ac_power = ac_power
        self._ac_status = ac_status
        self._volume_step = volume_step
        self._speak = speak
        self._playback_notifier = playback_notifier
        self._clock = clock
        self._log = log if log is not None else _stderr_log
        self._apply = bool(apply)
        self._min_confidence = (
            _min_confidence(config) if min_confidence is None else float(min_confidence)
        )
        self._playback_tail = float(playback_tail_seconds)

        gap = join_gap_ms if join_gap_ms is not None else config.join_gap_ms
        gap_ms = _positive_int(gap, DEFAULT_JOIN_GAP_MS)
        joiner_kwargs: dict[str, Any] = {}
        if joiner_clock is not None:
            joiner_kwargs["clock"] = joiner_clock
        self.joiner = TranscriptJoiner(
            gap_threshold_ms=gap_ms, on_utterance=self._on_utterance, **joiner_kwargs
        )

        self.rate_limiter = (
            rate_limiter if rate_limiter is not None else RateLimiter(_limits_config(config), clock)
        )
        self.delay_timer = (
            delay_timer
            if delay_timer is not None
            else DelayTimer(_strict_delay_seconds(config), clock)
        )
        self.context = context if context is not None else ContextWindow(clock=clock)

        capacity = recent_capacity
        if capacity is None:
            capacity = _positive_int(
                config.ring_sizes.get("transcript_buffer"), DEFAULT_RECENT_CAPACITY
            )
        self._recent: BoundedRing[RecentUtterance] = BoundedRing(capacity)
        self._records: BoundedRing[LogRecord] = BoundedRing(log_capacity)
        # Decide latencies are kept separately from the transcript ring so a
        # small transcript ring does not also shrink the latency sample the
        # dashboard's percentiles are computed from.
        self._latencies: BoundedRing[float] = BoundedRing(DEFAULT_LATENCY_CAPACITY)

        self._audio_ms: Optional[int] = None
        self._chain_suppressed = False
        self._playback_active = False
        self._playback_until = float("-inf")
        self._released: set[Any] = set()

    # -- introspection ----------------------------------------------------

    def recent(self) -> list[RecentUtterance]:
        """The in-memory ring of recent utterances (the only place text lives)."""
        return list(self._recent)

    def decide_latencies(self) -> list[float]:
        """Recent decide-call durations in milliseconds, newest last.

        Shaped for the dashboard's ``latency_provider``: a plain list of
        numbers, bounded, memory-only, and carrying nothing that could
        identify an utterance. An invalid decision contributes nothing --
        a timing without a usable decision would only skew the percentiles.
        """
        return list(self._latencies)

    @property
    def log_records(self) -> list[LogRecord]:
        return list(self._records)

    @property
    def apply(self) -> bool:
        return self._apply

    def __repr__(self) -> str:  # counts only, never text
        return (
            f"Pipeline(apply={self._apply}, recent={len(self._recent)}, "
            f"records={len(self._records)}, pending={len(self.delay_timer)})"
        )

    __str__ = __repr__

    # -- audio / own voice -------------------------------------------------

    def mark_playback(self, active: bool) -> None:
        """Mark the agent's own TTS as playing (or just finished).

        This changes nothing about capture: the mic keeps streaming so a
        household member can still be heard over the agent. Own-voice
        transcripts are discarded downstream instead --- here --- for as long
        as playback lasts plus a tail, because the ASR keeps emitting for a
        moment after the audio stops.
        """
        active = bool(active)
        self._playback_active = active
        if not active:
            self._playback_until = self._clock() + self._playback_tail
        if self._playback_notifier is not None:
            self._playback_notifier(active)

    def _in_playback_window(self) -> bool:
        return self._playback_active or self._clock() < self._playback_until

    # -- events ------------------------------------------------------------

    def handle_event(self, event: Any) -> None:
        """Feed one lobes event (a :class:`LobesEvent` or a wire dict)."""
        kind, data = _normalize(event)
        if kind in _RESET_KINDS:
            self.joiner.reset()
            self._chain_suppressed = False
            return
        wire_type = data.get("type") or ""
        if not wire_type:
            return
        at_ms = data.get("at_ms")
        if wire_type in _BOUNDARY_TYPES and isinstance(at_ms, int):
            self._audio_ms = at_ms if self._audio_ms is None else max(self._audio_ms, at_ms)
        self.joiner.handle_event(data)
        if wire_type in _SPEECH_STARTED_TYPES and self._in_playback_window():
            # Evaluated AFTER the joiner, so a chain the joiner just closed
            # is judged by its own segments, not by this new one.
            self._chain_suppressed = True

    def poll(self, now_ms: Optional[int] = None) -> None:
        """Advance both timelines: the joiner's, then the strict-mode delay.

        The joiner mixes two clocks. When boundary events carry ``at_ms``,
        gaps live on the audio-stream timeline, so that is what it must be
        polled with (the caller's ``now_ms``, or the latest ``at_ms`` seen).
        When they do not, the joiner samples its own injected receive clock,
        and is polled with no argument at all.
        """
        if now_ms is not None:
            self._audio_ms = now_ms if self._audio_ms is None else max(self._audio_ms, now_ms)
        if self._audio_ms is not None:
            self.joiner.poll(self._audio_ms)
        else:
            self.joiner.poll()
        self._release_due()

    # -- the utterance handler --------------------------------------------

    def _on_utterance(self, text: str) -> None:
        """The joiner's callback. Never raises, never leaks.

        An exception from any adapter is reported as its TYPE NAME only: the
        message can quote the transcript, the pod id or an API response, and
        a log line may carry none of those.
        """
        try:
            self._handle_utterance(text)
        except Exception as exc:  # noqa: BLE001 - a bug must not deafen the agent
            self._record(
                LogRecord(
                    klass="?",
                    intent="?",
                    verdict=VERDICT_ERROR,
                    action=ACTION_NONE,
                    reason=type(exc).__name__,
                )
            )
        finally:
            self._chain_suppressed = False

    def _handle_utterance(self, text: str) -> None:
        if self._chain_suppressed:
            self._record(
                LogRecord(
                    klass="-",
                    intent="-",
                    verdict=VERDICT_OWN_VOICE,
                    action=ACTION_NONE,
                    reason="own_playback",
                )
            )
            return

        resolved = self._mode_provider()
        mode = getattr(resolved, "mode", None)
        clock_untrusted = not bool(getattr(resolved, "clock_trusted", True))
        ac_state = self._read_ac_state()

        started = self._clock()
        decision = self._decider.decide(
            text, self.context, mode=mode or "strict", ac_state=ac_state
        )
        latency_ms = max(0.0, (self._clock() - started) * 1000.0)

        klass = getattr(decision, "klass", None)
        intent = getattr(decision, "intent", None)
        confidence = getattr(decision, "confidence", None)
        if (
            klass not in CLASSES
            or intent not in INTENTS
            or isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
            or not 0.0 <= float(confidence) <= 1.0
        ):
            # Untrusted input that does not even fit the vocabulary this repo
            # agreed to talk about. Nothing is remembered from it either.
            self._record(
                LogRecord(
                    klass="?",
                    intent="?",
                    verdict=VERDICT_INVALID_DECISION,
                    action=ACTION_NONE,
                    reason="bad_shape",
                )
            )
            return

        self._remember(text, decision, latency_ms)

        if _is_no_decision(decision):
            # Down, slow or malformed: do nothing, once, with a reason code.
            # No retry, no queue.
            self._record(
                LogRecord(
                    klass=klass,
                    intent=intent,
                    verdict=VERDICT_NO_DECISION,
                    action=ACTION_NONE,
                    reason=getattr(decision, "reason", "") or "no_decision",
                )
            )
            return

        if float(confidence) < self._min_confidence:
            self._log_refusal(klass, intent, VERDICT_LOW_CONFIDENCE)
            return

        if not may_act(mode, klass, clock_untrusted=clock_untrusted):
            self._log_refusal(klass, intent, VERDICT_GATE_REFUSED)
            return

        effective_mode = "strict" if clock_untrusted else mode
        if intent == "status":
            self._handle_status(klass, intent, effective_mode, ac_state)
            return

        planned = self._plan(intent)
        if planned is None:
            self._log_refusal(klass, intent, VERDICT_NO_ACTION)
            return

        self._approve(klass, intent, effective_mode, planned, ac_state, delayed=False)

    # -- gates -------------------------------------------------------------

    def _plan(self, intent: str) -> Optional[PlannedAction]:
        entry = _TOOL_BY_INTENT.get(intent)
        if entry is None:
            return None
        tool, action, value = entry
        if tool == TOOL_SENSIBO:
            return PlannedAction(
                tool=tool, key=self._pod_id, alias=self._pod_alias, action=action, value=value
            )
        return PlannedAction(
            tool=tool, key=self._volume_key, alias=self._volume_alias, action=action, value=value
        )

    def _approve(
        self,
        klass: str,
        intent: str,
        mode: Optional[str],
        planned: PlannedAction,
        ac_state: Optional[Mapping[str, Any]],
        *,
        delayed: bool,
    ) -> None:
        """Whitelist, arguments, limits, current state, strict delay, act.

        Runs in full both when an action is first proposed and again when a
        delayed one comes due --- the world may have moved in between, and
        every gate must still hold at the moment of acting.
        """
        if not self._config.is_whitelisted(planned.tool, planned.key):
            self._log_refusal(klass, intent, VERDICT_NOT_WHITELISTED, planned.alias)
            return

        if planned.tool == TOOL_SENSIBO:
            try:
                validate_ac_argument(planned.action, planned.value)
            except CliError:
                self._log_refusal(klass, intent, VERDICT_INVALID_ARGUMENTS, planned.alias)
                return

        allowed, _reason = self.rate_limiter.check(planned.key, direction=power_direction(planned))
        if not allowed:
            # The limiter's refusal record keys on the real pod id; the log
            # line gets the alias.
            self._log_refusal(klass, intent, VERDICT_RATE_LIMITED, planned.alias)
            return

        if planned.tool == TOOL_SENSIBO:
            current = (ac_state or {}).get("power")
            if current == planned.value:
                self._record(
                    LogRecord(
                        klass=klass,
                        intent=intent,
                        verdict=VERDICT_ALREADY_IN_STATE,
                        action=ACTION_NONE,
                        target=planned.alias,
                    )
                )
                return
            if current not in ("on", "off"):
                self._log_refusal(klass, intent, VERDICT_STATE_UNKNOWN, planned.alias)
                return

        if mode == "strict" and not delayed:
            self.delay_timer.schedule(planned)
            self._record(
                LogRecord(
                    klass=klass,
                    intent=intent,
                    verdict=VERDICT_DELAYED,
                    action=planned.name,
                    target=planned.alias,
                )
            )
            return

        self._execute(klass, intent, planned)

    def _execute(self, klass: str, intent: str, planned: PlannedAction) -> None:
        if planned.tool == TOOL_SENSIBO:
            if self._ac_power is None:
                self._log_refusal(klass, intent, VERDICT_NO_ADAPTER, planned.alias)
                return
            result = self._ac_power(planned.key, planned.value == "on", apply=self._apply)
            self.rate_limiter.record(planned.key, direction=power_direction(planned))
            acted = bool(isinstance(result, Mapping) and result.get("acted"))
            self._record(
                LogRecord(
                    klass=klass,
                    intent=intent,
                    verdict=VERDICT_ACTED if acted else VERDICT_DRY_RUN,
                    action=planned.name,
                    target=planned.alias,
                )
            )
            return

        if self._volume_step is None:
            self._log_refusal(klass, intent, VERDICT_NO_ADAPTER, planned.alias)
            return
        if not self._apply:
            # A volume change has no dry-run form of its own: not calling the
            # adapter IS the dry run.
            self.rate_limiter.record(planned.key, direction=power_direction(planned))
            self._record(
                LogRecord(
                    klass=klass,
                    intent=intent,
                    verdict=VERDICT_DRY_RUN,
                    action=planned.name,
                    target=planned.alias,
                )
            )
            return
        self._volume_step(1 if planned.value == "up" else -1)
        self.rate_limiter.record(planned.key, direction=power_direction(planned))
        self._record(
            LogRecord(
                klass=klass,
                intent=intent,
                verdict=VERDICT_ACTED,
                action=planned.name,
                target=planned.alias,
            )
        )

    def _release_due(self) -> None:
        """Run any strict-mode delayed action whose delay has elapsed.

        Each pending action is released at most once, and is re-gated (mode and
        clock trust, whitelist, limits, current state) before it runs. The
        utterance's class is NOT re-checked: only hint classes are ever
        scheduled, and hints may act in every mode. A restart drops every
        pending action, since the timer is memory-only.
        """
        due = self.delay_timer.due()
        # Forget releases the timer has already evicted: this record is then
        # bounded by the timer's ring, not by the listener's lifetime.
        self._released.intersection_update(due)
        for pending in due:
            if pending in self._released:
                continue
            self._released.add(pending)
            planned = pending.action
            if not isinstance(planned, PlannedAction):
                continue
            resolved = self._mode_provider()
            mode = getattr(resolved, "mode", None)
            clock_untrusted = not bool(getattr(resolved, "clock_trusted", True))
            effective_mode = "strict" if clock_untrusted else mode
            ac_state = self._read_ac_state() if planned.tool == TOOL_SENSIBO else None
            self._approve("delayed", "-", effective_mode, planned, ac_state, delayed=True)

    # -- speaking ----------------------------------------------------------

    def _handle_status(
        self,
        klass: str,
        intent: str,
        mode: Optional[str],
        ac_state: Optional[Mapping[str, Any]],
    ) -> None:
        """A spoken status statement --- on a weekday only.

        In strict mode the agent says nothing at all: speech is an action
        too, and a neutral statement is still the box talking back.
        """
        if mode != "weekday" or self._speak is None:
            self._log_refusal(klass, intent, VERDICT_GATE_REFUSED)
            return
        power = (ac_state or {}).get("power")
        if power == "on":
            text = SPEECH_AC_ON
        elif power == "off":
            text = SPEECH_AC_OFF
        else:
            text = SPEECH_AC_UNKNOWN
        self._say(text)
        self._record(
            LogRecord(
                klass=klass,
                intent=intent,
                verdict=VERDICT_ACTED,
                action=ACTION_SPEAK_STATUS,
                target=self._pod_alias,
            )
        )

    def _say(self, text: str) -> None:
        """Speak, with the mic still streaming and own-voice suppression armed."""
        if self._speak is None:
            return
        self.mark_playback(True)
        try:
            self._speak(text)
        finally:
            self.mark_playback(False)

    # -- plumbing ----------------------------------------------------------

    def _read_ac_state(self) -> Optional[dict[str, Any]]:
        if self._ac_status is None:
            return None
        state = self._ac_status(self._pod_id)
        return dict(state) if isinstance(state, Mapping) else None

    def _remember(self, text: str, decision: Decision, latency_ms: float) -> None:
        self._recent.append(
            RecentUtterance(
                at=self._clock(),
                text=text,
                klass=decision.klass,
                intent=decision.intent,
                confidence=float(decision.confidence),
                decide_latency_ms=float(latency_ms),
            )
        )
        self._latencies.append(float(latency_ms))
        self.context.add(text, decision)

    def _log_refusal(self, klass: str, intent: str, verdict: str, target: str = "") -> None:
        self._record(
            LogRecord(
                klass=klass, intent=intent, verdict=verdict, action=ACTION_NONE, target=target
            )
        )

    def _record(self, record: LogRecord) -> None:
        self._records.append(record)
        self._log(record)


def _normalize(event: Any) -> tuple[Optional[str], dict[str, Any]]:
    """One lobes event --- object or dict --- as ``(kind, wire dict)``."""
    if isinstance(event, Mapping):
        data = dict(event)
    else:
        data = {
            "kind": getattr(event, "kind", None),
            "type": getattr(event, "type", ""),
            "at_ms": getattr(event, "at_ms", None),
            "item_id": getattr(event, "item_id", None),
            "text": getattr(event, "text", ""),
        }
    kind = data.get("kind")
    if not data.get("type") and isinstance(kind, str):
        data["type"] = _WIRE_BY_KIND.get(kind, "")
    return (kind if isinstance(kind, str) else None), data

"""The decision pipeline, end to end on fixtures.

Every test here runs with no microphone, no lobes server, no Sensibo account
and no real sleeping: the decider is a :class:`ReplayDecider` built from
``tests/fixtures/pipeline/replay.json`, the actuators are fakes, and every
clock is hand-advanced.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from shabbos_goy.config import load_config
from shabbos_goy.decider import ContextWindow, Decision, ReplayDecider
from shabbos_goy.joiner import SpeechStart
from shabbos_goy.mode import ResolvedMode, parse_tzeit_definition, resolve_mode, set_override
from shabbos_goy.pipeline import Pipeline, pipewire_volume_stepper
from shabbos_goy.zmanim import Location, ZmanimRules, next_window

FIXTURES = Path(__file__).parent / "fixtures" / "pipeline"
POD = "PODFAKE1"
MARKER = "MARKER7QX"

HOT = "חם פה"
COLD = "קר לי"
NOISY = "רועש פה מדי"
FAINT = "קשה לשמוע"
TURN_IT_DOWN = "תוריד את הקול"
TURN_AC_ON = "תדליק את המזגן"
STATUS = "מה מצב המזגן"
HOT_UNSURE = "חם פה קצת"
CHITCHAT = "מה שלומך"
HOT_MARKED = f"{HOT} {MARKER}"


# --------------------------------------------------------------------------
# fakes
# --------------------------------------------------------------------------


class FakeClock:
    """A hand-advanced monotonic clock (seconds)."""

    def __init__(self, now: float = 10_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeAC:
    """Stands in for ``shabbos_goy.actuators.sensibo`` — never runs sensibo."""

    def __init__(self, state: str = "off") -> None:
        self.state = state
        self.power_calls: list[tuple[str, bool, bool]] = []
        self.status_calls = 0
        self.raises: Exception | None = None

    def power(self, pod_id: str, on: bool, *, apply: bool = False) -> dict:
        self.power_calls.append((pod_id, on, apply))
        if self.raises is not None:
            raise self.raises
        if apply:
            self.state = "on" if on else "off"
        return {"acted": bool(apply), "requested_apply": apply, "changes": {}}

    def status(self, pod_id: str) -> dict:
        self.status_calls += 1
        return {"power": self.state, "temperature": 28.0, "humidity": 41.0}


class FakeVolume:
    def __init__(self) -> None:
        self.steps: list[int] = []

    def __call__(self, steps: int) -> float:
        self.steps.append(steps)
        return 0.5


class FakeSpeaker:
    def __init__(self) -> None:
        self.said: list[str] = []

    def __call__(self, text: str) -> None:
        self.said.append(text)


class StubDecider:
    """Returns whatever it is given — including things a real decider never would."""

    def __init__(self, answer: object) -> None:
        self.answer = answer
        self.calls: list[tuple[str, str]] = []

    def decide(self, utterance, context, *, mode, ac_state=None):
        self.calls.append((utterance, context.render()))
        return self.answer


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def make_pipeline(**overrides):
    config = load_config(path=FIXTURES / "config.json")
    assert config.ok, config.error
    decider = ReplayDecider.from_file(FIXTURES / "replay.json")
    clock = overrides.pop("clock", None) or FakeClock()
    ac = overrides.pop("ac", None) or FakeAC()
    volume = overrides.pop("volume", None) or FakeVolume()
    speaker = overrides.pop("speaker", None) or FakeSpeaker()
    mode = overrides.pop("mode", "weekday")
    kwargs = dict(
        decider=overrides.pop("decider", decider),
        config=config,
        mode_provider=overrides.pop(
            "mode_provider",
            lambda: ResolvedMode(mode=mode, kinds=(), clock_trusted=True, overridden=False),
        ),
        pod_id=POD,
        ac_power=ac.power,
        ac_status=ac.status,
        volume_step=volume,
        speak=speaker,
        clock=clock,
    )
    kwargs.update(overrides)
    pipeline = Pipeline(**kwargs)
    return pipeline, ac, volume, speaker, clock


_SEQ = {"n": 0}


def feed(pipeline, text: str, *, start_ms: int | None = None) -> None:
    """Drive one complete utterance through the pipeline, then flush it."""
    _SEQ["n"] += 1
    item_id = f"item-{_SEQ['n']}"
    start = start_ms if start_ms is not None else _SEQ["n"] * 10_000
    stop = start + 800
    pipeline.handle_event(
        {"type": "input_audio_buffer.speech_started", "at_ms": start, "item_id": item_id}
    )
    pipeline.handle_event(
        {"type": "input_audio_buffer.speech_stopped", "at_ms": stop, "item_id": item_id}
    )
    pipeline.handle_event(
        {
            "type": "conversation.item.input_audio_transcription.completed",
            "item_id": item_id,
            "text": text,
        }
    )
    pipeline.poll(stop + 600)


def verdicts(pipeline) -> list[tuple[str, str]]:
    return [(record.verdict, record.action) for record in pipeline.log_records]


# --------------------------------------------------------------------------
# criterion 1: the pipeline is wired, and driven from fixture events only
# --------------------------------------------------------------------------


def test_fixture_events_drive_joiner_decider_gate_whitelist_limits_and_adapters() -> None:
    pipeline, ac, _volume, _speaker, _clock = make_pipeline(apply=True)
    feed(pipeline, HOT)

    assert ac.power_calls == [(POD, True, True)]
    assert verdicts(pipeline) == [("acted", "ac_power_on")]
    record = pipeline.log_records[-1]
    assert (record.klass, record.intent) == ("remark", "cool")


def test_half_sentences_are_joined_before_the_decider_sees_them() -> None:
    pipeline, ac, _volume, _speaker, _clock = make_pipeline(apply=True)
    for index, half in enumerate(("חם", "פה")):
        item_id = f"half-{index}"
        pipeline.handle_event(
            {
                "type": "input_audio_buffer.speech_started",
                "at_ms": 1000 + index * 400,
                "item_id": item_id,
            }
        )
        pipeline.handle_event(
            {
                "type": "input_audio_buffer.speech_stopped",
                "at_ms": 1100 + index * 400,
                "item_id": item_id,
            }
        )
        pipeline.handle_event(
            {
                "type": "conversation.item.input_audio_transcription.completed",
                "item_id": item_id,
                "text": half,
            }
        )
    pipeline.poll(2600)

    # Neither half is actionable on its own; only the joined utterance is.
    assert [entry.text for entry in pipeline.recent()] == [HOT]
    assert ac.power_calls == [(POD, True, True)]


def test_dry_run_is_the_default_and_apply_is_passed_down() -> None:
    pipeline, ac, _volume, _speaker, _clock = make_pipeline()
    feed(pipeline, HOT)
    assert ac.power_calls == [(POD, True, False)]
    assert verdicts(pipeline) == [("dry_run", "ac_power_on")]


def test_context_window_grows_and_is_handed_to_the_decider() -> None:
    clock = FakeClock()
    context = ContextWindow(clock=clock)
    decider = StubDecider(
        Decision(klass="unrelated", intent="none", confidence=0.9, source="stub", reason="ok")
    )
    pipeline, _ac, _volume, _speaker, _clock = make_pipeline(
        decider=decider, context=context, clock=clock
    )
    feed(pipeline, CHITCHAT)
    feed(pipeline, HOT)

    assert len(context) == 2
    # The second call saw the first utterance as context.
    assert CHITCHAT in decider.calls[1][1]


# --------------------------------------------------------------------------
# criterion 2: the six behaviours
# --------------------------------------------------------------------------


def test_hot_hint_powers_on() -> None:
    pipeline, ac, _volume, speaker, _clock = make_pipeline(apply=True, ac=FakeAC(state="off"))
    feed(pipeline, HOT)
    assert ac.power_calls == [(POD, True, True)]
    assert ac.state == "on"
    assert speaker.said == []


def test_cold_hint_powers_off() -> None:
    pipeline, ac, _volume, _speaker, _clock = make_pipeline(apply=True, ac=FakeAC(state="on"))
    feed(pipeline, COLD)
    assert ac.power_calls == [(POD, False, True)]
    assert ac.state == "off"


def test_already_in_state_does_nothing_and_says_nothing() -> None:
    ac = FakeAC(state="on")
    pipeline, ac, _volume, speaker, _clock = make_pipeline(apply=True, ac=ac)
    feed(pipeline, HOT)
    assert ac.power_calls == []
    assert speaker.said == []
    assert verdicts(pipeline) == [("already_in_state", "none")]


def test_unknown_ac_state_acts_on_nothing() -> None:
    ac = FakeAC(state="unknown")
    pipeline, ac, _volume, speaker, _clock = make_pipeline(apply=True, ac=ac)
    feed(pipeline, HOT)
    assert ac.power_calls == []
    assert speaker.said == []
    assert verdicts(pipeline) == [("state_unknown", "none")]


def test_weekday_volume_imperative_changes_volume() -> None:
    pipeline, _ac, volume, _speaker, _clock = make_pipeline(apply=True, mode="weekday")
    feed(pipeline, TURN_IT_DOWN)
    assert volume.steps == [-1]
    assert verdicts(pipeline) == [("acted", "volume_down")]


def test_strict_mode_ignores_a_volume_imperative() -> None:
    clock = FakeClock()
    pipeline, _ac, volume, speaker, _clock = make_pipeline(apply=True, mode="strict", clock=clock)
    feed(pipeline, TURN_IT_DOWN)
    clock.advance(3600)
    pipeline.poll(60_000)
    assert volume.steps == []
    assert speaker.said == []
    assert verdicts(pipeline) == [("gate_refused", "none")]


def test_strict_mode_loudness_remark_lowers_volume_after_the_delay() -> None:
    clock = FakeClock()
    pipeline, _ac, volume, _speaker, _clock = make_pipeline(apply=True, mode="strict", clock=clock)
    feed(pipeline, NOISY)
    assert volume.steps == []
    assert verdicts(pipeline) == [("delayed", "volume_down")]

    pipeline.poll(60_000)
    assert volume.steps == []  # the delay has not elapsed yet

    clock.advance(20)
    pipeline.poll(70_000)
    assert volume.steps == [-1]
    assert verdicts(pipeline)[-1] == ("acted", "volume_down")


def test_weekday_discomfort_raises_volume() -> None:
    pipeline, _ac, volume, _speaker, _clock = make_pipeline(apply=True)
    feed(pipeline, FAINT)
    assert volume.steps == [1]


def test_status_is_spoken_only_on_a_weekday() -> None:
    pipeline, _ac, _volume, speaker, _clock = make_pipeline(apply=True, mode="weekday")
    feed(pipeline, STATUS)
    assert len(speaker.said) == 1
    assert "?" not in speaker.said[0]

    strict, _ac2, _v2, strict_speaker, _c2 = make_pipeline(apply=True, mode="strict")
    feed(strict, STATUS)
    assert strict_speaker.said == []


# --------------------------------------------------------------------------
# criterion 3: own-voice suppression
# --------------------------------------------------------------------------


def test_own_playback_and_its_tail_discard_transcripts_but_a_later_hint_acts() -> None:
    clock = FakeClock()
    pipeline, ac, _volume, _speaker, _clock = make_pipeline(
        apply=True, clock=clock, playback_tail_seconds=2.0
    )

    pipeline.mark_playback(True)
    feed(pipeline, HOT)
    assert ac.power_calls == []
    assert verdicts(pipeline) == [("own_voice", "none")]

    pipeline.mark_playback(False)
    clock.advance(1.0)  # still inside the tail
    feed(pipeline, HOT)
    assert ac.power_calls == []
    assert verdicts(pipeline)[-1] == ("own_voice", "none")

    clock.advance(5.0)  # past the tail: a genuine hint
    feed(pipeline, HOT)
    assert ac.power_calls == [(POD, True, True)]


def test_own_voice_suppression_never_stops_the_audio_stream() -> None:
    pipeline, _ac, _volume, _speaker, _clock = make_pipeline()
    notified: list[bool] = []
    pipeline._playback_notifier = notified.append  # what the lobes client gets told
    pipeline.mark_playback(True)
    pipeline.mark_playback(False)
    # The flag is advisory only: nothing here mutes or stops capture.
    assert notified == [True, False]


def test_speaking_suppresses_the_agents_own_words() -> None:
    clock = FakeClock()
    pipeline, _ac, _volume, speaker, _clock = make_pipeline(
        apply=True, mode="weekday", clock=clock, playback_tail_seconds=2.0
    )
    feed(pipeline, STATUS)
    assert speaker.said  # it spoke
    feed(pipeline, HOT)  # immediately overheard: its own tail
    assert verdicts(pipeline)[-1] == ("own_voice", "none")


# --------------------------------------------------------------------------
# criterion 4: logging, privacy, exception handling
# --------------------------------------------------------------------------

_LOG_KEY_RE = re.compile(r"(\w+)=")
_ALLOWED_LOG_KEYS = {"class", "intent", "verdict", "action", "reason", "target"}


def test_log_lines_carry_class_intent_verdict_and_action_only(capsys) -> None:
    pipeline, _ac, _volume, _speaker, _clock = make_pipeline(apply=True)
    feed(pipeline, HOT)
    captured = capsys.readouterr()
    line = captured.err.strip()

    assert set(_LOG_KEY_RE.findall(line)) <= _ALLOWED_LOG_KEYS
    assert "class=remark" in line
    assert "intent=cool" in line
    assert "verdict=acted" in line
    assert "action=ac_power_on" in line
    assert HOT not in captured.out + captured.err
    assert POD not in captured.out + captured.err


def test_a_forced_exception_leaks_the_marker_to_neither_stdout_nor_stderr(capsys) -> None:
    ac = FakeAC(state="off")
    ac.raises = RuntimeError(f"sensibo blew up while handling {HOT_MARKED} for pod {POD}")
    pipeline, _ac, _volume, _speaker, _clock = make_pipeline(apply=True, ac=ac)

    feed(pipeline, HOT_MARKED)  # must not propagate out of the joiner callback

    captured = capsys.readouterr()
    assert MARKER not in captured.out
    assert MARKER not in captured.err
    assert POD not in captured.out + captured.err
    assert "reason=RuntimeError" in captured.err
    assert verdicts(pipeline)[-1] == ("error", "none")


def test_recent_utterance_text_goes_only_to_the_in_memory_ring(capsys) -> None:
    pipeline, _ac, _volume, _speaker, _clock = make_pipeline(apply=True)
    feed(pipeline, HOT)
    captured = capsys.readouterr()

    assert [entry.text for entry in pipeline.recent()] == [HOT]
    assert HOT not in captured.out + captured.err
    assert all(HOT not in record.render() for record in pipeline.log_records)


def test_the_recent_ring_is_bounded_and_empty_on_a_fresh_pipeline() -> None:
    pipeline, _ac, _volume, _speaker, _clock = make_pipeline(recent_capacity=2)
    assert pipeline.recent() == []
    for text in (CHITCHAT, CHITCHAT, CHITCHAT):
        feed(pipeline, text)
    assert len(pipeline.recent()) == 2


def test_a_rate_limit_refusal_keeps_the_pod_id_out_of_the_log(capsys) -> None:
    pipeline, ac, _volume, _speaker, _clock = make_pipeline(apply=True, ac=FakeAC(state="off"))
    feed(pipeline, HOT)
    ac.state = "off"  # pretend the AC fell back off
    feed(pipeline, HOT)

    captured = capsys.readouterr()
    assert verdicts(pipeline)[-1] == ("rate_limited", "none")
    assert POD not in captured.out + captured.err
    # ... while the limiter's own refusal record does key on the pod id.
    assert pipeline.rate_limiter.refusal_log[-1].key == POD


# --------------------------------------------------------------------------
# untrusted decisions, failure paths
# --------------------------------------------------------------------------


def test_no_decision_does_nothing_and_logs_one_line_with_a_reason() -> None:
    pipeline, ac, _volume, _speaker, _clock = make_pipeline(apply=True)
    feed(pipeline, "משהו שלא הוקלט")  # not in the replay file
    assert ac.power_calls == []
    assert len(pipeline.log_records) == 1
    assert pipeline.log_records[0].verdict == "no_decision"
    assert pipeline.log_records[0].reason == "not_recorded"


def test_a_hostile_decision_is_re_validated_in_the_pipeline() -> None:
    class Hostile:
        klass = "imperative-but-fine-really"
        intent = "rm -rf"
        confidence = 1.0
        source = "hostile"
        reason = "x"

    pipeline, ac, volume, _speaker, _clock = make_pipeline(
        apply=True, decider=StubDecider(Hostile())
    )
    feed(pipeline, HOT)
    assert ac.power_calls == []
    assert volume.steps == []
    assert verdicts(pipeline) == [("invalid_decision", "none")]


def test_low_confidence_acts_on_nothing() -> None:
    pipeline, ac, _volume, _speaker, _clock = make_pipeline(apply=True)
    feed(pipeline, HOT_UNSURE)
    assert ac.power_calls == []
    assert verdicts(pipeline) == [("low_confidence", "none")]


def test_a_pod_missing_from_the_whitelist_never_acts() -> None:
    pipeline, ac, _volume, _speaker, _clock = make_pipeline(apply=True, pod_id="NOTWHITELISTED")
    feed(pipeline, HOT)
    assert ac.power_calls == []
    assert verdicts(pipeline) == [("not_whitelisted", "none")]


def test_an_imperative_never_acts_in_strict_mode_even_later() -> None:
    clock = FakeClock()
    pipeline, ac, _volume, _speaker, _clock = make_pipeline(apply=True, mode="strict", clock=clock)
    feed(pipeline, TURN_AC_ON)
    clock.advance(10_000)
    pipeline.poll(900_000)
    assert ac.power_calls == []
    assert len(pipeline.delay_timer) == 0


def test_an_untrusted_clock_forces_the_strict_column() -> None:
    pipeline, ac, _volume, _speaker, _clock = make_pipeline(
        apply=True,
        mode_provider=lambda: ResolvedMode(
            mode="weekday", kinds=(), clock_trusted=False, overridden=False
        ),
    )
    feed(pipeline, TURN_AC_ON)  # a weekday-legal imperative
    assert ac.power_calls == []
    assert verdicts(pipeline) == [("gate_refused", "none")]


def test_a_joiner_bypassed_utterance_with_no_start_instant_forces_the_strict_column() -> None:
    """strict-window-close-boundary, t1, AC2: a reconnect-orphaned or
    joiner-bypassed utterance carries no speech-start instant. It must be
    judged by the STRICTER of the (unknowable) start-time mode and the
    decision-time mode -- here, decision-time is a weekday-legal imperative,
    so only forcing strict on the missing instant can explain a refusal."""
    pipeline, ac, _volume, _speaker, _clock = make_pipeline(apply=True)  # mode="weekday"

    pipeline._handle_utterance(TURN_AC_ON, None)  # bypasses the joiner entirely

    assert ac.power_calls == []
    assert verdicts(pipeline) == [("gate_refused", "none")]


def test_a_normal_joiner_driven_utterance_still_carries_its_start_instant() -> None:
    """Sanity check for the plumbing itself (AC1): an utterance driven
    through the real joiner path (not bypassed) is NOT forced strict on a
    weekday, because it DOES carry a start instant."""
    pipeline, ac, _volume, _speaker, _clock = make_pipeline(apply=True)  # mode="weekday"

    feed(pipeline, TURN_AC_ON)

    assert ac.power_calls == [(POD, True, True)]
    assert verdicts(pipeline) == [("acted", "ac_power_on")]


def test_an_unrelated_utterance_does_nothing() -> None:
    pipeline, ac, volume, speaker, _clock = make_pipeline(apply=True)
    feed(pipeline, CHITCHAT)
    assert (ac.power_calls, volume.steps, speaker.said) == ([], [], [])
    assert verdicts(pipeline) == [("gate_refused", "none")]


# --------------------------------------------------------------------------
# the two timelines
# --------------------------------------------------------------------------


def test_poll_uses_audio_stream_time_when_boundary_events_carry_at_ms() -> None:
    pipeline, ac, _volume, _speaker, _clock = make_pipeline(apply=True)
    pipeline.handle_event(
        {"type": "input_audio_buffer.speech_started", "at_ms": 1000, "item_id": "a"}
    )
    pipeline.handle_event(
        {"type": "input_audio_buffer.speech_stopped", "at_ms": 1800, "item_id": "a"}
    )
    pipeline.handle_event(
        {
            "type": "conversation.item.input_audio_transcription.completed",
            "item_id": "a",
            "text": HOT,
        }
    )
    pipeline.poll(1900)  # audio-stream time, still inside the join gap
    assert ac.power_calls == []
    pipeline.poll(2400)  # gap elapsed on the audio-stream timeline
    assert ac.power_calls == [(POD, True, True)]


def test_poll_falls_back_to_the_receive_clock_when_at_ms_is_absent() -> None:
    joiner_clock = FakeClock(0.0)
    pipeline, ac, _volume, _speaker, _clock = make_pipeline(
        apply=True, joiner_clock=lambda: joiner_clock.now
    )
    pipeline.handle_event({"type": "input_audio_buffer.speech_started", "item_id": "a"})
    pipeline.handle_event({"type": "input_audio_buffer.speech_stopped", "item_id": "a"})
    pipeline.handle_event(
        {
            "type": "conversation.item.input_audio_transcription.completed",
            "item_id": "a",
            "text": HOT,
        }
    )
    pipeline.poll()
    assert ac.power_calls == []
    joiner_clock.advance(600)  # milliseconds on the receive timeline
    pipeline.poll()
    assert ac.power_calls == [(POD, True, True)]


def test_a_lost_connection_resets_the_joiner_and_keeps_the_context() -> None:
    clock = FakeClock()
    context = ContextWindow(clock=clock)
    pipeline, ac, _volume, _speaker, _clock = make_pipeline(
        apply=True, clock=clock, context=context
    )
    feed(pipeline, CHITCHAT)
    assert len(context) == 1

    pipeline.handle_event(
        {"type": "input_audio_buffer.speech_started", "at_ms": 90_000, "item_id": "cut"}
    )
    pipeline.handle_event(
        {
            "type": "conversation.item.input_audio_transcription.completed",
            "item_id": "cut",
            "text": HOT,
        }
    )
    pipeline.handle_event({"kind": "connection_lost"})
    pipeline.poll(200_000)

    assert ac.power_calls == []  # the half-turn is gone, never replayed
    assert pipeline.joiner.dropped_reconnect == 1
    assert len(context) == 1  # context is only context; it survives


def test_lobes_event_objects_are_accepted_as_well_as_dicts() -> None:
    from shabbos_goy.lobes import events as ev

    pipeline, ac, _volume, _speaker, _clock = make_pipeline(apply=True)
    for wire in (
        {"type": "input_audio_buffer.speech_started", "at_ms": 100, "item_id": "z"},
        {"type": "input_audio_buffer.speech_stopped", "at_ms": 900, "item_id": "z"},
        {
            "type": "conversation.item.input_audio_transcription.completed",
            "item_id": "z",
            "text": HOT,
        },
    ):
        pipeline.handle_event(ev.normalize_event(wire))
    pipeline.poll(1600)
    assert ac.power_calls == [(POD, True, True)]


# --------------------------------------------------------------------------
# the volume adapter factory (fake runner — never a real wpctl)
# --------------------------------------------------------------------------


def test_pipewire_volume_stepper_reads_and_writes_through_the_injected_runner() -> None:
    calls: list[list[str]] = []

    class Result:
        returncode = 0
        stdout = "Volume: 0.50\n"
        stderr = ""

    def runner(argv, **_kwargs):
        calls.append(argv)
        return Result()

    step = pipewire_volume_stepper("alsa_output.usb-fake", runner=runner)
    step(-1)

    assert calls[0][:2] == ["wpctl", "get-volume"]
    assert calls[1][:2] == ["wpctl", "set-volume"]
    assert calls[1][-1] == "0.45"


def test_the_pipeline_module_does_not_import_the_rule_classifier() -> None:
    source = (Path(__file__).parent.parent / "shabbos_goy" / "pipeline.py").read_text(
        encoding="utf-8"
    )
    imports = [
        line
        for line in source.splitlines()
        if re.match(r"\s*(from|import)\s", line) and "classifier" in line
    ]
    assert imports == []


def test_the_fixture_replay_file_is_a_flat_object_of_decisions() -> None:
    data = json.loads((FIXTURES / "replay.json").read_text(encoding="utf-8"))
    assert all(set(value) == {"class", "intent", "confidence"} for value in data.values())


@pytest.mark.parametrize("mode", ["weekday", "strict"])
def test_nothing_is_persisted_across_a_restart(mode: str) -> None:
    clock = FakeClock()
    pipeline, _ac, _volume, _speaker, _clock = make_pipeline(apply=True, mode=mode, clock=clock)
    feed(pipeline, NOISY)

    fresh, _ac2, fresh_volume, _s2, _c2 = make_pipeline(apply=True, mode=mode)
    fresh.poll(900_000)
    assert fresh_volume.steps == []
    assert fresh.recent() == []
    assert len(fresh.delay_timer) == 0


# --------------------------------------------------------------------------
# t14: per-decision timing and confidence (what the dashboard shows)
# --------------------------------------------------------------------------


class SlowDecider:
    """A decider that costs a known amount of time on the injected clock."""

    source = "slow:p1"

    def __init__(self, clock, cost: float, answer: Decision) -> None:
        self.clock = clock
        self.cost = cost
        self.answer = answer

    def decide(self, utterance, context, *, mode, ac_state=None):
        self.clock.advance(self.cost)
        return self.answer


def test_a_recent_utterance_carries_its_confidence_and_decide_latency() -> None:
    pipeline, _ac, _volume, _speaker, _clock = make_pipeline()
    feed(pipeline, HOT)

    recent = pipeline.recent()
    assert len(recent) == 1
    assert recent[0].confidence == pytest.approx(0.9)
    assert recent[0].decide_latency_ms == pytest.approx(0.0)


def test_decide_latency_is_measured_on_the_injected_clock() -> None:
    clock = FakeClock()
    answer = Decision(klass="remark", intent="cool", confidence=0.9, source="slow:p1", reason="ok")
    pipeline, _ac, _volume, _speaker, _clock = make_pipeline(
        clock=clock, decider=SlowDecider(clock, 0.25, answer)
    )
    feed(pipeline, HOT)

    assert pipeline.recent()[0].decide_latency_ms == pytest.approx(250.0)
    assert pipeline.decide_latencies() == [pytest.approx(250.0)]


def test_decide_latencies_are_a_bounded_ring_of_plain_numbers() -> None:
    pipeline, _ac, _volume, _speaker, _clock = make_pipeline(recent_capacity=3)
    for _ in range(5):
        feed(pipeline, CHITCHAT)

    samples = pipeline.decide_latencies()
    assert len(samples) <= 20
    assert all(isinstance(value, float) for value in samples)


def test_an_invalid_decision_records_no_timing_and_no_text() -> None:
    class Broken:
        source = "broken"

        def decide(self, utterance, context, *, mode, ac_state=None):
            return Decision(klass="nonsense", intent="cool", confidence=0.9, source="x", reason="")

    pipeline, _ac, _volume, _speaker, _clock = make_pipeline(decider=Broken())
    feed(pipeline, HOT)

    assert pipeline.recent() == []
    assert pipeline.decide_latencies() == []


def test_a_cold_remark_a_minute_after_a_power_on_switches_it_off() -> None:
    """Found live: a symmetric 10-minute lockout left a cold person unable to stop the AC."""
    pipeline, ac, _volume, _speaker, clock = make_pipeline(apply=True, ac=FakeAC(state="off"))
    feed(pipeline, HOT)
    assert ac.state == "on"
    clock.advance(70)
    feed(pipeline, COLD)
    assert ac.state == "off"
    assert [call[1] for call in ac.power_calls] == [True, False]


def test_a_hot_remark_soon_after_a_power_off_waits_for_the_compressor() -> None:
    pipeline, ac, _volume, _speaker, clock = make_pipeline(apply=True, ac=FakeAC(state="on"))
    feed(pipeline, COLD)
    assert ac.state == "off"
    clock.advance(120)
    feed(pipeline, HOT)
    assert ac.state == "off"
    assert ("rate_limited", "none") in [(r.verdict, r.action) for r in pipeline.log_records]
    clock.advance(130)  # 250 s after the power-off
    feed(pipeline, HOT)
    assert ac.state == "on"


def test_the_record_of_released_delayed_actions_stays_bounded() -> None:
    """Found in review: the released-set grew by one per delayed action, forever."""
    pipeline, _ac, _volume, _speaker, clock = make_pipeline(mode="strict", apply=False)
    for _ in range(300):
        feed(pipeline, HOT)
        clock.advance(20)  # past the 15 s delay: the action is released
        pipeline.poll()
        clock.advance(90000)  # more than a day: past every interval and the daily cap
    assert len(pipeline.delay_timer) <= 64
    assert len(pipeline._released) <= 64


# --------------------------------------------------------------------------
# review round: limits, config bounds, and the audio timeline
# --------------------------------------------------------------------------


def test_an_actuation_that_did_not_happen_does_not_consume_the_limits() -> None:
    """Found in review: a timeout / non-zero exit / bad JSON charged the limiter.

    The adapter reports ``acted: False`` for every one of those, exactly as it
    does for a dry run. On an APPLYING listener that means the AC never moved,
    so neither the compressor debounce nor the daily cap may be spent.
    """
    calls: list[tuple[str, bool, bool]] = []

    def failing_power(pod_id: str, on: bool, *, apply: bool = False) -> dict:
        calls.append((pod_id, on, apply))
        return {"acted": False, "requested_apply": apply, "changes": {}}

    pipeline, _ac, _volume, _speaker, _clock = make_pipeline(apply=True, ac_power=failing_power)
    feed(pipeline, HOT)
    feed(pipeline, HOT)

    assert calls == [(POD, True, True), (POD, True, True)]
    assert "rate_limited" not in [record.verdict for record in pipeline.log_records]
    # ... and it is reported as what it was. "dry_run" would tell an operator
    # reading the log that nothing was attempted, on a listener that was applying.
    verdicts = [record.verdict for record in pipeline.log_records]
    assert "dry_run" not in verdicts
    assert verdicts.count("error") == 2


def test_a_dry_run_session_spends_the_limits_exactly_like_a_real_one() -> None:
    pipeline, _ac, _volume, _speaker, _clock = make_pipeline()
    feed(pipeline, HOT)
    feed(pipeline, HOT)

    assert verdicts(pipeline) == [("dry_run", "ac_power_on"), ("rate_limited", "none")]


def test_the_context_window_is_built_from_the_configured_bounds(tmp_path) -> None:
    raw = json.loads((FIXTURES / "config.json").read_text(encoding="utf-8"))
    raw["context_window"] = {"max_items": 2, "max_age_seconds": 30, "max_render_chars": 40}
    path = tmp_path / "config.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    config = load_config(path=path)
    assert config.ok, config.error

    pipeline, _ac, _volume, _speaker, _clock = make_pipeline(config=config)
    rendered = repr(pipeline.context)

    assert "/2" in rendered
    assert "max_age_seconds=30" in rendered
    assert "max_render_chars=40" in rendered


def test_a_malformed_context_window_block_falls_back_to_the_defaults(tmp_path) -> None:
    raw = json.loads((FIXTURES / "config.json").read_text(encoding="utf-8"))
    raw["context_window"] = {"max_items": 0, "max_age_seconds": "soon", "max_render_chars": -5}
    path = tmp_path / "config.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    config = load_config(path=path)

    pipeline, _ac, _volume, _speaker, _clock = make_pipeline(config=config)
    rendered = repr(pipeline.context)

    assert "/8" in rendered
    assert "max_age_seconds=900" in rendered
    assert "max_render_chars=800" in rendered


def test_a_ring_sizes_block_that_is_not_a_mapping_does_not_break_construction(tmp_path) -> None:
    raw = json.loads((FIXTURES / "config.json").read_text(encoding="utf-8"))
    raw["ring_sizes"] = "lots"
    path = tmp_path / "config.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    config = load_config(path=path)

    pipeline, _ac, _volume, _speaker, _clock = make_pipeline(config=config)
    feed(pipeline, HOT)

    assert pipeline._recent.capacity == 20
    assert len(pipeline.recent()) == 1


def test_a_new_session_after_a_connection_loss_keeps_its_own_audio_timeline() -> None:
    """Found in review: the old session's at_ms polled the new one past its deadline."""
    pipeline, ac, _volume, _speaker, _clock = make_pipeline(apply=True)
    feed(pipeline, CHITCHAT, start_ms=500_000)
    assert ac.power_calls == []

    pipeline.handle_event({"kind": "connection_lost", "type": ""})

    item_id = "after-loss"
    pipeline.handle_event(
        {"type": "input_audio_buffer.speech_started", "at_ms": 1000, "item_id": item_id}
    )
    pipeline.handle_event(
        {"type": "input_audio_buffer.speech_stopped", "at_ms": 1800, "item_id": item_id}
    )
    # The listener polls on its own cadence, before the transcript arrives.
    pipeline.poll()
    pipeline.handle_event(
        {
            "type": "conversation.item.input_audio_transcription.completed",
            "item_id": item_id,
            "text": HOT,
        }
    )
    pipeline.poll(2400)

    assert [entry.text for entry in pipeline.recent()][-1] == HOT
    assert ac.power_calls == [(POD, True, True)]


# --- strict-window-close-boundary, t2: mode resolved at the START instant ---


def test_a_command_begun_inside_the_window_is_refused_when_decided_after_it_closed() -> None:
    """t2, AC1+AC2: the bug this frame exists to fix. The utterance's speech
    STARTED inside a strict window; by the time the decision runs, the window
    has closed and 'now' resolves weekday. Resolving at decision time judges
    it weekday and ACTS -- which is a spoken command acting on a holy day.
    Resolving from the start instant, and taking the stricter of the two,
    refuses it."""
    start_wall = 1_000_000.0

    def mode_at(at: float):
        # Strict strictly before the boundary, weekday at or after it.
        inside = at < start_wall + 2.0
        return ResolvedMode(
            mode="strict" if inside else "weekday",
            kinds=("yom_kippur",) if inside else (),
            clock_trusted=True,
            overridden=False,
        )

    pipeline, ac, _volume, _speaker, _clock = make_pipeline(
        apply=True,
        # decision time is AFTER the window closed
        mode_provider=lambda: mode_at(start_wall + 5.0),
        mode_at=mode_at,
    )

    pipeline._handle_utterance(TURN_AC_ON, SpeechStart(monotonic_ms=0.0, wall_time=start_wall))

    assert ac.power_calls == []
    assert verdicts(pipeline) == [("gate_refused", "none")]


def test_a_command_begun_after_the_window_closed_still_acts() -> None:
    """The other half of the pair. Without this, the test above is satisfiable
    by refusing everything -- which would silently break weekday mode."""
    start_wall = 1_000_000.0

    def mode_at(at: float):
        inside = at < start_wall - 10.0
        return ResolvedMode(
            mode="strict" if inside else "weekday",
            kinds=("yom_kippur",) if inside else (),
            clock_trusted=True,
            overridden=False,
        )

    pipeline, ac, _volume, _speaker, _clock = make_pipeline(
        apply=True,
        mode_provider=lambda: mode_at(start_wall + 5.0),
        mode_at=mode_at,
    )

    pipeline._handle_utterance(TURN_AC_ON, SpeechStart(monotonic_ms=0.0, wall_time=start_wall))

    assert ac.power_calls == [(POD, True, True)]
    assert verdicts(pipeline) == [("acted", "ac_power_on")]


# --- strict-window-close-boundary, t3: the boundary, on REAL zmanim ---------

#: The Shabbat this test pins itself to, and the published Hebcal times that
#: anchor its two edges. ``tests/fixtures/zmanim_sun_vectors.json`` is an
#: independent source (see its ``source`` field), so if the zmanim maths, the
#: fixture config's location or its tzeit definition ever moves, the anchor
#: assertions below fail instead of the boundary quietly shifting with the
#: code under test.
_T3_FRIDAY = "2026-10-23"
_T3_SATURDAY = "2026-10-24"
_T3_TOLERANCE = timedelta(minutes=2)

#: Comfortably past the fixture config's 15-second strict-mode delay.
_T3_DELAY_HEADROOM = 60.0


def _t3_published(day: str, key: str) -> datetime:
    """A published Hebcal instant for Jerusalem, from the zmanim vectors."""
    vectors = json.loads(
        (Path(__file__).parent / "fixtures" / "zmanim_sun_vectors.json").read_text(encoding="utf-8")
    )["vectors"]
    for vector in vectors:
        if vector["location"] == "jerusalem" and vector["date"] == day:
            return datetime.fromisoformat(vector["times"][key]).astimezone(timezone.utc)
    raise AssertionError(f"no jerusalem vector for {day} {key}")


def _t3_real_window(config):
    """The real strict window around ``_T3_FRIDAY``, computed from the same
    config the pipeline under test is built from -- location, candle-lighting
    offset, tzeit definition and region all come from
    ``tests/fixtures/pipeline/config.json``, not from hand-picked offsets."""
    location = Location(config.location["lat"], config.location["lon"], config.location["timezone"])
    rules = ZmanimRules(
        candle_lighting_offset_minutes=config.candle_lighting_offset_minutes,
        tzeit=parse_tzeit_definition(config.tzeit_definition),
        israel=config.region == "israel",
    ).validate()
    noon = datetime.fromisoformat(f"{_T3_FRIDAY}T12:00:00").replace(
        tzinfo=ZoneInfo(config.location["timezone"])
    )
    window = next_window(noon, location, rules)
    assert window is not None, "no strict window computed for the pinned Shabbat"
    return window


def test_the_window_boundary_is_judged_from_the_speech_start_instant_on_real_zmanim() -> None:
    """strict-window-close-boundary, t3.

    The four boundary cases, in one test function, on a clock pinned either
    side of the REAL computed candle-lighting and tzeit instants for the
    configured location (Jerusalem, 18-minute offset, 3 medium stars) rather
    than synthetic offsets:

    1. a command begun INSIDE the window and decided after it closed -> refused;
    2. a command begun and decided OUTSIDE it -> acts (the other half of the
       pair, so case 1 cannot be satisfied by refusing everything);
    3. a command begun outside and decided INSIDE it (the OPEN boundary,
       candle lighting) -> refused;
    4. a hint in all four positions -> behaviour unchanged: never refused, and
       the AC is switched in every one of them (immediately on a weekday,
       after the strict-mode delay inside the window).

    The whole real path runs: fixture realtime events -> the joiner (whose
    wall clock is the only thing pinned) -> ``_on_utterance`` ->
    ``_handle_utterance``'s two mode readings and ``stricter_mode``.
    """
    set_override(None)  # module-level state; never rely on another test's
    config = load_config(path=FIXTURES / "config.json")
    assert config.ok, config.error
    window = _t3_real_window(config)

    # The anchors: both edges match the published Hebcal times for this
    # Shabbat, so a zmanim change surfaces here as a failure.
    assert window.kinds == ("shabbat",)
    expected_open = _t3_published(_T3_FRIDAY, "sunset") - timedelta(
        minutes=config.candle_lighting_offset_minutes
    )
    expected_close = _t3_published(_T3_SATURDAY, "tzeit85deg")
    assert abs(window.start - expected_open) <= _T3_TOLERANCE
    assert abs(window.end - expected_close) <= _T3_TOLERANCE

    opens = window.start.timestamp()
    closes = window.end.timestamp()

    def mode_at(at: float):
        """The real resolver, at a real instant. Only ``timedatectl`` is faked."""
        return resolve_mode(
            datetime.fromtimestamp(at, tz=timezone.utc),
            config,
            runner=lambda argv, **kwargs: type(
                "R", (), {"returncode": 0, "stdout": "yes\n", "stderr": ""}
            )(),
        )

    # Sanity: the instants really do straddle the two edges, one second out.
    assert mode_at(closes - 1.0).mode == "strict"
    assert mode_at(closes + 1.0).mode == "weekday"
    assert mode_at(opens - 1.0).mode == "weekday"
    assert mode_at(opens + 1.0).mode == "strict"

    # (name, text, speech-start instant, decision instant, expected verdicts,
    #  whether the AC must end up switched)
    cases = [
        (
            "command begun inside the window, decided after tzeit",
            TURN_AC_ON,
            closes - 1.0,
            closes + 1.0,
            [("gate_refused", "none")],
            False,
        ),
        (
            "command begun and decided after tzeit",
            TURN_AC_ON,
            closes + 1.0,
            closes + 5.0,
            [("acted", "ac_power_on")],
            True,
        ),
        (
            "command begun before candle lighting, decided after it",
            TURN_AC_ON,
            opens - 1.0,
            opens + 1.0,
            [("gate_refused", "none")],
            False,
        ),
        ("hint, both inside", HOT, opens + 1.0, closes - 1.0, [("delayed", "ac_power_on")], True),
        ("hint, begun inside", HOT, closes - 1.0, closes + 1.0, [("delayed", "ac_power_on")], True),
        ("hint, begun outside", HOT, opens - 1.0, opens + 1.0, [("delayed", "ac_power_on")], True),
        ("hint, both outside", HOT, closes + 1.0, closes + 5.0, [("acted", "ac_power_on")], True),
    ]

    for name, text, start_wall, decide_wall, expected, switched in cases:
        clock = FakeClock()
        pipeline, ac, _volume, _speaker, _clock = make_pipeline(
            apply=True,
            clock=clock,
            mode_provider=lambda decide_wall=decide_wall: mode_at(decide_wall),
            mode_at=mode_at,
        )
        # The one injection: the joiner samples wall-clock time at
        # ``speech_started``, and this test needs that instant to be the
        # pinned one. Everything downstream is the real path.
        pipeline.joiner._wall_clock = lambda start_wall=start_wall: start_wall

        feed(pipeline, text)

        assert verdicts(pipeline) == expected, name
        assert pipeline.joiner.last_utterance_start.wall_time == start_wall, name

        # Let any strict-mode delay elapse, then check what actually happened.
        clock.advance(_T3_DELAY_HEADROOM)
        pipeline.poll()
        if switched:
            assert ac.power_calls == [(POD, True, True)], name
            assert verdicts(pipeline)[-1] == ("acted", "ac_power_on"), name
        else:
            assert ac.power_calls == [], name
            assert all(verdict == "gate_refused" for verdict, _ in verdicts(pipeline)), name

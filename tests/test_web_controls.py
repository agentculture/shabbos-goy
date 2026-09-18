"""Criterion 1, second half: the controls go through the same gates as voice.

AC power, volume, mode and preflight are POST endpoints. Each one resolves a
tool call and then hands it to exactly the machinery the ambient listener
uses: ``Config.is_whitelisted``, ``config.validate_ac_argument``, the
pipeline's own ``RateLimiter``, and the pipeline's own adapters -- dry-run
unless the pipeline was built with ``apply=True``.

Per the approved deviation, controls work in EVERY mode, strict included:
the dashboard is an operator UI, like the CLI, and the operator has not
spoken a command to a listening box.
"""

from __future__ import annotations

import threading

import pytest

from shabbos_goy import mode as mode_module

from .test_web_support import (
    HOT,
    NOW_STRICT,
    POD,
    FakeAC,
    dashboard,
    feed,
    reset_override,
    timedatectl_says_unsynced,
)


@pytest.fixture(autouse=True)
def _no_override():
    reset_override()
    yield
    reset_override()


# ---------------------------------------------------------------------------
# AC power
# ---------------------------------------------------------------------------


def test_ac_control_is_dry_run_by_default(tmp_path) -> None:
    with dashboard(tmp_path) as ui:
        response = ui.post("/api/control/ac", {"power": "on"})

    assert response.status == 200
    body = response.json()
    assert body["verdict"] == "dry_run"
    assert body["action"] == "ac_power_on"
    assert body["target"] == "ac"
    assert ui.stack.ac.power_calls == [(POD, True, False)]


def test_ac_control_actuates_when_the_pipeline_was_built_with_apply(tmp_path) -> None:
    with dashboard(tmp_path, apply=True) as ui:
        body = ui.post("/api/control/ac", {"power": "on"}).json()

    assert body["verdict"] == "acted"
    assert ui.stack.ac.power_calls == [(POD, True, True)]


def test_ac_control_refuses_a_pod_that_is_not_whitelisted(tmp_path) -> None:
    overrides = {"whitelist": {"volume": {"pods": ["self"]}}}
    with dashboard(tmp_path, config_overrides=overrides) as ui:
        body = ui.post("/api/control/ac", {"power": "on"}).json()

    assert body["verdict"] == "not_whitelisted"
    assert ui.stack.ac.power_calls == []


def test_ac_control_refuses_an_argument_the_validator_rejects(tmp_path) -> None:
    with dashboard(tmp_path) as ui:
        response = ui.post("/api/control/ac", {"power": "ON"})
        assert response.status == 400
        assert response.json()["error"] == "invalid_arguments"
        assert ui.stack.ac.power_calls == []


def test_ac_control_shares_the_rate_limiter_with_voice(tmp_path) -> None:
    """Operator presses bypass the intervals, but they still COUNT for the voice path."""
    with dashboard(tmp_path) as ui:
        first = ui.post("/api/control/ac", {"power": "on"}).json()
        second = ui.post("/api/control/ac", {"power": "off"}).json()

        # An operator who cannot switch the AC off for ten minutes is no operator.
        assert first["verdict"] == "dry_run"
        assert second["verdict"] == "dry_run"
        assert len(ui.stack.ac.power_calls) == 2

        # ... and the voice path sees the same limiter: ON straight after the
        # operator's OFF is inside the compressor interval.
        feed(ui.stack.pipeline, HOT)
        assert ui.stack.pipeline.log_records[-1].verdict == "rate_limited"


def test_ac_control_works_in_strict_mode(tmp_path) -> None:
    with dashboard(tmp_path, now=NOW_STRICT) as ui:
        assert ui.get("/api/state").json()["mode"]["mode"] == "strict"
        body = ui.post("/api/control/ac", {"power": "on"}).json()

    assert body["verdict"] == "dry_run"
    assert body["mode"]["mode"] == "strict"


def test_ac_control_works_when_the_clock_is_untrusted(tmp_path) -> None:
    with dashboard(tmp_path, runner=timedatectl_says_unsynced) as ui:
        body = ui.post("/api/control/ac", {"power": "on"}).json()
    assert body["verdict"] == "dry_run"


def test_ac_control_acts_even_when_the_current_state_is_unknown(tmp_path) -> None:
    """An operator pressing a button is explicit; the voice path's
    'already in state' / 'state unknown' checks are for inferred intent."""
    with dashboard(tmp_path) as ui:
        ui.stack.ac.status_raises = RuntimeError("cloud down")
        body = ui.post("/api/control/ac", {"power": "on"}).json()

    assert body["verdict"] == "dry_run"


def test_an_adapter_that_raises_becomes_a_type_name_not_a_traceback(tmp_path) -> None:
    with dashboard(tmp_path) as ui:
        ui.stack.ac.raises = ValueError(f"pod {POD} rejected the write")
        response = ui.post("/api/control/ac", {"power": "on"})

    assert response.status == 200
    body = response.json()
    assert body["verdict"] == "error"
    assert body["reason"] == "ValueError"
    assert POD not in response.body


def test_a_missing_ac_adapter_is_refused_not_crashed(tmp_path) -> None:
    with dashboard(tmp_path, ac_power=None) as ui:
        body = ui.post("/api/control/ac", {"power": "on"}).json()
    assert body["verdict"] == "no_adapter"


@pytest.mark.parametrize("body", [{}, {"power": 1}, {"power": None}, {"power": "warm"}])
def test_ac_control_refuses_a_malformed_body(tmp_path, body) -> None:
    with dashboard(tmp_path) as ui:
        response = ui.post("/api/control/ac", body)
        assert response.status == 400
        assert ui.stack.ac.power_calls == []


def test_ac_control_refuses_a_body_that_is_not_json(tmp_path) -> None:
    with dashboard(tmp_path) as ui:
        response = ui.get(
            "/api/control/ac",
            method="POST",
            headers={"Content-Type": "application/json"},
        )
    assert response.status == 400


# ---------------------------------------------------------------------------
# volume
# ---------------------------------------------------------------------------


def test_volume_control_is_dry_run_by_default(tmp_path) -> None:
    with dashboard(tmp_path) as ui:
        body = ui.post("/api/control/volume", {"direction": "up"}).json()

    assert body["verdict"] == "dry_run"
    assert body["action"] == "volume_up"
    assert ui.stack.volume.steps == []


def test_volume_control_steps_the_adapter_when_applying(tmp_path) -> None:
    with dashboard(tmp_path, apply=True) as ui:
        up = ui.post("/api/control/volume", {"direction": "up"}).json()
        # An operator bypasses the interval: a second press straight away works.
        down = ui.post("/api/control/volume", {"direction": "down"}).json()

    assert (up["verdict"], down["verdict"]) == ("acted", "acted")
    assert ui.stack.volume.steps == [1, -1]


def test_operator_volume_presses_still_count_toward_the_daily_cap(tmp_path) -> None:
    with dashboard(tmp_path, apply=True) as ui:
        verdicts = [
            ui.post("/api/control/volume", {"direction": "up"}).json()["verdict"] for _ in range(13)
        ]
    assert verdicts[:12] == ["acted"] * 12
    assert verdicts[12] == "rate_limited"


def test_volume_control_refuses_a_key_that_is_not_whitelisted(tmp_path) -> None:
    overrides = {"whitelist": {"sensibo": {"pods": [POD]}}}
    with dashboard(tmp_path, config_overrides=overrides) as ui:
        body = ui.post("/api/control/volume", {"direction": "up"}).json()
    assert body["verdict"] == "not_whitelisted"


@pytest.mark.parametrize("body", [{}, {"direction": "sideways"}, {"direction": 3}])
def test_volume_control_refuses_a_malformed_body(tmp_path, body) -> None:
    with dashboard(tmp_path) as ui:
        assert ui.post("/api/control/volume", body).status == 400


# ---------------------------------------------------------------------------
# mode
# ---------------------------------------------------------------------------


def test_mode_control_forces_strict_inside_a_weekday(tmp_path) -> None:
    with dashboard(tmp_path) as ui:
        body = ui.post("/api/control/mode", {"mode": "strict"}).json()
        assert body["mode"]["mode"] == "strict"
        assert body["mode"]["overridden"] is True
        assert mode_module.get_override() == "strict"
        assert ui.get("/api/state").json()["mode"]["override"] == "strict"


def test_mode_control_switches_strict_off_inside_a_live_window(tmp_path) -> None:
    with dashboard(tmp_path, now=NOW_STRICT) as ui:
        body = ui.post("/api/control/mode", {"mode": "weekday"}).json()

    assert body["mode"]["mode"] == "weekday"
    assert body["mode"]["overridden"] is True
    # The window itself is still reported honestly.
    assert "shabbat" in body["mode"]["kinds"]


def test_mode_control_clears_the_override(tmp_path) -> None:
    with dashboard(tmp_path) as ui:
        ui.post("/api/control/mode", {"mode": "strict"})
        body = ui.post("/api/control/mode", {"mode": None}).json()

    assert body["mode"]["overridden"] is False
    assert mode_module.get_override() is None


def test_mode_control_uses_the_one_shared_override_not_a_dashboard_copy(tmp_path) -> None:
    with dashboard(tmp_path) as ui:
        ui.post("/api/control/mode", {"mode": "strict"})
        # A CLI in the same process sees it, and clearing it there is seen here.
        assert mode_module.get_override() == "strict"
        mode_module.clear_override()
        assert ui.get("/api/state").json()["mode"]["override"] is None


def test_mode_control_refuses_an_unknown_mode(tmp_path) -> None:
    with dashboard(tmp_path) as ui:
        response = ui.post("/api/control/mode", {"mode": "shabbos"})
        assert response.status == 400
        assert mode_module.get_override() is None


def test_an_override_never_beats_an_untrusted_clock(tmp_path) -> None:
    with dashboard(tmp_path, runner=timedatectl_says_unsynced) as ui:
        body = ui.post("/api/control/mode", {"mode": "weekday"}).json()

    assert body["mode"]["mode"] == "strict"
    assert body["mode"]["clock_trusted"] is False


# ---------------------------------------------------------------------------
# preflight
# ---------------------------------------------------------------------------


def test_preflight_reports_every_check_and_actuates_nothing(tmp_path) -> None:
    connected = {"state": "connected"}
    with dashboard(tmp_path, server_kwargs={"connection_provider": lambda: connected}) as ui:
        body = ui.post("/api/control/preflight").json()

    names = {check["name"] for check in body["checks"]}
    assert names >= {
        "config",
        "clock",
        "whitelist",
        "ac_adapter",
        "ac_status",
        "volume_adapter",
        "decider",
        "connection",
        "apply",
    }
    assert body["ok"] is True
    assert ui.stack.ac.power_calls == []
    assert ui.stack.volume.steps == []


def test_preflight_fails_when_the_lobes_connection_is_not_up(tmp_path) -> None:
    with dashboard(tmp_path) as ui:
        body = ui.post("/api/control/preflight").json()

    assert body["ok"] is False
    assert any(c["name"] == "connection" and not c["ok"] for c in body["checks"])


def test_preflight_fails_when_the_config_did_not_load(tmp_path) -> None:
    from shabbos_goy.config import load_config

    config = load_config(path=tmp_path / "missing.json")
    server_kwargs = {"bind_address": "127.0.0.1:0"}
    with dashboard(tmp_path, config=config, server_kwargs=server_kwargs) as ui:
        body = ui.post("/api/control/preflight").json()

    assert body["ok"] is False
    failed = {check["name"] for check in body["checks"] if not check["ok"]}
    assert {"config", "whitelist"} <= failed


def test_preflight_fails_when_the_ac_status_read_is_unavailable(tmp_path) -> None:
    with dashboard(tmp_path) as ui:
        ui.stack.ac.status_raises = RuntimeError("cloud down")
        ui.stack.clock.advance(3600)
        body = ui.post("/api/control/preflight").json()

    assert body["ok"] is False
    assert any(c["name"] == "ac_status" and not c["ok"] for c in body["checks"])
    assert "cloud down" not in str(body)


# ---------------------------------------------------------------------------
# the control log
# ---------------------------------------------------------------------------


def test_control_actions_are_logged_without_transcript_text_or_pod_ids(tmp_path) -> None:
    with dashboard(tmp_path) as ui:
        ui.post("/api/control/ac", {"power": "on"})
        ui.post("/api/control/mode", {"mode": "strict"})
        state = ui.get("/api/state").json()

    controls = state["controls"]
    assert controls[0]["action"] == "ac_power_on"
    assert controls[0]["target"] == "ac"
    assert POD not in str(controls)
    assert all(
        set(entry) <= {"klass", "intent", "verdict", "action", "reason", "target"}
        for entry in controls
    )


# ---------------------------------------------------------------------------
# review round: the control path races the voice path
# ---------------------------------------------------------------------------


class RendezvousAC(FakeAC):
    """A fake AC whose first call waits inside the adapter for a second one.

    That is the window the race lives in: check -> (adapter) -> record. If the
    two paths are not serialised, the control request reaches the adapter while
    the voice action is still inside it, and both spend the same last slot.
    """

    def __init__(self) -> None:
        super().__init__()
        self._cond = threading.Condition()
        self.entered = threading.Event()

    def power(self, pod_id: str, on: bool, *, apply: bool = False) -> dict:
        with self._cond:
            self.power_calls.append((pod_id, on, apply))
            first = len(self.power_calls) == 1
            self.entered.set()
            self._cond.notify_all()
            if first:
                self._cond.wait_for(lambda: len(self.power_calls) >= 2, timeout=1.0)
        if apply:
            self.state = "on" if on else "off"
        return {"acted": bool(apply), "requested_apply": apply, "changes": {}}


def test_a_control_press_racing_a_voice_action_cannot_double_spend_the_daily_cap(
    tmp_path,
) -> None:
    """Found in review: check and record were not atomic across threads."""
    ac = RendezvousAC()
    overrides = {
        "rate_limits": {
            "min_interval_seconds": 600,
            "daily_cap": 1,
            "off_min_interval_seconds": 0,
            "on_after_off_min_interval_seconds": 0,
        }
    }
    body: dict = {}
    with dashboard(tmp_path, apply=True, ac=ac, config_overrides=overrides) as ui:
        voice = threading.Thread(target=feed, args=(ui.stack.pipeline, HOT), daemon=True)
        voice.start()
        assert ac.entered.wait(timeout=5), "the voice action never reached the adapter"

        def press() -> None:
            body.update(ui.post("/api/control/ac", {"power": "on"}).json())

        control = threading.Thread(target=press, daemon=True)
        control.start()
        control.join(timeout=10)
        voice.join(timeout=10)
        assert not control.is_alive() and not voice.is_alive()

    assert len(ac.power_calls) == 1
    assert ui.stack.pipeline.log_records[-1].verdict == "acted"
    assert body["verdict"] == "rate_limited"


def test_state_can_be_polled_while_controls_are_being_pressed(tmp_path) -> None:
    """Found in review: state() iterated the live control log a POST was appending to."""
    failures: list[Exception] = []
    stop = threading.Event()

    server_kwargs = {"control_log_capacity": 4000}
    with dashboard(tmp_path, server_kwargs=server_kwargs) as ui:
        for _ in range(4000):
            ui.server.control_volume({"direction": "up"})

        def hammer() -> None:
            try:
                while not stop.is_set():
                    ui.server.control_volume({"direction": "up"})
            except Exception as exc:  # noqa: BLE001 - reported, not swallowed
                failures.append(exc)

        presser = threading.Thread(target=hammer, daemon=True)
        presser.start()
        try:
            for _ in range(200):
                assert isinstance(ui.server.state()["controls"], list)
        finally:
            stop.set()
            presser.join(timeout=5)

    assert failures == []


def test_an_applying_control_that_did_not_act_is_an_error_not_a_dry_run(tmp_path) -> None:
    """A failed `sensibo --apply` (timeout, non-zero exit) must not read as a dry run."""

    class FailingAC(FakeAC):
        def power(self, pod_id: str, on: bool, *, apply: bool = False) -> dict:
            self.power_calls.append((pod_id, on, apply))
            return {"acted": False, "requested_apply": apply, "changes": {}}

    with dashboard(tmp_path, apply=True, ac=FailingAC()) as ui:
        pressed = ui.post("/api/control/ac", {"power": "on"}).json()
    assert pressed["verdict"] == "error"

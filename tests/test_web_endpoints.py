"""Criterion 1 (the read surface) and criterion 3 (the transcript ring).

Everything the page shows comes from one of two GET endpoints, and both are
served by ``http.server`` alone: no build step, no external asset, no CDN.
"""

from __future__ import annotations

import pytest

from shabbos_goy.decider import Decision
from shabbos_goy.web import DashboardServer

from .test_web_support import (
    HOT,
    MARKER_TEXT,
    NOISY,
    NOW_STRICT,
    StubDecider,
    dashboard,
    feed,
    make_stack,
    remark,
    request,
    reset_override,
    serving,
    timedatectl_says_unsynced,
)


@pytest.fixture(autouse=True)
def _no_override():
    reset_override()
    yield
    reset_override()


# ---------------------------------------------------------------------------
# the page itself
# ---------------------------------------------------------------------------


def test_the_page_is_one_self_contained_html_document(tmp_path) -> None:
    with dashboard(tmp_path) as ui:
        response = ui.get("/")

    assert response.status == 200
    assert response.headers["Content-Type"].startswith("text/html")
    body = response.body
    assert body.lstrip().startswith("<!doctype html>")
    # No build step and no external asset: nothing is fetched from anywhere
    # but this server, so the page works offline.
    for marker in ("http://", "https://", "//cdn", "<script src=", '<link rel="stylesheet"'):
        assert marker not in body


def test_the_page_names_the_endpoints_it_polls(tmp_path) -> None:
    with dashboard(tmp_path) as ui:
        body = ui.get("/").body
    assert "/api/state" in body
    assert "/api/utterances" in body


def test_an_unknown_path_is_a_json_404(tmp_path) -> None:
    with dashboard(tmp_path) as ui:
        response = ui.get("/api/nope")
    assert response.status == 404
    assert response.json()["error"] == "not_found"


# ---------------------------------------------------------------------------
# criterion 1: mode, window, connection, AC, decider, errors
# ---------------------------------------------------------------------------


def test_state_reports_mode_window_and_next_window(tmp_path) -> None:
    with dashboard(tmp_path) as ui:
        state = ui.get("/api/state").json()

    assert state["mode"]["mode"] == "weekday"
    assert state["mode"]["clock_trusted"] is True
    assert state["mode"]["overridden"] is False
    assert state["mode"]["override"] is None
    window = state["window"]
    assert window["available"] is True
    assert window["current"] is None
    assert window["next"] is not None
    assert set(window["next"]) >= {"start", "end", "kinds"}
    assert "shabbat" in window["next"]["kinds"]


def test_state_reports_a_running_window_and_strict_mode(tmp_path) -> None:
    with dashboard(tmp_path, now=NOW_STRICT) as ui:
        state = ui.get("/api/state").json()

    assert state["mode"]["mode"] == "strict"
    assert "shabbat" in state["mode"]["kinds"]
    assert state["window"]["current"] is not None


def test_state_reports_an_untrusted_clock(tmp_path) -> None:
    with dashboard(tmp_path, runner=timedatectl_says_unsynced) as ui:
        state = ui.get("/api/state").json()

    assert state["mode"]["mode"] == "strict"
    assert state["mode"]["clock_trusted"] is False


def test_state_reports_ac_status_from_the_same_adapter_as_voice(tmp_path) -> None:
    with dashboard(tmp_path) as ui:
        state = ui.get("/api/state").json()
        assert state["ac"]["available"] is True
        assert state["ac"]["power"] == "off"
        assert state["ac"]["temperature"] == 28.0
        assert state["ac"]["humidity"] == 41.0
        assert ui.stack.ac.status_calls >= 1


def test_an_ac_adapter_that_raises_degrades_to_unavailable(tmp_path) -> None:
    with dashboard(tmp_path) as ui:
        ui.stack.ac.status_raises = RuntimeError("sensibo says no")
        ui.stack.clock.advance(3600)
        state = ui.get("/api/state").json()

    assert state["ac"]["available"] is False
    assert state["ac"]["power"] == "unknown"
    # The exception message can quote a pod id or an API response: only the
    # type name is ever reported.
    assert state["ac"]["reason"] == "RuntimeError"
    assert "sensibo says no" not in str(state)


def test_ac_status_is_cached_so_polling_does_not_hammer_the_cloud(tmp_path) -> None:
    with dashboard(tmp_path) as ui:
        ui.get("/api/state")
        ui.get("/api/state")
        assert ui.stack.ac.status_calls == 1
        ui.stack.clock.advance(3600)
        ui.get("/api/state")
        assert ui.stack.ac.status_calls == 2


def test_state_reports_connection_state_from_the_injected_provider(tmp_path) -> None:
    provider = {"state": "connected", "since": 12.0, "transcripts": 7}
    with dashboard(tmp_path, server_kwargs={"connection_provider": lambda: provider}) as ui:
        state = ui.get("/api/state").json()
    assert state["connection"]["state"] == "connected"
    assert state["connection"]["transcripts"] == 7


def test_connection_state_is_unknown_when_nothing_is_wired(tmp_path) -> None:
    with dashboard(tmp_path) as ui:
        state = ui.get("/api/state").json()
    assert state["connection"]["state"] == "unknown"


def test_a_connection_provider_that_raises_does_not_break_the_page(tmp_path) -> None:
    def boom():
        raise RuntimeError("no socket")

    with dashboard(tmp_path, server_kwargs={"connection_provider": boom}) as ui:
        state = ui.get("/api/state").json()
    assert state["connection"]["state"] == "unknown"
    assert state["connection"]["reason"] == "RuntimeError"


# ---------------------------------------------------------------------------
# criterion 1 / deviation d1: the decider as bug context
# ---------------------------------------------------------------------------


def test_state_reports_the_decider_source_and_prompt_version(tmp_path) -> None:
    with dashboard(tmp_path) as ui:
        state = ui.get("/api/state").json()
    assert state["decider"]["source"] == "stub:p1"
    assert state["decider"]["prompt_version"] == "p1"


def test_state_reports_the_last_decision(tmp_path) -> None:
    with dashboard(tmp_path) as ui:
        feed(ui.stack.pipeline, HOT)
        state = ui.get("/api/state").json()

    last = state["decider"]["last"]
    assert last["klass"] == "remark"
    assert last["intent"] == "cool"
    assert last["verdict"] == "dry_run"
    assert last["action"] == "ac_power_on"


def test_state_counts_no_decision_reasons(tmp_path) -> None:
    timeout = Decision(
        klass="unrelated", intent="none", confidence=0.0, source="stub:p1", reason="timeout"
    )
    unauthorised = Decision(
        klass="unrelated", intent="none", confidence=0.0, source="stub:p1", reason="http_401"
    )
    decider = StubDecider(timeout)
    with dashboard(tmp_path, decider=decider) as ui:
        feed(ui.stack.pipeline, HOT)
        feed(ui.stack.pipeline, NOISY)
        decider.answer = unauthorised
        feed(ui.stack.pipeline, HOT)
        state = ui.get("/api/state").json()

    assert state["decider"]["no_decision_reasons"] == {"timeout": 2, "http_401": 1}


def test_decide_latency_is_reported_when_a_provider_supplies_it(tmp_path) -> None:
    samples = [10.0, 20.0, 30.0, 40.0, 1000.0]
    with dashboard(tmp_path, server_kwargs={"latency_provider": lambda: samples}) as ui:
        state = ui.get("/api/state").json()

    latency = state["decider"]["decide_latency_ms"]
    assert latency["available"] is True
    assert latency["count"] == 5
    assert latency["p50"] == 30.0
    assert latency["p95"] == 1000.0


def test_decide_latency_is_honestly_absent_when_nothing_measures_it(tmp_path) -> None:
    """With no ``latency_provider`` injected the dashboard says so rather than
    inventing a number. The listener injects the pipeline's own
    ``decide_latencies`` (t14); a bare DashboardServer is given nothing."""
    with dashboard(tmp_path) as ui:
        state = ui.get("/api/state").json()

    latency = state["decider"]["decide_latency_ms"]
    assert latency["available"] is False
    assert latency["p50"] is None
    assert latency["p95"] is None


def test_state_reports_pipeline_errors_by_type_name_only(tmp_path) -> None:
    with dashboard(tmp_path) as ui:
        ui.stack.ac.raises = ValueError("pod PODFAKE1 rejected the write")
        feed(ui.stack.pipeline, HOT)
        state = ui.get("/api/state").json()

    assert state["errors"] == [{"reason": "ValueError"}]
    assert "PODFAKE1" not in str(state)


# ---------------------------------------------------------------------------
# criterion 3: the recent-utterance ring
# ---------------------------------------------------------------------------


def test_utterances_carry_text_class_intent_verdict_action_and_timings(tmp_path) -> None:
    with dashboard(tmp_path) as ui:
        feed(ui.stack.pipeline, f"{HOT} {MARKER_TEXT}")
        ui.stack.clock.advance(5.0)
        payload = ui.get("/api/utterances").json()

    assert payload["count"] == 1
    entry = payload["utterances"][0]
    assert entry["text"] == f"{HOT} {MARKER_TEXT}"
    assert entry["klass"] == "remark"
    assert entry["intent"] == "cool"
    assert entry["verdict"] == "dry_run"
    assert entry["action"] == "ac_power_on"
    assert entry["target"] == "ac"
    assert entry["age_seconds"] == pytest.approx(5.0)
    # t14 made the per-decision timing real: the stub decider costs nothing
    # on the hand-advanced clock, so this is 0.0 -- a measurement, not a guess.
    assert entry["decide_latency_ms"] == pytest.approx(0.0)


def test_an_utterance_with_no_matching_log_record_still_shows_its_text(tmp_path) -> None:
    """Pairing is best-effort; a missing verdict is reported as null, not guessed."""
    stack = make_stack(tmp_path, log_capacity=1)
    server = DashboardServer(stack.pipeline, stack.mode_provider, stack.config)
    with serving(server):
        feed(stack.pipeline, HOT)
        feed(stack.pipeline, NOISY)
        payload = request(f"{server.url}/api/utterances").json()

    entries = payload["utterances"]
    assert [e["text"] for e in entries] == [HOT, NOISY]
    assert entries[-1]["verdict"] is not None
    assert entries[0]["verdict"] is None


def test_the_ring_never_exceeds_its_configured_size(tmp_path) -> None:
    with dashboard(tmp_path, recent_capacity=3) as ui:
        for index in range(7):
            feed(ui.stack.pipeline, f"{HOT} {index}")
        payload = ui.get("/api/utterances").json()

    assert payload["capacity"] == 3
    assert payload["count"] == 3
    assert [e["text"] for e in payload["utterances"]] == [f"{HOT} {n}" for n in (4, 5, 6)]


def test_the_ring_is_empty_after_a_restart(tmp_path) -> None:
    with dashboard(tmp_path) as ui:
        feed(ui.stack.pipeline, f"{HOT} {MARKER_TEXT}")
        assert ui.get("/api/utterances").json()["count"] == 1

    # A restart is a fresh Pipeline (nothing is persisted): the ring comes
    # back empty and the marker transcript is gone.
    with dashboard(tmp_path) as ui:
        payload = ui.get("/api/utterances").json()
        assert payload["count"] == 0
        assert MARKER_TEXT not in str(payload)


def test_a_decision_the_pipeline_refused_still_appears_with_its_verdict(tmp_path) -> None:
    low = remark("cool", confidence=0.1)
    with dashboard(tmp_path, decider=StubDecider(low)) as ui:
        feed(ui.stack.pipeline, HOT)
        entry = ui.get("/api/utterances").json()["utterances"][0]

    assert entry["verdict"] == "low_confidence"
    assert entry["action"] == "none"


def test_the_last_decision_carries_its_real_confidence(tmp_path) -> None:
    """t14 wired confidence through from the transcript ring."""
    with dashboard(tmp_path) as ui:
        feed(ui.stack.pipeline, HOT)
        state = ui.get("/api/state").json()

    last = state["decider"]["last"]
    assert last["klass"] == "remark"
    assert last["confidence"] == pytest.approx(0.9)


def test_the_pipelines_own_latencies_are_a_usable_provider(tmp_path) -> None:
    stack = make_stack(tmp_path)
    server = DashboardServer(
        stack.pipeline,
        stack.mode_provider,
        stack.config,
        latency_provider=stack.pipeline.decide_latencies,
        now_provider=lambda: stack.now,
    )
    with serving(server):
        feed(stack.pipeline, HOT)
        state = request(f"{server.url}/api/state").json()

    latency = state["decider"]["decide_latency_ms"]
    assert latency["available"] is True
    assert latency["count"] == 1

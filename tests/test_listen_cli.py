"""``shabbos-goy listen``: the fixtures-only end-to-end path and --healthcheck.

The end-to-end test runs the *real* loop -- the real pipeline, gates,
whitelist, rate limiter, control server and the real
``shabbos_goy.actuators.sensibo`` / ``shabbos_goy.audio.pipewire`` adapters --
with a JSONL events file instead of a microphone, a ReplayDecider instead of
a model, and fake ``sensibo``/``wpctl`` executables first on ``PATH``. No
microphone, no lobes server, no Sensibo account, no real volume change.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from shabbos_goy import mode as mode_module
from shabbos_goy.cli import main

from .test_listen_support import (
    HOT,
    HOT_MARKED,
    IMPERATIVE,
    MARKER_TEXT,
    POD,
    make_config,
    write_events_file,
)

FAKE_SENSIBO = Path(__file__).parent / "fixtures" / "fake_sensibo"
FAKE_PIPEWIRE = Path(__file__).parent / "fixtures" / "fake_pipewire"
REPLAY = Path(__file__).parent / "fixtures" / "pipeline" / "replay.json"


@pytest.fixture(autouse=True)
def _no_override():
    mode_module.clear_override()
    yield
    mode_module.clear_override()


@pytest.fixture
def weekday(monkeypatch):
    """Pin the mode without pinning the clock.

    These tests run at whatever moment CI happens to run them -- which may be
    inside a real Shabbat window, where the strict-mode delay would hold the
    action instead of running it. So they use the production override
    (``shabbos_goy.mode.set_override``, the same one ``mode set`` drives) and
    assert clock trust, rather than asserting a verdict that depends on the
    calendar. The strict path has its own tests in ``test_listen_runtime.py``.
    """
    monkeypatch.setattr(mode_module, "clock_is_trusted", lambda *a, **k: True)
    mode_module.set_override("weekday")
    yield
    mode_module.clear_override()


@pytest.fixture
def strict(monkeypatch):
    """The same seam as ``weekday``, pinned to Shabbat behaviour instead."""
    monkeypatch.setattr(mode_module, "clock_is_trusted", lambda *a, **k: True)
    mode_module.set_override("strict")
    yield
    mode_module.clear_override()


@pytest.fixture
def fake_path(monkeypatch, tmp_path):
    """Fake ``sensibo``/``wpctl`` first on PATH, with their state in tmp_path."""
    for directory in (FAKE_SENSIBO, FAKE_PIPEWIRE):
        for entry in directory.iterdir():
            if entry.is_file():
                entry.chmod(entry.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv(
        "PATH", f"{FAKE_SENSIBO}{os.pathsep}{FAKE_PIPEWIRE}{os.pathsep}{os.environ['PATH']}"
    )
    monkeypatch.setenv("SENSIBO_FAKE_STATE", str(tmp_path / "sensibo-state.json"))
    monkeypatch.setenv("SENSIBO_FAKE_CALLS", str(tmp_path / "sensibo-calls.jsonl"))
    monkeypatch.setenv("WPCTL_FAKE_STATE", str(tmp_path / "wpctl-state.json"))
    return tmp_path


def _sensibo_calls(tmp_path: Path) -> list[list[str]]:
    path = tmp_path / "sensibo-calls.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _run_script(tmp_path, texts, *, extra: list[str] | None = None) -> int:
    config = make_config(tmp_path, dashboard_bind_address="127.0.0.1:0")
    events = write_events_file(tmp_path / "events.jsonl", texts)
    argv = [
        "listen",
        "--config",
        str(config.path),
        "--script",
        str(events),
        "--decider",
        "replay",
        "--replay-file",
        str(REPLAY),
        "--control-address",
        "127.0.0.1:0",
        "--no-dashboard",
        "--heartbeat",
        str(tmp_path / "heartbeat.json"),
        "--json",
    ]
    return main(argv + (extra or []))


def test_the_fixtures_only_end_to_end_run_classifies_and_stays_dry(
    weekday, fake_path, tmp_path, capsys
) -> None:
    rc = _run_script(tmp_path, [HOT])
    out = capsys.readouterr().out
    assert rc == 0

    payload = json.loads(out)
    assert payload["apply"] is False
    assert payload["utterances"] == 1
    assert payload["actions"] == [{"verdict": "dry_run", "action": "ac_power_on", "target": "ac"}]

    calls = _sensibo_calls(tmp_path)
    assert calls, "the real sensibo adapter was never reached"
    assert all("--apply" not in call for call in calls), calls


def test_an_imperative_reaches_no_actuator_at_all(strict, fake_path, tmp_path, capsys) -> None:
    """The core invariant, through the real CLI: in strict mode a command
    reaches no adapter at all."""
    rc = _run_script(tmp_path, [IMPERATIVE])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["actions"] == []
    # status() is a read, so a call may exist -- but nothing was ever written.
    assert all("--apply" not in call for call in _sensibo_calls(tmp_path))


def test_the_run_summary_never_prints_transcript_text(weekday, fake_path, tmp_path, capsys) -> None:
    rc = _run_script(tmp_path, [HOT_MARKED])
    captured = capsys.readouterr()
    assert rc == 0
    printed = captured.out + captured.err
    assert MARKER_TEXT not in printed
    assert HOT not in printed
    assert POD not in printed


def test_apply_is_refused_without_the_flag_and_honoured_with_it(
    weekday, fake_path, tmp_path, capsys
) -> None:
    rc = _run_script(tmp_path, [HOT], extra=["--apply"])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["apply"] is True
    assert payload["actions"] == [{"verdict": "acted", "action": "ac_power_on", "target": "ac"}]
    assert any("--apply" in call for call in _sensibo_calls(tmp_path))


def test_a_missing_script_file_is_a_user_error(fake_path, tmp_path, capsys) -> None:
    config = make_config(tmp_path)
    rc = main(
        [
            "listen",
            "--config",
            str(config.path),
            "--script",
            str(tmp_path / "nope.jsonl"),
            "--decider",
            "replay",
            "--replay-file",
            str(REPLAY),
            "--control-address",
            "127.0.0.1:0",
            "--no-dashboard",
        ]
    )
    assert rc == 1


def _entrance_keyed_replay(tmp_path: Path, *, entrances: list[str]) -> Path:
    flat = json.loads(REPLAY.read_text(encoding="utf-8"))
    path = tmp_path / "golden_replay.json"
    path.write_text(
        json.dumps({"format": "golden-replay/1", "entrances": {name: flat for name in entrances}}),
        encoding="utf-8",
    )
    return path


def test_listen_replays_a_recorded_entrance_keyed_file(
    weekday, fake_path, tmp_path, capsys
) -> None:
    """Finding 3: ``listen --decider replay`` must read what --record wrote."""
    rc = _run_script(
        tmp_path,
        [HOT],
        extra=["--replay-file", str(_entrance_keyed_replay(tmp_path, entrances=["text"]))],
    )
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["actions"] == [{"verdict": "dry_run", "action": "ac_power_on", "target": "ac"}]


def test_listen_names_the_entrances_instead_of_picking_one(fake_path, tmp_path, capsys) -> None:
    rc = _run_script(
        tmp_path,
        [HOT],
        extra=[
            "--replay-file",
            str(_entrance_keyed_replay(tmp_path, entrances=["text", "audio-batch"])),
        ],
    )
    err = capsys.readouterr().err
    assert rc == 1
    assert "--replay-entrance" in err


def test_listen_replay_entrance_selects_the_bucket(weekday, fake_path, tmp_path, capsys) -> None:
    rc = _run_script(
        tmp_path,
        [HOT],
        extra=[
            "--replay-file",
            str(_entrance_keyed_replay(tmp_path, entrances=["text", "audio-batch"])),
            "--replay-entrance",
            "audio-batch",
        ],
    )
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["utterances"] == 1


def test_replay_decider_requires_a_replay_file(fake_path, tmp_path) -> None:
    config = make_config(tmp_path)
    events = write_events_file(tmp_path / "events.jsonl", [HOT])
    rc = main(
        [
            "listen",
            "--config",
            str(config.path),
            "--script",
            str(events),
            "--decider",
            "replay",
            "--control-address",
            "127.0.0.1:0",
            "--no-dashboard",
        ]
    )
    assert rc == 1


# ---------------------------------------------------------------------------
# --healthcheck (the container healthcheck)
# ---------------------------------------------------------------------------


def test_healthcheck_exits_zero_on_a_fresh_heartbeat(tmp_path, capsys) -> None:
    from shabbos_goy.runtime import Heartbeat

    path = tmp_path / "hb.json"
    beat = Heartbeat(path, window_seconds=600.0)
    beat.mark("transcript")
    beat.write()

    rc = main(["listen", "--healthcheck", "--heartbeat", str(path), "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload == {"ok": True, "reason": "ok"}


def test_healthcheck_exits_one_when_no_heartbeat_exists(tmp_path, capsys) -> None:
    rc = main(["listen", "--healthcheck", "--heartbeat", str(tmp_path / "nope.json"), "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert payload == {"ok": False, "reason": "missing"}


def test_listen_is_documented_in_learn_and_the_explain_catalog() -> None:
    from shabbos_goy.cli._commands.learn import _TEXT
    from shabbos_goy.explain.catalog import ENTRIES

    assert ("listen",) in ENTRIES
    assert "--apply" in ENTRIES[("listen",)]
    assert "listen" in _TEXT
    assert "shabbos-goy listen" in ENTRIES[()]


# ---------------------------------------------------------------------------
# A broken config is visible, but never fatal (review thread #11, partial).
# ---------------------------------------------------------------------------


def _broken_config(tmp_path: Path) -> Path:
    path = tmp_path / "broken.json"
    path.write_text("{ this is not json", encoding="utf-8")
    return path


def _listen_with_broken_config(tmp_path: Path, extra: list[str] | None = None) -> int:
    events = write_events_file(tmp_path / "events.jsonl", [HOT])
    return main(
        [
            "listen",
            "--config",
            str(_broken_config(tmp_path)),
            "--script",
            str(events),
            "--decider",
            "replay",
            "--replay-file",
            str(REPLAY),
            "--control-address",
            "127.0.0.1:0",
            "--no-dashboard",
            "--heartbeat",
            str(tmp_path / "heartbeat.json"),
            "--json",
        ]
        + (extra or [])
    )


def test_a_broken_config_keeps_listening_and_says_so_exactly_once(
    weekday, fake_path, tmp_path, capsys
) -> None:
    """Deliberately NOT an exit: a container that quits on a bad config
    crash-loops, and Shabbat is when nobody can restart it. A broken config
    has already failed closed -- empty whitelist, strict mode -- so the
    listener that keeps running can act on nothing. It must be *visible*
    though: one named line, and the code in the summary."""
    rc = _listen_with_broken_config(tmp_path)
    captured = capsys.readouterr()

    assert rc == 0, "a broken config must not make the container exit"
    payload = json.loads(captured.out)
    assert payload["config_error"] == 2
    assert payload["actions"] == [], "a broken config whitelists nothing"

    lines = [line for line in captured.err.splitlines() if line.startswith("event=config_error")]
    assert lines == ["event=config_error detail=2"], captured.err
    # The code, never the file or its contents.
    assert "this is not json" not in captured.err
    assert str(tmp_path) not in " ".join(lines)


def test_a_broken_config_leaves_healthcheck_alone(tmp_path, capsys) -> None:
    rc = main(
        ["listen", "--healthcheck", "--heartbeat", str(tmp_path / "nope.json"), "--json"],
    )
    assert rc == 1
    assert json.loads(capsys.readouterr().out)["reason"] == "missing"

"""Tests for the Sensibo adapter (shabbos_goy/actuators/sensibo.py).

Every test drives a *fake* ``sensibo`` executable placed on ``PATH`` — never
the real one, never a network call, never an import of the ``sensibo``
package. That fake script logs the argv it was called with (one JSON line per
call, to a file whose path is passed in via an env var) so tests can assert
exactly what argv this adapter builds, in addition to asserting on the
adapter's return value.
"""

from __future__ import annotations

import json
import os
import stat
import textwrap
from pathlib import Path

import pytest

from shabbos_goy.actuators import sensibo as sensibo_module
from shabbos_goy.actuators.sensibo import _read_argv, _set_argv, power, status

POD_ID = "pod-fixture-1"


# --- argv builders: enumerate the closed set --------------------------------


def _all_flags(argv: list[str]) -> set[str]:
    return {tok for tok in argv if tok.startswith("--")}


# The only flags this adapter is ever allowed to build.
_ALLOWED_FLAGS = {"--power", "--apply", "--json"}


def test_set_argv_enumerated_only_uses_closed_flag_set() -> None:
    for power_on in (True, False):
        for apply in (True, False):
            argv = _set_argv(POD_ID, power_on=power_on, apply=apply)
            assert _all_flags(argv) <= _ALLOWED_FLAGS
            assert argv[0] == sensibo_module.SENSIBO_EXECUTABLE
            assert argv[1] == "set"
            assert argv[2] == POD_ID
            # --power always carries on|off, never any other value
            power_index = argv.index("--power")
            assert argv[power_index + 1] == ("on" if power_on else "off")
            assert ("--apply" in argv) is apply
            assert argv[-1] == "--json"
            # no other sensibo verb-specific flag ever appears
            for forbidden in ("--mode", "--target", "--fan", "--swing", "--all"):
                assert forbidden not in argv


def test_read_argv_enumerated_only_uses_closed_flag_set() -> None:
    argv = _read_argv(POD_ID)
    assert _all_flags(argv) <= _ALLOWED_FLAGS
    assert "--apply" not in argv
    assert argv == [sensibo_module.SENSIBO_EXECUTABLE, "read", POD_ID, "--json"]


def test_read_argv_never_contains_apply() -> None:
    assert "--apply" not in _read_argv(POD_ID)


# --- fake sensibo executable on PATH ----------------------------------------


_FAKE_SENSIBO_TEMPLATE = textwrap.dedent("""\
    #!/usr/bin/env python3
    import json
    import os
    import sys

    log_path = os.environ["FAKE_SENSIBO_LOG"]
    with open(log_path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(sys.argv[1:]) + "\\n")

    responses = json.loads(os.environ["FAKE_SENSIBO_RESPONSES"])
    exit_codes = json.loads(os.environ.get("FAKE_SENSIBO_EXIT_CODES", "[]"))
    call_index_path = os.environ["FAKE_SENSIBO_CALL_INDEX"]
    try:
        with open(call_index_path, "r", encoding="utf-8") as fh:
            call_index = int(fh.read() or "0")
    except FileNotFoundError:
        call_index = 0
    with open(call_index_path, "w", encoding="utf-8") as fh:
        fh.write(str(call_index + 1))

    exit_code = exit_codes[call_index] if call_index < len(exit_codes) else 0
    payload = responses[call_index] if call_index < len(responses) else {}
    print(json.dumps(payload))
    sys.exit(exit_code)
    """)


def _install_fake_sensibo(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    responses: list[dict],
    exit_codes: list[int] | None = None,
) -> Path:
    """Write a fake ``sensibo`` executable, put it first on PATH, wire its env."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    script_path = bin_dir / "sensibo"
    script_path.write_text(_FAKE_SENSIBO_TEMPLATE, encoding="utf-8")
    script_path.chmod(script_path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    log_path = tmp_path / "calls.jsonl"
    call_index_path = tmp_path / "call_index.txt"

    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setenv("FAKE_SENSIBO_LOG", str(log_path))
    monkeypatch.setenv("FAKE_SENSIBO_RESPONSES", json.dumps(responses))
    monkeypatch.setenv("FAKE_SENSIBO_EXIT_CODES", json.dumps(exit_codes or []))
    monkeypatch.setenv("FAKE_SENSIBO_CALL_INDEX", str(call_index_path))
    return log_path


def _read_calls(log_path: Path) -> list[list[str]]:
    if not log_path.exists():
        return []
    return [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines() if line]


# --- power(): dry-run by default --------------------------------------------


def test_power_dry_run_by_default_does_not_pass_apply(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log_path = _install_fake_sensibo(
        tmp_path,
        monkeypatch,
        responses=[
            {"applied": False, "pod_id": POD_ID, "changes": {"on": {"from": False, "to": True}}}
        ],
    )

    result = power(POD_ID, True)

    calls = _read_calls(log_path)
    assert len(calls) == 1
    assert "--apply" not in calls[0]
    assert result["acted"] is False  # apply defaults to False -> never "acted"
    assert result["requested_apply"] is False


def test_power_apply_true_passes_apply_and_reports_acted_on_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log_path = _install_fake_sensibo(
        tmp_path,
        monkeypatch,
        responses=[
            {
                "applied": True,
                "pod_id": POD_ID,
                "changes": {"on": {"from": False, "to": True}},
                "method": "patch",
            }
        ],
    )

    result = power(POD_ID, True, apply=True)

    calls = _read_calls(log_path)
    assert "--apply" in calls[0]
    assert result["acted"] is True


def test_power_non_zero_exit_is_treated_as_did_not_act(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_sensibo(
        tmp_path,
        monkeypatch,
        responses=[{}],
        exit_codes=[1],
    )

    result = power(POD_ID, False, apply=True)

    assert result["acted"] is False


def test_power_off_builds_power_off_flag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    log_path = _install_fake_sensibo(
        tmp_path,
        monkeypatch,
        responses=[{"applied": False, "pod_id": POD_ID, "changes": {}}],
    )

    power(POD_ID, False)

    calls = _read_calls(log_path)
    power_index = calls[0].index("--power")
    assert calls[0][power_index + 1] == "off"


def test_power_unparsable_output_is_treated_as_did_not_act(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    script_path = bin_dir / "sensibo"
    script_path.write_text(
        "#!/usr/bin/env python3\nprint('not json')\n",
        encoding="utf-8",
    )
    script_path.chmod(script_path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")

    result = power(POD_ID, True, apply=True)

    assert result["acted"] is False


# --- status(): zero-write -----------------------------------------------------


def test_status_never_passes_apply(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    log_path = _install_fake_sensibo(
        tmp_path,
        monkeypatch,
        responses=[
            {"applied": False, "pod_id": POD_ID, "changes": {}},
            {"asOf": "now", "id": POD_ID, "readings": {"temperature": 24.5, "humidity": 41}},
        ],
    )

    status(POD_ID)

    calls = _read_calls(log_path)
    for call in calls:
        assert "--apply" not in call


def test_status_derives_off_from_dry_run_diff_from_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_sensibo(
        tmp_path,
        monkeypatch,
        responses=[
            # dry-run of --power on shows a diff: current ("from") is off
            {"applied": False, "pod_id": POD_ID, "changes": {"on": {"from": False, "to": True}}},
            {"asOf": "now", "id": POD_ID, "readings": {"temperature": 23.0, "humidity": 50}},
        ],
    )

    result = status(POD_ID)

    assert result["power"] == "off"


def test_status_derives_on_when_dry_run_reports_no_diff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_sensibo(
        tmp_path,
        monkeypatch,
        responses=[
            # no diff => pod already matches the requested "on" => already on
            {"applied": False, "pod_id": POD_ID, "changes": {}},
            {"asOf": "now", "id": POD_ID, "readings": {"temperature": 22.0, "humidity": 45}},
        ],
    )

    result = status(POD_ID)

    assert result["power"] == "on"


def test_status_merges_temperature_and_humidity_from_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_sensibo(
        tmp_path,
        monkeypatch,
        responses=[
            {"applied": False, "pod_id": POD_ID, "changes": {}},
            {"asOf": "now", "id": POD_ID, "readings": {"temperature": 26.5, "humidity": 38}},
        ],
    )

    result = status(POD_ID)

    assert result["temperature"] == 26.5
    assert result["humidity"] == 38


def test_status_returns_unknown_power_on_non_zero_exit_from_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_sensibo(
        tmp_path,
        monkeypatch,
        responses=[
            {},
            {"asOf": "now", "id": POD_ID, "readings": {"temperature": 20.0, "humidity": 30}},
        ],
        exit_codes=[1, 0],
    )

    result = status(POD_ID)

    assert result["power"] == "unknown"
    # read still succeeded independently
    assert result["temperature"] == 20.0
    assert result["humidity"] == 30


def test_status_returns_none_readings_on_non_zero_exit_from_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_sensibo(
        tmp_path,
        monkeypatch,
        responses=[{"applied": False, "pod_id": POD_ID, "changes": {}}, {}],
        exit_codes=[0, 1],
    )

    result = status(POD_ID)

    assert result["power"] == "on"
    assert result["temperature"] is None
    assert result["humidity"] is None


def test_status_all_unknown_when_both_calls_fail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_sensibo(
        tmp_path,
        monkeypatch,
        responses=[{}, {}],
        exit_codes=[1, 1],
    )

    result = status(POD_ID)

    assert result["power"] == "unknown"
    assert result["temperature"] is None
    assert result["humidity"] is None


def test_status_calls_set_before_read_in_that_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log_path = _install_fake_sensibo(
        tmp_path,
        monkeypatch,
        responses=[
            {"applied": False, "pod_id": POD_ID, "changes": {}},
            {"asOf": "now", "id": POD_ID, "readings": {}},
        ],
    )

    status(POD_ID)

    calls = _read_calls(log_path)
    assert calls[0][0] == "set"
    assert calls[1][0] == "read"


# --- timeout is above sensibo-cli's own backoff ceiling ----------------------


def test_timeout_constant_exceeds_sensibos_single_retry_ceiling() -> None:
    # sensibo/api/client.py's `_MAX_RETRY_DELAY` ceiling on a single 429 sleep
    # is 120s. Our subprocess timeout must clear that so an in-progress retry
    # inside sensibo-cli is never mistaken for a hung process.
    assert sensibo_module._TIMEOUT_SECONDS > 120.0


def test_run_uses_subprocess_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}

    def fake_run(argv, **kwargs):
        captured.update(kwargs)
        raise sensibo_module.subprocess.SubprocessError("stop after capturing kwargs")

    monkeypatch.setattr(sensibo_module.subprocess, "run", fake_run)

    result = sensibo_module._run(["sensibo", "read", POD_ID, "--json"])

    # _run swallows the exception raised by our fake, so no crash propagates
    assert result is None
    assert captured["timeout"] == sensibo_module._TIMEOUT_SECONDS
    assert captured.get("shell", False) is False or "shell" not in captured

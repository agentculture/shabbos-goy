"""The Sensibo key is injected by the ``grant`` secrets manager, never held here.

``grant run --inject VAR=NAME -- cmd`` forks, sets ``VAR`` from the operator's
per-user store and ``execvp``s ``cmd``: the child's stdout and exit code are
``sensibo``'s own, and the key never enters this process, a config file or a
compose env file. The adapter only ever PREFIXES its locked argv with that
closed ``grant`` wrapper, and only when the operator configured a secret name.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from shabbos_goy.actuators import sensibo

from .test_actuators_sensibo import POD_ID, _install_fake_sensibo, _read_calls

_FAKE_GRANT = """#!/usr/bin/env python3
import json, os, sys
argv = sys.argv[1:]
with open(os.environ["FAKE_GRANT_LOG"], "a", encoding="utf-8") as handle:
    handle.write(json.dumps(argv) + "\\n")
assert argv[0] == "run", argv
env = dict(os.environ)
rest = argv[1:]
while rest and rest[0] == "--inject":
    var, name = rest[1].split("=", 1)
    env[var] = "value-of-" + name
    rest = rest[2:]
assert rest and rest[0] == "--", rest
os.execvpe(rest[1], rest[1:], env)
"""


def _install_fake_grant(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    script = bin_dir / "grant"
    script.write_text(_FAKE_GRANT, encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    log_path = tmp_path / "grant_calls.jsonl"
    monkeypatch.setenv("FAKE_GRANT_LOG", str(log_path))
    return log_path


def test_without_a_grant_name_the_argv_is_exactly_the_plain_sensibo_argv() -> None:
    plain = sensibo._set_argv(POD_ID, power_on=True, apply=False)
    assert sensibo._with_grant(plain, None, env={}) == plain


def test_the_grant_prefix_is_a_closed_shape_around_the_locked_argv() -> None:
    plain = sensibo._set_argv(POD_ID, power_on=True, apply=True)
    wrapped = sensibo._with_grant(plain, "SENSIBO_API_KEY", env={})
    assert wrapped == [
        "grant",
        "run",
        "--inject",
        "SENSIBO_API_KEY=SENSIBO_API_KEY",
        "--",
        *plain,
    ]
    # The only dash-prefixed tokens anywhere: the wrapper's two, plus sensibo's closed set.
    dashed = {token for token in wrapped if token.startswith("-")}
    assert dashed <= {"--inject", "--", "--power", "--apply", "--json"}


def test_a_key_already_in_the_environment_wins_and_grant_is_not_called() -> None:
    plain = sensibo._read_argv(POD_ID)
    assert sensibo._with_grant(plain, "SENSIBO_API_KEY", env={"SENSIBO_API_KEY": "x"}) == plain


@pytest.mark.parametrize(
    "hostile",
    ["", "--inject", "-x", "A=B", "a b", "lower_case", "X;rm", "../X", "X" * 65, 7],
)
def test_a_hostile_secret_name_is_refused_before_any_process_starts(
    hostile: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log_path = _install_fake_sensibo(tmp_path, monkeypatch, responses=[{}])
    grant_log = _install_fake_grant(tmp_path, monkeypatch)
    monkeypatch.delenv("SENSIBO_API_KEY", raising=False)
    with pytest.raises(ValueError):
        sensibo.power(POD_ID, True, grant_secret=hostile)  # type: ignore[arg-type]
    assert _read_calls(log_path) == []
    assert _read_calls(grant_log) == []


def test_power_runs_sensibo_through_grant_and_the_child_gets_the_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log_path = _install_fake_sensibo(
        tmp_path,
        monkeypatch,
        responses=[{"applied": True, "method": "patch", "changes": {"on": {"from": False}}}],
    )
    grant_log = _install_fake_grant(tmp_path, monkeypatch)
    monkeypatch.delenv("SENSIBO_API_KEY", raising=False)

    result = sensibo.power(POD_ID, True, apply=True, grant_secret="SENSIBO_API_KEY")

    assert result["acted"] is True
    assert _read_calls(grant_log) == [
        [
            "run",
            "--inject",
            "SENSIBO_API_KEY=SENSIBO_API_KEY",
            "--",
            "sensibo",
            "set",
            POD_ID,
            "--power",
            "on",
            "--apply",
            "--json",
        ]
    ]
    # sensibo itself saw only its own locked argv: grant consumed the wrapper.
    assert _read_calls(log_path) == [["set", POD_ID, "--power", "on", "--apply", "--json"]]
    # The key never entered THIS process.
    assert "SENSIBO_API_KEY" not in os.environ


def test_status_through_grant_still_never_passes_apply(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log_path = _install_fake_sensibo(
        tmp_path,
        monkeypatch,
        responses=[
            {"applied": False, "changes": {}},
            {"readings": {"temperature": 24.0, "humidity": 50.0}},
        ],
    )
    _install_fake_grant(tmp_path, monkeypatch)
    monkeypatch.delenv("SENSIBO_API_KEY", raising=False)

    status = sensibo.status(POD_ID, grant_secret="SENSIBO_API_KEY")

    assert status["power"] == "on"
    assert all("--apply" not in call for call in _read_calls(log_path))


def test_a_missing_grant_binary_is_did_not_act_not_a_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_sensibo(tmp_path, monkeypatch, responses=[{"applied": True}])
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))  # sensibo is there, grant is not
    monkeypatch.delenv("SENSIBO_API_KEY", raising=False)
    result = sensibo.power(POD_ID, True, apply=True, grant_secret="SENSIBO_API_KEY")
    assert result["acted"] is False


def test_the_secret_name_never_appears_in_results(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_sensibo(tmp_path, monkeypatch, responses=[{"applied": False, "changes": {}}])
    _install_fake_grant(tmp_path, monkeypatch)
    monkeypatch.delenv("SENSIBO_API_KEY", raising=False)
    result = sensibo.power(POD_ID, True, grant_secret="SENSIBO_API_KEY")
    assert "value-of-" not in json.dumps(result)

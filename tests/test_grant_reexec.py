"""The lobes key is consumed by THIS process, so `grant` injects it by re-exec.

When a verb needs the lobes gateway key, the key is not in the environment and
config names a grant secret, the process replaces itself with
``grant run --inject SHABBOS_GOY_LOBES_API_KEY=<NAME> -- python -m shabbos_goy <argv>``.
The key then exists only in that process's environment: no env file, no shell
profile, nothing on disk.
"""

from __future__ import annotations

import json
import sys

import pytest

from shabbos_goy import grant_inject
from shabbos_goy.config import load_config


def _config(tmp_path, block):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"grant": block} if block is not None else {}), "utf-8")
    return load_config(path=path)


class _Exec:
    def __init__(self, error: Exception | None = None) -> None:
        self.calls: list[tuple[str, list[str], dict]] = []
        self.error = error

    def __call__(self, file, argv, env):
        self.calls.append((file, list(argv), dict(env)))
        if self.error:
            raise self.error


def test_config_names_the_lobes_secret_and_validates_it(tmp_path) -> None:
    assert _config(tmp_path, {"lobes_api_key": "LOBES_GATEWAY_API_KEY"}).grant_lobes_secret == (
        "LOBES_GATEWAY_API_KEY"
    )
    assert _config(tmp_path, None).grant_lobes_secret is None
    for bad in ("--inject", "a b", "lower", "X=Y", "", 7):
        assert _config(tmp_path, {"lobes_api_key": bad}).grant_lobes_secret is None


def test_reexecs_under_grant_when_the_key_is_missing_and_a_secret_is_named(tmp_path) -> None:
    run = _Exec()
    config = _config(tmp_path, {"lobes_api_key": "LOBES_GATEWAY_API_KEY"})
    grant_inject.ensure_lobes_key(
        config, env={"PATH": "/usr/bin"}, argv=["listen", "--apply"], execvpe=run
    )
    assert len(run.calls) == 1
    file, argv, env = run.calls[0]
    assert file == "grant"
    assert argv == [
        "grant",
        "run",
        "--inject",
        "SHABBOS_GOY_LOBES_API_KEY=LOBES_GATEWAY_API_KEY",
        "--",
        sys.executable,
        "-m",
        "shabbos_goy",
        "listen",
        "--apply",
    ]
    # The loop guard travels to the child; the key itself is grant's to add.
    assert env[grant_inject.GUARD_ENV] == "1"
    assert "SHABBOS_GOY_LOBES_API_KEY" not in env


@pytest.mark.parametrize("present", ["SHABBOS_GOY_LOBES_API_KEY", "GATEWAY_API_KEY"])
def test_a_key_already_in_the_environment_means_no_reexec(tmp_path, present) -> None:
    run = _Exec()
    config = _config(tmp_path, {"lobes_api_key": "LOBES_GATEWAY_API_KEY"})
    grant_inject.ensure_lobes_key(config, env={present: "x"}, argv=["listen"], execvpe=run)
    assert run.calls == []


def test_no_secret_named_means_no_reexec(tmp_path) -> None:
    run = _Exec()
    grant_inject.ensure_lobes_key(_config(tmp_path, None), env={}, argv=["listen"], execvpe=run)
    assert run.calls == []


def test_the_guard_prevents_a_reexec_loop_when_grant_did_not_supply_the_key(tmp_path) -> None:
    run = _Exec()
    config = _config(tmp_path, {"lobes_api_key": "LOBES_GATEWAY_API_KEY"})
    grant_inject.ensure_lobes_key(
        config, env={grant_inject.GUARD_ENV: "1"}, argv=["listen"], execvpe=run
    )
    assert run.calls == []


def test_a_missing_grant_binary_is_not_a_crash(tmp_path) -> None:
    run = _Exec(error=FileNotFoundError("grant"))
    config = _config(tmp_path, {"lobes_api_key": "LOBES_GATEWAY_API_KEY"})
    # Returns normally: the verb then reports the missing key in its own named way.
    assert grant_inject.ensure_lobes_key(config, env={}, argv=["preflight"], execvpe=run) is False


def test_a_broken_config_never_reexecs(tmp_path) -> None:
    run = _Exec()
    broken = load_config(path=tmp_path / "missing.json")
    grant_inject.ensure_lobes_key(broken, env={}, argv=["listen"], execvpe=run)
    assert run.calls == []


@pytest.mark.parametrize("verb", [["preflight"], ["classify", "x"], ["listen", "--no-dashboard"]])
def test_the_verbs_that_need_the_lobes_key_ask_for_it(tmp_path, monkeypatch, verb) -> None:
    """Wiring: each lobes-consuming verb calls ensure_lobes_key with its loaded config."""
    from shabbos_goy.cli import main

    path = tmp_path / "config.json"
    path.write_text(json.dumps({"grant": {"lobes_api_key": "LOBES_GATEWAY_API_KEY"}}), "utf-8")
    seen: list[str | None] = []

    def fake(config, **kwargs):
        seen.append(config.grant_lobes_secret)
        raise SystemExit(0)  # stand in for the process being replaced

    monkeypatch.setattr(grant_inject, "ensure_lobes_key", fake)
    monkeypatch.delenv("SHABBOS_GOY_LOBES_API_KEY", raising=False)
    monkeypatch.delenv("GATEWAY_API_KEY", raising=False)
    with pytest.raises(SystemExit):
        main([*verb, "--config", str(path)])
    assert seen == ["LOBES_GATEWAY_API_KEY"]

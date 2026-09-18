"""Tests for shabbos_goy.config — the JSON config + whitelist loader.

No network, no microphone, no lobes server, no Sensibo account, no real
sleeping: everything here is tmp-path JSON and injected env mappings.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from shabbos_goy import config as config_mod
from shabbos_goy.cli._errors import EXIT_ENV_ERROR, EXIT_USER_ERROR, CliError

FIXTURE = Path(__file__).parent / "fixtures" / "config.example.json"


def _load_fixture_data() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _write(tmp_path: Path, data: dict, name: str = "config.json") -> Path:
    p = tmp_path / name
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# path resolution: flag/path > env var > XDG default
# ---------------------------------------------------------------------------


def test_resolve_config_path_explicit_path_wins_over_everything(tmp_path):
    explicit = tmp_path / "explicit.json"
    env = {
        config_mod.ENV_CONFIG_PATH: str(tmp_path / "from-env.json"),
        config_mod.ENV_XDG_CONFIG_HOME: str(tmp_path / "xdg"),
    }
    resolved = config_mod.resolve_config_path(path=explicit, env=env)
    assert resolved == explicit


def test_resolve_config_path_env_var_wins_over_xdg_default(tmp_path):
    env = {
        config_mod.ENV_CONFIG_PATH: str(tmp_path / "from-env.json"),
        config_mod.ENV_XDG_CONFIG_HOME: str(tmp_path / "xdg"),
    }
    resolved = config_mod.resolve_config_path(path=None, env=env)
    assert resolved == tmp_path / "from-env.json"


def test_resolve_config_path_xdg_default_when_nothing_else_set(tmp_path):
    env = {config_mod.ENV_XDG_CONFIG_HOME: str(tmp_path / "xdg")}
    resolved = config_mod.resolve_config_path(path=None, env=env)
    assert resolved == tmp_path / "xdg" / "shabbos-goy" / "config.json"


def test_resolve_config_path_falls_back_to_home_config_when_xdg_unset(monkeypatch, tmp_path):
    monkeypatch.setattr(config_mod.Path, "home", classmethod(lambda cls: tmp_path))
    resolved = config_mod.resolve_config_path(path=None, env={})
    assert resolved == tmp_path / ".config" / "shabbos-goy" / "config.json"


# ---------------------------------------------------------------------------
# AC1: the effective whitelist is exactly what the config says, no code change
# ---------------------------------------------------------------------------


def test_valid_config_loads_with_no_error(tmp_path):
    data = _load_fixture_data()
    path = _write(tmp_path, data)
    cfg = config_mod.load_config(path=path, env={})
    assert cfg.ok
    assert cfg.error is None
    assert cfg.is_whitelisted("sensibo", "<pod-id-1>")
    assert cfg.is_whitelisted("sensibo", "<pod-id-2>")


def test_removing_a_pod_id_removes_it_from_the_effective_whitelist(tmp_path):
    data = _load_fixture_data()
    data["whitelist"]["sensibo"]["pods"].remove("<pod-id-2>")
    path = _write(tmp_path, data)
    cfg = config_mod.load_config(path=path, env={})
    assert cfg.is_whitelisted("sensibo", "<pod-id-1>") is True
    assert cfg.is_whitelisted("sensibo", "<pod-id-2>") is False


def test_removing_a_tool_removes_it_from_the_effective_whitelist(tmp_path):
    data = _load_fixture_data()
    del data["whitelist"]["sensibo"]
    path = _write(tmp_path, data)
    cfg = config_mod.load_config(path=path, env={})
    assert cfg.whitelist == {}
    assert cfg.is_whitelisted("sensibo", "<pod-id-1>") is False


def test_adding_a_pod_id_extends_the_effective_whitelist_no_code_change(tmp_path):
    data = _load_fixture_data()
    data["whitelist"]["sensibo"]["pods"].append("<pod-id-3>")
    path = _write(tmp_path, data)
    cfg = config_mod.load_config(path=path, env={})
    assert cfg.is_whitelisted("sensibo", "<pod-id-3>") is True


# ---------------------------------------------------------------------------
# AC2: missing/unreadable/malformed config fails closed with a named CliError
# ---------------------------------------------------------------------------


def test_missing_config_file_yields_empty_whitelist_and_named_cli_error(tmp_path):
    path = tmp_path / "does-not-exist.json"
    cfg = config_mod.load_config(path=path, env={})
    assert cfg.whitelist == {}
    assert not cfg.ok
    assert isinstance(cfg.error, CliError)
    assert cfg.error.code == EXIT_ENV_ERROR
    assert "not found" in cfg.error.message


def test_unreadable_config_file_yields_empty_whitelist_and_named_cli_error(tmp_path):
    # A directory where a file is expected: is_file() is False for XDG-default
    # resolution semantics, but load_config resolves an *explicit* path, so
    # exercise the read-failure branch by pointing straight at a directory
    # that we then force through the read path.
    directory = tmp_path / "config.json"
    directory.mkdir()
    cfg = config_mod.load_config(path=directory, env={})
    assert cfg.whitelist == {}
    assert not cfg.ok
    assert isinstance(cfg.error, CliError)
    assert cfg.error.code == EXIT_ENV_ERROR


def test_malformed_json_yields_empty_whitelist_and_named_cli_error(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("{ not valid json", encoding="utf-8")
    cfg = config_mod.load_config(path=path, env={})
    assert cfg.whitelist == {}
    assert not cfg.ok
    assert isinstance(cfg.error, CliError)
    assert cfg.error.code == EXIT_ENV_ERROR
    assert "JSON" in cfg.error.message


def test_json_that_is_not_an_object_yields_empty_whitelist_and_named_cli_error(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")
    cfg = config_mod.load_config(path=path, env={})
    assert cfg.whitelist == {}
    assert not cfg.ok
    assert isinstance(cfg.error, CliError)


def test_error_config_fails_closed_on_every_structured_accessor(tmp_path):
    path = tmp_path / "does-not-exist.json"
    cfg = config_mod.load_config(path=path, env={})
    assert cfg.location == {}
    assert cfg.candle_lighting_offset_minutes is None
    assert cfg.tzeit_definition is None
    assert cfg.region is None
    assert cfg.rate_limits == {}
    assert cfg.strict_mode_delay_seconds is None
    assert cfg.volume == {}
    assert cfg.join_gap_ms is None
    assert cfg.ring_sizes == {}
    assert cfg.dashboard_bind_address is None


# ---------------------------------------------------------------------------
# AC2: the only representable AC argument is power on|off
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["on", "off"])
def test_validate_ac_argument_accepts_power_on_off(value):
    config_mod.validate_ac_argument("power", value)  # must not raise


@pytest.mark.parametrize(
    "action,value",
    [
        ("power", "ON"),
        ("power", "true"),
        ("power", "1"),
        ("power", ""),
        ("mode", "cool"),
        ("temperature", "24"),
        ("power", "cool"),
    ],
)
def test_validate_ac_argument_rejects_anything_else(action, value):
    with pytest.raises(CliError) as excinfo:
        config_mod.validate_ac_argument(action, value)
    assert excinfo.value.code == EXIT_USER_ERROR


# ---------------------------------------------------------------------------
# AC3: config carries the full documented shape
# ---------------------------------------------------------------------------


def test_config_carries_the_full_documented_shape(tmp_path):
    data = _load_fixture_data()
    path = _write(tmp_path, data)
    cfg = config_mod.load_config(path=path, env={})

    assert cfg.location == {"lat": 31.78, "lon": 35.22, "timezone": "Asia/Jerusalem"}
    assert cfg.candle_lighting_offset_minutes == 18
    assert cfg.tzeit_definition == "3_medium_stars"
    assert cfg.region == "israel"
    assert cfg.rate_limits == {
        "min_interval_seconds": 600,
        "daily_cap": 12,
        "retry_window_seconds": 300,
        "retry_max_attempts": 5,
    }
    assert cfg.strict_mode_delay_seconds == 15
    assert cfg.volume == {"level": 0.0, "muted": True, "min": 0.0, "max": 0.7, "step": 0.1}
    assert cfg.join_gap_ms == 500
    assert cfg.ring_sizes == {"transcript_buffer": 20, "action_log": 200}
    assert cfg.dashboard_bind_address == "127.0.0.1:8787"


def test_example_fixture_has_no_real_secrets_or_hosts():
    """Guard against the fixture drifting away from placeholder-only values."""
    data = _load_fixture_data()
    pods = data["whitelist"]["sensibo"]["pods"]
    assert all(pod.startswith("<") and pod.endswith(">") for pod in pods)
    assert data["dashboard_bind_address"].split(":")[0] in {"127.0.0.1", "localhost"}


def test_example_rate_limits_feed_the_limits_module_unchanged() -> None:
    """The example config's keys are exactly what LimitsConfig.from_dict reads,
    and the strict-mode delay matches the decided default of about 15 s."""
    from shabbos_goy.config import load_config as _load
    from shabbos_goy.limits import LimitsConfig

    cfg = _load(path=FIXTURE)
    assert cfg.ok
    limits = LimitsConfig.from_dict(
        {**cfg.rate_limits, "strict_delay_seconds": cfg.strict_mode_delay_seconds}
    )
    assert limits.daily_cap == 12
    assert limits.min_interval_seconds == 600.0
    assert limits.strict_delay_seconds == 15.0

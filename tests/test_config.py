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
        "off_min_interval_seconds": 60,
        "on_after_off_min_interval_seconds": 240,
        "daily_cap": 48,
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
    assert limits.daily_cap == 48
    assert limits.off_min_interval_seconds == 60.0
    assert limits.on_after_off_min_interval_seconds == 240.0
    assert limits.min_interval_seconds == 600.0
    assert limits.strict_delay_seconds == 15.0


# --------------------------------------------------------------------------
# accessors added for t14 (callers must stop reaching into config.raw)
# --------------------------------------------------------------------------


def _config(**raw):
    from shabbos_goy.config import Config

    return Config(path=FIXTURE, raw=dict(raw), error=None)


def test_dashboard_allow_non_tailnet_is_true_only_for_a_literal_true():
    assert _config(dashboard_allow_non_tailnet=True).dashboard_allow_non_tailnet is True
    assert _config(dashboard_allow_non_tailnet="yes").dashboard_allow_non_tailnet is False
    assert _config().dashboard_allow_non_tailnet is False


def test_dashboard_hostnames_fails_closed_to_an_empty_list():
    assert _config(dashboard_hostnames=["spark", ""]).dashboard_hostnames == ["spark"]
    assert _config(dashboard_hostnames="spark").dashboard_hostnames == []
    assert _config().dashboard_hostnames == []


def test_min_confidence_is_none_unless_a_usable_fraction_is_configured():
    assert _config(min_confidence=0.75).min_confidence == 0.75
    assert _config(min_confidence=1.5).min_confidence is None
    assert _config(min_confidence=True).min_confidence is None
    assert _config().min_confidence is None


def test_context_window_bounds_are_read_and_validated():
    cfg = _config(context_window={"max_items": 5, "max_age_seconds": 120, "max_render_chars": 400})
    assert cfg.context_max_items == 5
    assert cfg.context_max_age_seconds == 120.0
    assert cfg.context_max_render_chars == 400

    bad = _config(context_window={"max_items": 0, "max_age_seconds": -1, "max_render_chars": "x"})
    assert bad.context_max_items is None
    assert bad.context_max_age_seconds is None
    assert bad.context_max_render_chars is None


def test_pipewire_node_names_come_from_the_audio_block():
    cfg = _config(audio={"mic_node": "alsa_input.fake", "speaker_node": "alsa_output.fake"})
    assert cfg.mic_node == "alsa_input.fake"
    assert cfg.speaker_node == "alsa_output.fake"
    # volume_node falls back to the speaker node, as AudioConfig does.
    assert cfg.volume_node == "alsa_output.fake"
    assert _config().mic_node is None


def test_a_broken_config_fails_closed_on_every_new_accessor():
    from shabbos_goy.cli._errors import CliError
    from shabbos_goy.config import Config

    broken = Config(path=FIXTURE, raw={"min_confidence": 0.9}, error=CliError(2, "x", "y"))
    assert broken.min_confidence is None
    assert broken.dashboard_hostnames == []
    assert broken.dashboard_allow_non_tailnet is False
    assert broken.mic_node is None
    assert broken.context_max_items is None


def test_grant_sensibo_secret_is_read_from_config_and_validated(tmp_path) -> None:
    from shabbos_goy.config import load_config as _load

    def cfg(block):
        path = tmp_path / "c.json"
        path.write_text(json.dumps({"grant": block} if block is not None else {}), "utf-8")
        return _load(path=path)

    assert cfg({"sensibo_api_key": "SENSIBO_API_KEY"}).grant_sensibo_secret == "SENSIBO_API_KEY"
    assert cfg(None).grant_sensibo_secret is None
    # A name that could become an option or a shell fragment is never passed on.
    for bad in ("--inject", "a b", "lower", "X=Y", "", 7, ["SENSIBO_API_KEY"]):
        assert cfg({"sensibo_api_key": bad}).grant_sensibo_secret is None

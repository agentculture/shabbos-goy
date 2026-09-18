"""The lobes session config comes from the environment and nowhere else."""

from __future__ import annotations

import pytest

from shabbos_goy.lobes import config as cfg


def test_url_and_key_are_read_from_env() -> None:
    conf = cfg.config_from_env(
        {
            "SHABBOS_GOY_LOBES_URL": "ws://lobes-host.invalid:8001",
            "SHABBOS_GOY_LOBES_API_KEY": "k-from-env",
        }
    )
    assert (conf.host, conf.port) == ("lobes-host.invalid", 8001)
    assert conf.api_key == "k-from-env"
    assert conf.language == "he"
    assert conf.input_sample_rate == 16000


def test_gateway_api_key_is_accepted_as_the_fallback_variable() -> None:
    conf = cfg.config_from_env(
        {"SHABBOS_GOY_LOBES_URL": "ws://127.0.0.1:8001", "GATEWAY_API_KEY": "k2"}
    )
    assert conf.api_key == "k2"


def test_a_missing_url_is_a_named_environment_error() -> None:
    with pytest.raises(cfg.LobesConfigError) as excinfo:
        cfg.config_from_env({})
    assert "SHABBOS_GOY_LOBES_URL" in str(excinfo.value)


@pytest.mark.parametrize("url", ["", "not-a-url", "ws://", "ws://host:notaport"])
def test_a_malformed_url_is_a_named_environment_error(url: str) -> None:
    with pytest.raises(cfg.LobesConfigError):
        cfg.config_from_env({"SHABBOS_GOY_LOBES_URL": url})


def test_an_unsupported_sample_rate_is_refused_by_name() -> None:
    with pytest.raises(cfg.LobesConfigError) as excinfo:
        cfg.config_from_env(
            {
                "SHABBOS_GOY_LOBES_URL": "ws://127.0.0.1:8001",
                "SHABBOS_GOY_LOBES_SAMPLE_RATE": "8000",
            }
        )
    assert "SHABBOS_GOY_LOBES_SAMPLE_RATE" in str(excinfo.value)


def test_http_and_https_urls_map_to_ws_and_wss_with_default_ports() -> None:
    plain = cfg.config_from_env({"SHABBOS_GOY_LOBES_URL": "http://host.invalid"})
    assert (plain.port, plain.tls) == (80, False)
    secure = cfg.config_from_env({"SHABBOS_GOY_LOBES_URL": "wss://host.invalid"})
    assert (secure.port, secure.tls) == (443, True)


def test_the_realtime_path_is_ears_only_and_carries_language_and_rate() -> None:
    conf = cfg.config_from_env(
        {
            "SHABBOS_GOY_LOBES_URL": "ws://127.0.0.1:8001",
            "SHABBOS_GOY_LOBES_LANGUAGE": "he",
        }
    )
    assert conf.realtime_path.startswith("/v1/realtime?")
    assert "language=he" in conf.realtime_path
    assert "input_sample_rate=16000" in conf.realtime_path
    assert "tool" not in conf.realtime_path


def test_headers_carry_the_bearer_token_only_when_a_key_is_configured() -> None:
    keyed = cfg.config_from_env(
        {"SHABBOS_GOY_LOBES_URL": "ws://127.0.0.1:8001", "SHABBOS_GOY_LOBES_API_KEY": "k"}
    )
    assert keyed.handshake_headers() == {"Authorization": "Bearer k"}
    keyless = cfg.config_from_env({"SHABBOS_GOY_LOBES_URL": "ws://127.0.0.1:8001"})
    assert keyless.handshake_headers() == {}


def test_repr_never_leaks_the_api_key() -> None:
    conf = cfg.config_from_env(
        {
            "SHABBOS_GOY_LOBES_URL": "ws://127.0.0.1:8001",
            "SHABBOS_GOY_LOBES_API_KEY": "super-secret-token",
        }
    )
    assert "super-secret-token" not in repr(conf)
    assert "super-secret-token" not in str(conf)


def test_module_declares_no_default_host_or_key() -> None:
    """Host and key come from env only — no baked-in fallback anywhere."""
    source = cfg.__file__
    with open(source, "r", encoding="utf-8") as handle:
        text = handle.read()
    for forbidden in ("Bearer sk-", '8001"', "lobes-host"):
        assert forbidden not in text

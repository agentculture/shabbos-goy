"""Criterion 2, first half: the dashboard refuses to bind anywhere but the tailnet.

The dashboard carries no token (it is reachable over Tailscale only), so the
bind address IS half of its security boundary. These tests pin exactly which
addresses are accepted and prove the refusal is not decoration: a refused
address never opens a socket.
"""

from __future__ import annotations

import pytest

from shabbos_goy.config import load_config
from shabbos_goy.web import (
    DashboardServer,
    control_server,
    parse_bind_address,
    validate_bind_address,
)
from shabbos_goy.web.bind import BindRefused

from .test_web_support import make_stack, serving

TAILNET_V4 = "100.101.102.103"
TAILNET_V6 = "fd7a:115c:a1e0::1234"


def build(stack, **kwargs) -> DashboardServer:
    return DashboardServer(stack.pipeline, stack.mode_provider, stack.config, **kwargs)


@pytest.mark.parametrize("address", ["127.0.0.1", "::1", "localhost", TAILNET_V4, TAILNET_V6])
def test_tailnet_and_loopback_addresses_are_accepted(address: str) -> None:
    assert validate_bind_address(address) == address


@pytest.mark.parametrize(
    "address",
    [
        "0.0.0.0",  # nosec B104 - the point of the test is that this is refused
        "::",
        "192.168.1.10",
        "10.0.0.5",
        "172.16.3.4",
        "169.254.10.10",
        "203.0.113.7",
        "fe80::1",
        "dashboard.example.net",
        "",
    ],
)
def test_wildcard_lan_public_and_name_addresses_are_refused(address: str) -> None:
    with pytest.raises(BindRefused):
        validate_bind_address(address)


@pytest.mark.parametrize("address", ["0.0.0.0", "192.168.1.10"])  # nosec B104
def test_non_tailnet_addresses_are_accepted_only_when_config_says_so(address: str) -> None:
    assert validate_bind_address(address, allow_non_tailnet=True) == address


def test_refusal_names_the_address_and_the_config_key() -> None:
    with pytest.raises(BindRefused) as excinfo:
        validate_bind_address("0.0.0.0")  # nosec B104
    message = str(excinfo.value)
    assert "0.0.0.0" in message
    assert "dashboard_allow_non_tailnet" in message


@pytest.mark.parametrize(
    "value,expected",
    [
        ("127.0.0.1:8787", ("127.0.0.1", 8787)),
        ("127.0.0.1:0", ("127.0.0.1", 0)),
        (f"[{TAILNET_V6}]:8787", (TAILNET_V6, 8787)),
        (TAILNET_V4, (TAILNET_V4, 8787)),
    ],
)
def test_parse_bind_address(value: str, expected: tuple[str, int]) -> None:
    assert parse_bind_address(value) == expected


@pytest.mark.parametrize("value", ["127.0.0.1:notaport", "127.0.0.1:99999", "127.0.0.1:-1"])
def test_parse_bind_address_refuses_a_bad_port(value: str) -> None:
    with pytest.raises(BindRefused):
        parse_bind_address(value)


def test_a_refused_bind_returns_a_result_and_never_raises_into_the_caller(tmp_path) -> None:
    stack = make_stack(
        tmp_path, config_overrides={"dashboard_bind_address": "0.0.0.0:0"}  # nosec B104
    )
    server = build(stack)

    result = server.start()

    assert result.ok is False
    assert result.port == 0
    assert result.reason == "bind_refused"
    assert server.url is None
    # Stopping a server that never bound is a no-op, not an error.
    server.stop()


def test_a_bind_failure_from_the_os_is_also_a_result_not_an_exception(tmp_path) -> None:
    stack = make_stack(tmp_path)

    def explode(*args, **kwargs):
        raise OSError(98, "Address already in use")

    server = build(stack, server_factory=explode)
    result = server.start()

    assert result.ok is False
    assert result.reason == "bind_failed"
    assert server.url is None


def test_an_allowed_bind_reports_the_ephemeral_port_it_actually_got(tmp_path) -> None:
    stack = make_stack(tmp_path)
    server = build(stack)

    with serving(server) as result:
        assert result.ok is True
        assert result.address == "127.0.0.1"
        assert result.port > 0
        assert server.url == f"http://127.0.0.1:{result.port}"


def test_an_explicit_override_binds_a_non_tailnet_address(tmp_path) -> None:
    stack = make_stack(
        tmp_path,
        config_overrides={
            "dashboard_bind_address": "127.0.0.1:0",
            "dashboard_allow_non_tailnet": True,
        },
    )
    with serving(build(stack)) as result:
        assert result.ok is True


def test_a_broken_config_still_resolves_to_loopback_only(tmp_path) -> None:
    """A config that failed to load has no bind address at all; the fallback
    is loopback, never a wildcard. Resolved without opening a socket."""
    config = load_config(path=tmp_path / "missing.json")
    assert not config.ok
    stack = make_stack(tmp_path, config=config)
    assert build(stack).resolve_bind() == ("127.0.0.1", 8787)


def test_the_control_endpoint_constructor_is_loopback_only(tmp_path) -> None:
    """``control_server`` is what the CLI talks to: 127.0.0.1, whatever config says."""
    stack = make_stack(tmp_path, config_overrides={"dashboard_bind_address": f"{TAILNET_V4}:8787"})
    server = control_server(stack.pipeline, stack.mode_provider, stack.config)
    with serving(server) as result:
        assert result.ok is True
        assert result.address == "127.0.0.1"

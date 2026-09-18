"""Which addresses the dashboard is allowed to listen on.

The dashboard is reachable **over Tailscale only** and carries no token, so
this module is half of its security boundary (the other half is the
cross-origin refusal in :mod:`shabbos_goy.web.server`). A dashboard that can
turn the air conditioning on must not be one ``0.0.0.0`` away from every
device on a cafe's wifi.

Accepted by default:

* ``127.0.0.1`` / ``::1`` / ``localhost`` -- loopback, which is what the
  CLI's control endpoint and every test bind;
* ``100.64.0.0/10`` -- the CGNAT range Tailscale assigns its nodes;
* ``fd7a:115c:a1e0::/48`` -- Tailscale's ULA prefix.

Refused by default: the wildcards ``0.0.0.0`` and ``::``, every LAN/private
range, link-local, every public address, and anything that is not an IP
literal at all (a hostname's resolution is not ours to trust). An operator
who genuinely wants one of those sets ``dashboard_allow_non_tailnet: true``
in config and takes responsibility for it -- the escape hatch is data, like
the action whitelist, and it is never the default.

Nothing here opens a socket: a refusal happens before any bind is attempted.
"""

from __future__ import annotations

import ipaddress

__all__ = [
    "ALLOW_NON_TAILNET_KEY",
    "BindRefused",
    "DEFAULT_PORT",
    "TAILNET_V4",
    "TAILNET_V6",
    "is_tailnet_address",
    "parse_bind_address",
    "validate_bind_address",
]

#: The CGNAT range Tailscale hands out (100.64.0.0 - 100.127.255.255).
TAILNET_V4 = ipaddress.ip_network("100.64.0.0/10")

#: Tailscale's IPv6 ULA prefix.
TAILNET_V6 = ipaddress.ip_network("fd7a:115c:a1e0::/48")

#: The config key that widens the rule. Named in every refusal message so an
#: operator never has to grep for it.
ALLOW_NON_TAILNET_KEY = "dashboard_allow_non_tailnet"

DEFAULT_PORT = 8787

#: Loopback spellings accepted without an IP-literal check.
_LOOPBACK_NAMES = frozenset({"localhost"})


class BindRefused(ValueError):
    """The configured bind address is not one the dashboard may listen on.

    Raised before any socket exists. Callers that must not fail hard --
    :meth:`shabbos_goy.web.server.DashboardServer.start` -- catch it and turn
    it into a result they can report and retry.
    """


def is_tailnet_address(value: str) -> bool:
    """Is ``value`` an IP literal inside one of Tailscale's own ranges?"""
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    if address.version == 4:
        return address in TAILNET_V4
    return address in TAILNET_V6


def _is_loopback(value: str) -> bool:
    if value in _LOOPBACK_NAMES:
        return True
    try:
        return ipaddress.ip_address(value).is_loopback
    except ValueError:
        return False


def validate_bind_address(address: str, *, allow_non_tailnet: bool = False) -> str:
    """Return ``address`` if the dashboard may bind it; raise otherwise.

    ``allow_non_tailnet`` comes from config (:data:`ALLOW_NON_TAILNET_KEY`)
    and is the only way to widen the rule. It is never inferred.
    """
    if allow_non_tailnet:
        if not isinstance(address, str) or not address:
            raise BindRefused(
                f"empty dashboard bind address (even with {ALLOW_NON_TAILNET_KEY} set)"
            )
        return address
    if not isinstance(address, str) or not address:
        raise BindRefused(
            "empty dashboard bind address: set dashboard_bind_address to a tailnet "
            f"or loopback address, or set {ALLOW_NON_TAILNET_KEY}: true to widen the rule"
        )
    if _is_loopback(address) or is_tailnet_address(address):
        return address
    raise BindRefused(
        f"refusing to bind {address!r}: the dashboard listens on loopback or a "
        f"Tailscale address ({TAILNET_V4}, {TAILNET_V6}) only. "
        f"Set {ALLOW_NON_TAILNET_KEY}: true in config to allow anything else."
    )


def parse_bind_address(value: object, *, default_port: int = DEFAULT_PORT) -> tuple[str, int]:
    """Split a ``host``/``host:port``/``[v6]:port`` config value.

    A bare host takes ``default_port``. Port ``0`` is legitimate (ask the OS
    for an ephemeral one), which is what the tests bind.
    """
    if not isinstance(value, str) or not value.strip():
        raise BindRefused("dashboard_bind_address must be a non-empty string")
    text = value.strip()

    if text.startswith("["):
        closing = text.find("]")
        if closing < 0:
            raise BindRefused(f"malformed bracketed address: {value!r}")
        host = text[1:closing]
        remainder = text[closing + 1 :]
        if not remainder:
            return host, default_port
        if not remainder.startswith(":"):
            raise BindRefused(f"malformed bracketed address: {value!r}")
        return host, _port(remainder[1:], value)

    if text.count(":") > 1:
        # A bare IPv6 literal, no port.
        return text, default_port

    if ":" in text:
        host, _, port_text = text.partition(":")
        return host, _port(port_text, value)

    return text, default_port


def _port(text: str, original: str) -> int:
    try:
        port = int(text)
    except ValueError:
        raise BindRefused(f"port is not a number in {original!r}") from None
    if not 0 <= port <= 65535:
        raise BindRefused(f"port out of range in {original!r}")
    return port

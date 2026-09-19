"""The operator dashboard: one static page plus JSON endpoints, stdlib only.

``http.server`` and ``json``, nothing else -- no build step, no external
asset, no CDN, so the page works on a box whose only route to the world is a
tailnet. The page itself is embedded in :mod:`shabbos_goy.web.page`.

The dashboard reads the running agent (the pipeline's in-memory rings and the
mode resolver) and drives it through the *same* whitelist, rate limits and
adapters the ambient listener uses. See :mod:`shabbos_goy.web.server` for the
gate order and the security boundary, and :mod:`shabbos_goy.web.bind` for
which addresses it may listen on.
"""

from __future__ import annotations

from .bind import (
    ALLOW_NON_TAILNET_KEY,
    BindRefused,
    is_tailnet_address,
    parse_bind_address,
    validate_bind_address,
)
from .page import DASHBOARD_HTML
from .server import (
    BindResult,
    Controls,
    DashboardServer,
    control_server,
    controls_from_pipeline,
)

__all__ = [
    "ALLOW_NON_TAILNET_KEY",
    "DASHBOARD_HTML",
    "BindRefused",
    "BindResult",
    "Controls",
    "DashboardServer",
    "control_server",
    "controls_from_pipeline",
    "is_tailnet_address",
    "parse_bind_address",
    "validate_bind_address",
]

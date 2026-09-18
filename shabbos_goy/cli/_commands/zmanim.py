"""``shabbos-goy zmanim`` -- the current mode window, from config.

Read-only, and never gates an action: computes with the same
:func:`shabbos_goy.mode.resolve_mode` the pipeline calls, but this verb only
describes what it returns. There is deliberately no override plumbing here
-- an override lives in a running listener's memory (see ``mode set``), and
this verb reports the zmanim-and-clock-trust picture, not a live listener's
current override.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone

from shabbos_goy.cli._commands._domain import next_strict_window_payload
from shabbos_goy.cli._output import emit_result
from shabbos_goy.config import load_config
from shabbos_goy.mode import resolve_mode


def _payload(now: datetime, config) -> dict:
    resolved = resolve_mode(now, config)
    return {
        "mode": resolved.mode,
        "window_kinds": list(resolved.kinds),
        "clock_trusted": resolved.clock_trusted,
        "overridden": resolved.overridden,
        "next_strict_window": next_strict_window_payload(now, config),
    }


def _render_text(payload: dict) -> str:
    lines = [
        f"mode: {payload['mode']}",
        f"window kinds: {', '.join(payload['window_kinds']) or '(none)'}",
        f"clock trusted: {payload['clock_trusted']}",
    ]
    win = payload["next_strict_window"]
    if win is None:
        lines.append("next strict window: unknown (no usable location/rules in config)")
    else:
        lines.append(
            f"next strict window (local): {win['starts_at_local']} -> {win['ends_at_local']} "
            f"[{', '.join(win['kinds'])}]"
        )
    return "\n".join(lines)


def cmd_zmanim(args: argparse.Namespace) -> int:
    json_mode = bool(getattr(args, "json", False))
    config = load_config(path=getattr(args, "config", None))
    if not config.ok:
        raise config.error
    now = datetime.now(timezone.utc)
    payload = _payload(now, config)
    if json_mode:
        emit_result(payload, json_mode=True)
    else:
        emit_result(_render_text(payload), json_mode=False)
    return 0


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "zmanim", help="The current mode window: mode, window kinds, next strict window."
    )
    p.add_argument("--config", help="Path to an explicit config file.")
    p.add_argument("--json", action="store_true", help="Emit structured JSON.")
    p.set_defaults(func=cmd_zmanim)

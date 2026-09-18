"""``shabbos-goy mode`` -- mode noun: show (read) and set (override, write).

The resolved mode (weekday/strict) and its in-memory override
(:mod:`shabbos_goy.mode`) live inside the running listener process, not in
this short-lived CLI process, so both verbs talk to the listener's control
endpoint (see :mod:`shabbos_goy.cli._commands._control`).

``mode show`` is the one exception with a graceful, listener-free fallback:
with no listener reachable, it computes the zmanim-implied mode locally
(:func:`shabbos_goy.mode.resolve_mode`, with no override in play, since an
override only ever lives in a running listener) and prints the next strict
window in local time, rather than failing. ``mode set`` has no such
fallback: setting an override with no listener to hold it would do nothing
useful, so it exits 2.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone

from shabbos_goy.cli._commands import _control
from shabbos_goy.cli._commands._domain import next_strict_window_payload
from shabbos_goy.cli._commands.overview import emit_overview
from shabbos_goy.cli._errors import EXIT_SUCCESS
from shabbos_goy.cli._output import emit_result
from shabbos_goy.config import load_config
from shabbos_goy.mode import resolve_mode
from shabbos_goy.policy import MODES

_OVERRIDE_CHOICES = (*MODES, "auto")

_SECTIONS = [
    {
        "title": "Verbs",
        "items": [
            "mode show -- the mode in effect now (asks the listener; falls back to a "
            "local zmanim computation with no listener running)",
            "mode set {weekday,strict,auto} -- force (or clear, with 'auto') the "
            "listener's in-memory override",
        ],
    },
    {
        "title": "Notes",
        "items": [
            "an override lives only in the running listener's memory -- it is never "
            "written to disk and never survives a restart",
            "'mode set' with no listener running exits 2 (nothing would hold the " "override)",
        ],
    },
]


def cmd_mode_overview(args: argparse.Namespace) -> int:
    emit_overview("shabbos-goy mode", _SECTIONS, json_mode=bool(getattr(args, "json", False)))
    return 0


def _no_verb(args: argparse.Namespace) -> int:
    return cmd_mode_overview(args)


def _local_mode_payload(config) -> dict:
    """The zmanim-implied mode, computed here because no listener answered.

    There is no override to apply -- an override lives only in a running
    listener's memory (see the module docstring) -- so this is exactly what a
    freshly-started listener would also report before any override is set.
    """
    now = datetime.now(timezone.utc)
    resolved = resolve_mode(now, config)
    return {
        "listener": False,
        "mode": resolved.mode,
        "window_kinds": list(resolved.kinds),
        "clock_trusted": resolved.clock_trusted,
        "next_strict_window": next_strict_window_payload(now, config),
    }


def cmd_mode_show(args: argparse.Namespace) -> int:
    """Always succeeds, by design: a missing listener is a documented fallback,
    not a failure, and ``mode show`` is the one verb that must answer with no
    listener running. The single exit below is that contract, made explicit --
    ``mode set``, which genuinely cannot fall back, exits 2 instead."""
    json_mode = bool(getattr(args, "json", False))
    config = load_config(path=getattr(args, "config", None))
    base_url = _control.resolve_base_url(config)
    result = _control.get_json(base_url, "/mode")
    if result.ok:
        payload = dict(result.data) if isinstance(result.data, dict) else {}
        payload["listener"] = True
    else:
        payload = _local_mode_payload(config)
    emit_result(payload, json_mode=json_mode)
    return EXIT_SUCCESS


def cmd_mode_set(args: argparse.Namespace) -> int:
    json_mode = bool(getattr(args, "json", False))
    config = load_config(path=getattr(args, "config", None))
    base_url = _control.resolve_base_url(config)
    override = None if args.value == "auto" else args.value
    result = _control.post_json(base_url, "/mode", {"override": override})
    if not result.ok:
        raise _control.no_listener_error(base_url)
    payload = dict(result.data) if isinstance(result.data, dict) else {}
    payload["listener"] = True
    emit_result(payload, json_mode=json_mode)
    return EXIT_SUCCESS


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("mode", help="Mode noun: show (read), set (override; see 'mode overview').")
    p.add_argument("--json", action="store_true", help="Emit structured JSON.")
    p.set_defaults(func=_no_verb, json=False)
    noun_sub = p.add_subparsers(dest="mode_command", parser_class=type(p))

    ov = noun_sub.add_parser("overview", help="Describe the mode noun.")
    ov.add_argument("--json", action="store_true", help="Emit structured JSON.")
    ov.set_defaults(func=cmd_mode_overview)

    sh = noun_sub.add_parser("show", help="The mode in effect now.")
    sh.add_argument("--config", help="Path to an explicit config file.")
    sh.add_argument("--json", action="store_true", help="Emit structured JSON.")
    sh.set_defaults(func=cmd_mode_show)

    se = noun_sub.add_parser("set", help="Force (or clear, with 'auto') the mode override.")
    se.add_argument("value", choices=list(_OVERRIDE_CHOICES), help="weekday, strict, or auto.")
    se.add_argument("--config", help="Path to an explicit config file.")
    se.add_argument("--json", action="store_true", help="Emit structured JSON.")
    se.set_defaults(func=cmd_mode_set)

"""``shabbos-goy volume`` -- volume noun: get (read) and set (write).

Both verbs proxy to the running listener's control endpoint (see
:mod:`shabbos_goy.cli._commands._control`), for the same reason as ``ac``:
the listener owns the real ``wpctl`` adapter and its clamp bounds, so this
CLI never touches the mixer directly. ``set`` is dry-run unless ``--apply``
is passed. With no listener running, both verbs exit 2 with a remediation.
"""

from __future__ import annotations

import argparse

from shabbos_goy.cli._commands import _control
from shabbos_goy.cli._commands.overview import emit_overview
from shabbos_goy.cli._output import emit_result
from shabbos_goy.config import load_config

_DIRECTIONS = ("up", "down")

_SECTIONS = [
    {
        "title": "Verbs",
        "items": [
            "volume get -- current level/muted state (read-only)",
            "volume set {up,down} [--apply] -- step the volume (dry-run unless --apply)",
        ],
    },
    {
        "title": "Notes",
        "items": [
            "both verbs proxy to the running listener's control endpoint",
            "with no listener running, both exit 2 with a remediation",
        ],
    },
]


def cmd_volume_overview(args: argparse.Namespace) -> int:
    emit_overview("shabbos-goy volume", _SECTIONS, json_mode=bool(getattr(args, "json", False)))
    return 0


def _no_verb(args: argparse.Namespace) -> int:
    return cmd_volume_overview(args)


def cmd_volume_get(args: argparse.Namespace) -> int:
    json_mode = bool(getattr(args, "json", False))
    config = load_config(path=getattr(args, "config", None))
    base_url = _control.resolve_base_url(config)
    result = _control.get_json(base_url, "/volume")
    if not result.ok:
        raise _control.no_listener_error(base_url)
    emit_result(result.data, json_mode=json_mode)
    return 0


def cmd_volume_set(args: argparse.Namespace) -> int:
    json_mode = bool(getattr(args, "json", False))
    config = load_config(path=getattr(args, "config", None))
    base_url = _control.resolve_base_url(config)
    apply_ = bool(getattr(args, "apply", False))
    result = _control.post_json(base_url, "/volume", {"direction": args.direction, "apply": apply_})
    if not result.ok:
        raise _control.no_listener_error(base_url)
    emit_result(result.data, json_mode=json_mode)
    return 0


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "volume", help="Volume noun: get (read), set (write; see 'volume overview')."
    )
    p.add_argument("--json", action="store_true", help="Emit structured JSON.")
    p.set_defaults(func=_no_verb, json=False)
    noun_sub = p.add_subparsers(dest="volume_command", parser_class=type(p))

    ov = noun_sub.add_parser("overview", help="Describe the volume noun.")
    ov.add_argument("--json", action="store_true", help="Emit structured JSON.")
    ov.set_defaults(func=cmd_volume_overview)

    g = noun_sub.add_parser("get", help="Current volume level/muted state (read-only).")
    g.add_argument("--config", help="Path to an explicit config file.")
    g.add_argument("--json", action="store_true", help="Emit structured JSON.")
    g.set_defaults(func=cmd_volume_get)

    s = noun_sub.add_parser("set", help="Step the volume. Dry-run unless --apply.")
    s.add_argument("direction", choices=list(_DIRECTIONS), help="up or down.")
    s.add_argument("--apply", action="store_true", help="Actually actuate (default: dry-run).")
    s.add_argument("--config", help="Path to an explicit config file.")
    s.add_argument("--json", action="store_true", help="Emit structured JSON.")
    s.set_defaults(func=cmd_volume_set)

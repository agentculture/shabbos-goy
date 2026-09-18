"""``shabbos-goy ac`` -- AC noun: status (read) and power (write).

Both verbs proxy to the running listener's control endpoint (see
:mod:`shabbos_goy.cli._commands._control`): the listener is the one process
holding the whitelist, the rate limiter and the real sensibo-cli adapter, so
this CLI never calls sensibo-cli directly and never bypasses those gates.
``power`` is dry-run unless ``--apply`` is passed; this CLI's request always
states the flag explicitly (never inferred) so a bug here can never silently
default to acting. With no listener running, both verbs exit 2 with a
remediation.
"""

from __future__ import annotations

import argparse

from shabbos_goy.cli._commands import _control
from shabbos_goy.cli._commands.overview import emit_overview
from shabbos_goy.cli._errors import EXIT_USER_ERROR, CliError
from shabbos_goy.cli._output import emit_result
from shabbos_goy.config import AC_ACTION, ALLOWED_POWER_VALUES, load_config

_SECTIONS = [
    {
        "title": "Verbs",
        "items": [
            "ac status -- current power/temperature/humidity (read-only)",
            "ac power {on,off} [--apply] -- request AC power (dry-run unless --apply)",
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


def cmd_ac_overview(args: argparse.Namespace) -> int:
    emit_overview("shabbos-goy ac", _SECTIONS, json_mode=bool(getattr(args, "json", False)))
    return 0


def _no_verb(args: argparse.Namespace) -> int:
    return cmd_ac_overview(args)


def cmd_ac_status(args: argparse.Namespace) -> int:
    json_mode = bool(getattr(args, "json", False))
    config = load_config(path=getattr(args, "config", None))
    base_url = _control.resolve_base_url(config)
    result = _control.get_json(base_url, "/ac/status")
    if not result.ok:
        raise _control.no_listener_error(base_url)
    emit_result(result.data, json_mode=json_mode)
    return 0


def cmd_ac_power(args: argparse.Namespace) -> int:
    json_mode = bool(getattr(args, "json", False))
    value = args.value
    if value not in ALLOWED_POWER_VALUES:
        raise CliError(
            code=EXIT_USER_ERROR,
            message=f"invalid AC power value: {value!r}",
            remediation=f"use one of {'|'.join(ALLOWED_POWER_VALUES)}",
        )
    config = load_config(path=getattr(args, "config", None))
    base_url = _control.resolve_base_url(config)
    apply_ = bool(getattr(args, "apply", False))
    result = _control.post_json(
        base_url, "/ac/power", {"action": AC_ACTION, "value": value, "apply": apply_}
    )
    if not result.ok:
        raise _control.no_listener_error(base_url)
    emit_result(result.data, json_mode=json_mode)
    return 0


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("ac", help="AC noun: status (read), power (write; see 'ac overview').")
    p.add_argument("--json", action="store_true", help="Emit structured JSON.")
    p.set_defaults(func=_no_verb, json=False)
    noun_sub = p.add_subparsers(dest="ac_command", parser_class=type(p))

    ov = noun_sub.add_parser("overview", help="Describe the ac noun.")
    ov.add_argument("--json", action="store_true", help="Emit structured JSON.")
    ov.set_defaults(func=cmd_ac_overview)

    st = noun_sub.add_parser("status", help="Current AC power/temperature/humidity (read-only).")
    st.add_argument("--config", help="Path to an explicit config file.")
    st.add_argument("--json", action="store_true", help="Emit structured JSON.")
    st.set_defaults(func=cmd_ac_status)

    pw = noun_sub.add_parser("power", help="Request AC power. Dry-run unless --apply.")
    pw.add_argument("value", choices=list(ALLOWED_POWER_VALUES), help="on or off.")
    pw.add_argument("--apply", action="store_true", help="Actually actuate (default: dry-run).")
    pw.add_argument("--config", help="Path to an explicit config file.")
    pw.add_argument("--json", action="store_true", help="Emit structured JSON.")
    pw.set_defaults(func=cmd_ac_power)

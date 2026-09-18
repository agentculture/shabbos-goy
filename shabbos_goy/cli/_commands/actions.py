"""``shabbos-goy actions`` -- the whitelist in effect, and the intent map.

Read-only. Prints two things: the effective actuation whitelist (pure config,
see :class:`shabbos_goy.config.Config.whitelist` -- fails closed, so a broken
or missing config file reports an empty whitelist rather than raising) and
the fixed intent -> tool mapping the (planned) pipeline and listener use to
turn a decided intent into a tool call. The mapping itself is data described
here for a human/agent to read; it never actuates.
"""

from __future__ import annotations

import argparse

from shabbos_goy.cli._output import emit_result
from shabbos_goy.config import load_config

#: Mirrors shabbos_goy.pipeline's intent -> tool table (kept here as
#: descriptive data, not imported, so this verb never depends on the
#: pipeline's private internals).
INTENT_TO_TOOL = [
    {"intent": "cool", "tool": "sensibo", "action": "power", "value": "on"},
    {"intent": "warm", "tool": "sensibo", "action": "power", "value": "off"},
    {"intent": "louder", "tool": "volume", "action": "step", "value": "up"},
    {"intent": "quieter", "tool": "volume", "action": "step", "value": "down"},
    {
        "intent": "status",
        "tool": "speech",
        "action": "spoken_status",
        "value": "weekday_only",
    },
]


def _render_text(whitelist: dict) -> str:
    lines = ["## Whitelist"]
    if not whitelist:
        lines.append("(empty)")
    else:
        for tool, entry in whitelist.items():
            pods = entry.get("pods") if isinstance(entry, dict) else None
            lines.append(f"- {tool}: {pods if pods else '(none)'}")
    lines.append("")
    lines.append("## Intent -> tool")
    for row in INTENT_TO_TOOL:
        lines.append(f"- {row['intent']} -> {row['tool']}.{row['action']}={row['value']}")
    return "\n".join(lines)


def cmd_actions(args: argparse.Namespace) -> int:
    json_mode = bool(getattr(args, "json", False))
    config = load_config(path=getattr(args, "config", None))
    payload = {
        "whitelist": config.whitelist,
        "intent_to_tool": INTENT_TO_TOOL,
    }
    if json_mode:
        emit_result(payload, json_mode=True)
    else:
        emit_result(_render_text(config.whitelist), json_mode=False)
    return 0


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("actions", help="The whitelist in effect, and the intent -> tool mapping.")
    p.add_argument("--config", help="Path to an explicit config file.")
    p.add_argument("--json", action="store_true", help="Emit structured JSON.")
    p.set_defaults(func=cmd_actions)

"""``shabbos-goy learn`` — the learnability affordance.

Prints a structured self-teaching prompt. Must satisfy the agent-first rubric:
>=200 chars and mention purpose, command map, exit codes, --json, and explain.
"""

from __future__ import annotations

import argparse

from shabbos_goy import __version__
from shabbos_goy.cli._output import emit_result

_TEXT = """\
shabbos-goy — a Hebrew-speaking, speech-to-speech household agent that helps
observant Jews on Shabbat and Yom Kippur without the user breaking the day.

Purpose
-------
It never takes a direct command: it only acts on intent it *infers* from
indirect speech (a state remark, a wish, a discomfort). A false positive on
an imperative/request/rebuke is the one failure this repo exists to prevent
— see `shabbos-goy explain classify` and `shabbos-goy explain actions`.

Commands
--------
  shabbos-goy whoami              Identity from culture.yaml.
  shabbos-goy learn                This self-teaching prompt.
  shabbos-goy explain <path>...   Markdown docs for any noun/verb path.
  shabbos-goy overview            Descriptive snapshot of the agent.
  shabbos-goy doctor               Check the agent-identity invariants.
  shabbos-goy cli overview         Describe the CLI surface itself.
  shabbos-goy classify "<text>"    Decide (never act) on one utterance.
  shabbos-goy zmanim                The current mode window, from config.
  shabbos-goy actions               The whitelist in effect, and the intent map.
  shabbos-goy preflight              Check every precondition before Shabbat.
  shabbos-goy ac status|power       AC noun (read/write, via the listener).
  shabbos-goy volume get|set        Volume noun (read/write, via the listener).
  shabbos-goy mode show|set          The mode in effect, and its override.

Machine-readable output
-----------------------
Every command supports --json. Errors in JSON mode emit
{"code", "message", "remediation"} to stderr. Stdout and stderr never mix.

Exit-code policy
----------------
  0 success
  1 user-input error (bad flag, bad path, missing arg)
  2 environment / setup error
  3+ reserved

More detail
-----------
  shabbos-goy explain shabbos-goy
"""


def _as_json_payload() -> dict[str, object]:
    return {
        "tool": "shabbos-goy",
        "version": __version__,
        "purpose": (
            "A Hebrew-speaking, speech-to-speech household agent for Shabbat/Yom "
            "Kippur that never takes a direct command, only infers intent from "
            "indirect speech."
        ),
        "commands": [
            {"path": ["whoami"], "summary": "Identity probe from culture.yaml."},
            {"path": ["learn"], "summary": "Self-teaching prompt."},
            {"path": ["explain"], "summary": "Markdown docs by path."},
            {"path": ["overview"], "summary": "Descriptive snapshot of the agent."},
            {"path": ["doctor"], "summary": "Check the agent-identity invariants."},
            {"path": ["cli", "overview"], "summary": "Describe the CLI surface."},
            {"path": ["classify"], "summary": "Decide (never act) on one utterance."},
            {"path": ["zmanim"], "summary": "The current mode window, from config."},
            {
                "path": ["actions"],
                "summary": "The whitelist in effect, and the intent map.",
            },
            {
                "path": ["preflight"],
                "summary": "Check every precondition before Shabbat starts.",
            },
            {"path": ["ac", "status"], "summary": "Current AC state (read-only)."},
            {"path": ["ac", "power"], "summary": "Request AC power (dry-run unless --apply)."},
            {"path": ["volume", "get"], "summary": "Current volume state (read-only)."},
            {"path": ["volume", "set"], "summary": "Step volume (dry-run unless --apply)."},
            {"path": ["mode", "show"], "summary": "The mode in effect now."},
            {"path": ["mode", "set"], "summary": "Force/clear the mode override."},
        ],
        "exit_codes": {
            "0": "success",
            "1": "user-input error",
            "2": "environment/setup error",
        },
        "json_support": True,
        "explain_pointer": "shabbos-goy explain <path>",
    }


def cmd_learn(args: argparse.Namespace) -> int:
    if getattr(args, "json", False):
        emit_result(_as_json_payload(), json_mode=True)
    else:
        emit_result(_TEXT, json_mode=False)
    return 0


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "learn",
        help="Print a structured self-teaching prompt for agent consumers.",
    )
    p.add_argument("--json", action="store_true", help="Emit structured JSON.")
    p.set_defaults(func=cmd_learn)

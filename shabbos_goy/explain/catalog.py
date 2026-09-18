"""Markdown catalog for ``shabbos-goy explain <path>``.

Each entry is verbatim markdown. Keys are command-path tuples. The empty tuple
and ``("shabbos-goy",)`` both resolve to the root entry.

Keep bodies self-contained: an agent reading one entry should get enough
context without chaining reads.
"""

from __future__ import annotations

_ROOT = """\
# shabbos-goy

A clonable template for AgentCulture mesh agents. It carries an agent-first CLI
(cited from the teken `python-cli` reference), a mesh identity (`culture.yaml` +
`CLAUDE.md`), the canonical guildmaster skill kit under `.claude/skills/`, and a
buildable/deployable package baseline. Clone it, rename the package, edit
`culture.yaml`, and you have a new agent.

## Verbs

- `shabbos-goy whoami` — identity probe from `culture.yaml`.
- `shabbos-goy learn` — structured self-teaching prompt.
- `shabbos-goy explain <path>` — markdown docs for any noun/verb.
- `shabbos-goy overview` — descriptive snapshot of the agent.
- `shabbos-goy doctor` — check the agent-identity invariants.
- `shabbos-goy cli overview` — describe the CLI surface.

## Exit-code policy

- `0` success
- `1` user-input error
- `2` environment / setup error
- `3+` reserved

## See also

- `shabbos-goy explain whoami`
- `shabbos-goy explain doctor`
"""

_WHOAMI = """\
# shabbos-goy whoami

Reports the agent's identity from `culture.yaml`: nick (`suffix`), backend,
served model, and the package version. Read-only.

## Usage

    shabbos-goy whoami
    shabbos-goy whoami --json
"""

_LEARN = """\
# shabbos-goy learn

Prints a structured self-teaching prompt covering purpose, command map,
exit-code policy, `--json` support, and the `explain` pointer.

## Usage

    shabbos-goy learn
    shabbos-goy learn --json
"""

_EXPLAIN = """\
# shabbos-goy explain <path>

Prints markdown documentation for any noun/verb path. Unlike `--help` (terse,
positional), `explain` is global and addressable by path.

## Usage

    shabbos-goy explain shabbos-goy
    shabbos-goy explain whoami
    shabbos-goy explain --json <path>
"""

_OVERVIEW = """\
# shabbos-goy overview

Read-only descriptive snapshot of the agent: identity (from `culture.yaml`), the
verb surface, and the sibling-pattern artifacts the template carries. Accepts an
ignored `target` so a stray path never hard-fails.

## Usage

    shabbos-goy overview
    shabbos-goy overview --json
"""

_DOCTOR = """\
# shabbos-goy doctor

Checks the agent-identity invariants `steward doctor` verifies:
prompt-file-present and backend-consistency (`claude` → `CLAUDE.md`), plus a
skills-present check. Exits 1 when unhealthy.

prompt-file-present requires the *resident* prompt the declared backend
actually reads. Other harness prompt files recognized under the same backend
name (`AGENTS.override.md`, `.pi/SYSTEM.md`, `QWEN.md`) belong to
interactively available harnesses the mesh daemon never loads; they are
reported by the informational harness-prompts check and never substituted.

## Usage

    shabbos-goy doctor
    shabbos-goy doctor --json
"""

_CLI = """\
# shabbos-goy cli

Noun group for CLI-surface introspection. `cli overview` describes the CLI
itself (distinct from the global `overview`, which describes the agent).

## Usage

    shabbos-goy cli overview
    shabbos-goy cli overview --json
"""


ENTRIES: dict[tuple[str, ...], str] = {
    (): _ROOT,
    ("shabbos-goy",): _ROOT,
    ("whoami",): _WHOAMI,
    ("learn",): _LEARN,
    ("explain",): _EXPLAIN,
    ("overview",): _OVERVIEW,
    ("doctor",): _DOCTOR,
    ("cli",): _CLI,
    ("cli", "overview"): _CLI,
}

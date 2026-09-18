# QWEN.md

This file provides guidance to Qwen Code when working with code in this
repository. Qwen Code's context loader reads exactly `QWEN.md` and `AGENTS.md`
in a directory; this repo deliberately ships only `QWEN.md` — there is no
`AGENTS.md` here (each harness gets its own file; see "Prompt files by
harness" below), so this file is the sole source of project guidance for a
Qwen Code session.

## What this project is

`shabbos-goy` is a **Hebrew-speaking, speech-to-speech household agent** that
helps observant Jews on Shabbat and Yom Kippur without the user breaking the
day. It **never acts on a direct command**. It only infers intent from
indirect remarks ("הלוואי שהיה קר" / "I wish it was cold" → turn on the AC).
The build brief is issue #1 on `agentculture/shabbos-goy`.

It is an AgentCulture mesh agent, provisioned from `culture-agent-template`,
and a sibling of [`guildmaster`](https://github.com/agentculture/guildmaster)
(skills supplier), [`steward`](https://github.com/agentculture/steward)
(alignment), [`teken`](https://github.com/agentculture/teken) (the CLI
scaffolder), `lobes-cli` (the Hebrew speech stack it consumes) and
`sensibo-cli` (the AC control it composes).

## Prompt files by harness

This repo's root carries one prompt file per agent harness, each read by
exactly one of them — there is no shared base file for them to inherit from:

- **Claude Code** → [`CLAUDE.md`](CLAUDE.md) (the fullest write-up; read it
  first if you are new to the repo).
- **Pi / associate** → [`AGENTS.override.md`](AGENTS.override.md) for context,
  plus [`.pi/SYSTEM.md`](.pi/SYSTEM.md) for its system prompt.
- **colleague** → [`AGENTS.colleague.md`](AGENTS.colleague.md).
- **Qwen Code** → this file.

## Identity

Declared in `culture.yaml`:

```yaml
agents:
- suffix: shabbos-goy
  backend: claude
```

`backend: claude` fixes the *mesh resident* prompt file to `CLAUDE.md` — the
mesh runtime reads that file, not this one. A Qwen Code session working in a
clone of this repo is a separate, local tool session; it reads `QWEN.md`
regardless of what `culture.yaml` declares, and running Qwen Code here neither
requires nor changes that declaration. The declaration and the resident prompt
together satisfy the two invariants `steward doctor` verifies:
**prompt-file-present** and **backend-consistency** (`claude` ↔ `CLAUDE.md`).

## Design (planned; nothing below is built yet)

`CLAUDE.md` holds the full design. In short:

- **Core invariant, enforced by a tested classifier, not a prompt:**
  imperatives, requests and rebukes are never acted on, and never queued for
  later (including across a restart). There is no wake word and no
  confirmation question. When unsure, it does nothing. The headline metric is
  the false-positive rate on commands, measured on ASR-transcribed Hebrew.
- **Pipeline:** microphone → lobes Hebrew realtime session in **ears-only**
  mode (it never sends `response.create`) → transcript joiner → classifier →
  zmanim calendar gate → whitelisted **tool call** → optional neutral spoken
  remark via batch TTS.
- **Tool calling / climate:** actions are declared as tools (flat
  `name`/`description`/`parameters` shape). The first backend is `sensibo-cli`
  (`sensibo set <pod> --mode cool --target 24 [--apply] --json`). Every call,
  from a rule or from a model, passes classifier → gate → whitelist → argument
  validation in this repo's code. The whitelist is config, not code. Sensibo is
  cloud-only.
- **Deployment:** a Docker Compose service with `restart: unless-stopped`,
  `/dev/snd` passthrough (ALSA card chosen by name), secrets from a gitignored
  env file, and private config mounted read-only from `$XDG_CONFIG_HOME/shabbos-goy`.
  Startup is stateless: it recomputes the mode from the clock and zmanim, and
  if unsure it fails toward acting on nothing.
- **Halacha is flagged, not decided.** No claim of rabbinic approval.

## The CLI

The CLI is cited (cite-don't-import) from teken's `python-cli` reference
(`teken cli cite`), so the runtime package has **no third-party dependencies**;
`teken` (a.k.a. `afi-cli`) is a dev dependency only. Agent-first verbs:

- `shabbos-goy whoami` — identity from `culture.yaml`.
- `shabbos-goy learn` — structured self-teaching prompt.
- `shabbos-goy explain <path>` — markdown docs for any noun/verb.
- `shabbos-goy overview` — descriptive snapshot of the agent.
- `shabbos-goy doctor` — check the agent-identity invariants.
- `shabbos-goy cli overview` — describe the CLI surface itself.
- *(planned)* `classify "<text>"`, `zmanim --location …`, `actions`,
  `listen`. Any actuating verb is dry-run by default, and `--apply` actuates.

Conventions: every command supports `--json`; results go to stdout, errors and
diagnostics to stderr (never mixed); exit codes are `0` success, `1` user
error, `2` environment error, `3+` reserved. The agent-first rubric is
enforced in CI by `teken cli doctor . --strict`.

## Skills

`.claude/skills/` vendors the **canonical guildmaster skill kit**
(cite-don't-import). Provenance and the re-sync procedure live in
`docs/skill-sources.md`. Do not reformat or edit vendored scripts — re-sync
from guildmaster instead.

## Conventions

- **Every PR bumps the version** — even docs/config/CI. Use the
  `version-bump` skill; the `version-check` CI job blocks merge otherwise.
- **Tests**: `uv run pytest -n auto`. **Lint**: black, isort, flake8 (line
  length 100), bandit, markdownlint.
- **Deploy**: pushing to `main` publishes to PyPI via Trusted Publishing
  (`.github/workflows/publish.yml`); PRs do a TestPyPI dry-run.

## Layout

```text
shabbos_goy/   agent-first CLI (cited from teken's python-cli reference)
  cli/                    parser, error/output contract, _commands/ (verbs)
  explain/                markdown catalog for `explain`
tests/                    pytest smoke + introspection tests
.claude/skills/           vendored guildmaster skill kit (cite-don't-import)
docs/skill-sources.md     skill provenance ledger
culture.yaml              mesh identity (suffix + backend)
.github/workflows/        tests + deploy (PyPI Trusted Publishing)
```

This file describes the repository **as it exists on disk today**. When you
edit, keep claims grounded in checked-in reality; if a section drifts ahead of
reality, mark it `(planned)` or move it under a `## Roadmap` heading. For the
full set of workflow conventions (worktree layout, memory discipline,
`ask-colleague` usage), see [`CLAUDE.md`](CLAUDE.md) — those conventions apply
to work in this repo regardless of which harness is doing it.

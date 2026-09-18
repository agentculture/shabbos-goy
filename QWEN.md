# QWEN.md

This file provides guidance to Qwen Code when working with code in this
repository. Qwen Code's context loader reads exactly `QWEN.md` and `AGENTS.md`
in a directory; this repo deliberately ships only `QWEN.md` — there is no
`AGENTS.md` here (each harness gets its own file; see "Prompt files by
harness" below), so this file is the sole source of project guidance for a
Qwen Code session.

## What this project is

`shabbos-goy` is a **Hebrew-speaking household agent** that listens ambiently
in a room and switches an air conditioner's power. In **strict mode** —
Shabbat, Yom Kippur and Yom Tov, computed from zmanim for a configured
location — it acts only on intent inferred from indirect remarks ("הלוואי
שהיה קר" / "I wish it was cold" → AC on) and **never on a spoken command**. On
a weekday it obeys spoken commands too. The build brief is issue #1 on
`agentculture/shabbos-goy`.

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

## Design (built; not yet verified on hardware)

`CLAUDE.md` holds the full design. In short:

- **Core invariant, about speech:** in strict mode imperatives, requests and
  rebukes are never acted on, and never queued for later (including across a
  restart). There is no wake word and no confirmation question. When unsure,
  it does nothing. The refusals are a tested contract in this repo's code; how
  reliably speech gets the right label is measured by the golden set
  (`tests/golden/`, 275 rows), whose headline number is the false-positive
  rate on commands, measured on ASR-transcribed Hebrew. Zero is
  release-blocking, and that live run has not happened yet.
- **The CLI and the dashboard are operator UIs** and sit outside the
  invariant: they work in every mode, including strict, and may force strict
  mode on or switch it off inside a zmanim window. Overrides live in memory
  only, so the zmanim-computed mode returns after any restart.
- **Pipeline:** PipeWire capture → lobes Hebrew realtime session in
  **ears-only** mode (it never sends `response.create`) → transcript joiner →
  a local model that labels the utterance (the lobes `senses` role, Gemma) →
  confidence floor → the mode x class gate → intent to tool → whitelist →
  argument validation → rate limits → strict-mode delay → the adapter →
  optional neutral spoken remark via batch TTS, on weekdays only.
- **The model labels; this repo's code decides.** The label is untrusted
  input, and every failure (down, slow, malformed) means do nothing.
- **Tool calling / climate:** the only AC action is **power on/off** —
  `sensibo set <pod> --power on|off [--apply] --json`, called as a subprocess.
  The adapter cannot build `--mode`, `--target`, `--fan` or `--swing` at all.
  Plus the agent's own volume. The whitelist is config, not code. Sensibo is
  cloud-only.
- **Zmanim** are computed in pure standard library (NOAA sun maths plus
  Hebrew-calendar arithmetic); no dependency was added. Yom Tov is strict by
  default, Israel/diaspora is config, and an untrusted clock fails toward
  strict mode.
- **Deployment:** a Compose service (see `docker-compose.yml`) with
  `restart: unless-stopped`, the **host PipeWire session** for audio
  (`pw-record` / `pw-play` / `wpctl`, device chosen by name), secrets from a
  gitignored env file, and private config mounted read-only from
  `$XDG_CONFIG_HOME/shabbos-goy`. Startup is stateless: it recomputes the mode
  from the clock and zmanim, and if unsure it fails toward acting on nothing.
  The on-box drills are not yet done.
- **Halacha is flagged, not decided.** No claim of rabbinic approval; the open
  questions live in `docs/halacha-open-questions.md`.

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

Domain verbs:

- `classify "<text>"` — class, intent, confidence and the gate's verdict.
  Never acts.
- `zmanim` — the current mode, the window kinds, the next strict window.
- `actions` — the whitelist in effect and the intent-to-tool map.
- `preflight` — every precondition for strict mode, read-only.
- `ac status` / `ac power`, `volume get` / `volume set`, `mode show` /
  `mode set` — these proxy to a running listener's loopback control endpoint,
  so the CLI can never bypass its whitelist, rate limits or delay.
- `listen` — the ambient loop, and what the container runs; `--script`
  replays a JSONL events file or a WAV with no hardware.

Any actuating verb is dry-run by default, and `--apply` actuates.

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
shabbos_goy/
  cli/                    parser, error/output contract, _commands/ (verbs)
  explain/                markdown catalog for `explain`
  decider/                the model-backed labeller (prompt, Gemma client, replay, context)
  classifier/             the retired rule cascade — test oracle only, never imported at runtime
  lobes/                  ears-only realtime client (ws wire, env config, session)
  zmanim/                 stdlib sun maths, Hebrew calendar, strict windows
  actuators/ audio/       sensibo-cli and PipeWire adapters
  runtime/ web/           the listener's threads, and the dashboard
  pipeline.py policy.py   the gates, and the mode x class table
tests/                    pytest (fixtures-only); tests/golden/ is the measured evidence
.claude/skills/           vendored guildmaster skill kit (cite-don't-import)
docs/                     specs/ + plans/ (historical), halacha-open-questions.md,
                          skill-sources.md (skill provenance + cited source files)
culture.yaml              mesh identity (suffix + backend)
.github/workflows/        tests + deploy (PyPI Trusted Publishing)
```

This file describes the repository **as it exists on disk today**. When you
edit, keep claims grounded in checked-in reality; if a section drifts ahead of
reality, mark it `(planned)` or move it under a `## Roadmap` heading. For the
full set of workflow conventions (worktree layout, memory discipline,
`ask-colleague` usage), see [`CLAUDE.md`](CLAUDE.md) — those conventions apply
to work in this repo regardless of which harness is doing it.

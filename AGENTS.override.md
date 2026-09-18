# AGENTS.override.md

This file is the **context layer** for the Pi harness (the `pi` CLI, and the
`associate` non-coding harness modelled on it) when it runs inside this repo.
Pi's CONTEXT loader concatenates `AGENTS.md` or `CLAUDE.md` from its user-level
config directory (see Pi's own docs), each parent directory, and the working
directory — but an `AGENTS.override.md`
present in a directory replaces that directory's `AGENTS.md`/`CLAUDE.md` entry
outright rather than adding to it. That is why this repo ships this file
instead of an `AGENTS.md`: Pi must **not** inherit `CLAUDE.md` (the Claude Code
guidance file) — the two harnesses read the same repository very differently,
and `CLAUDE.md` assumes a coding session with full repo-write authority that
Pi's non-coding lane does not have.

The identity and behavioral bounds for that lane — who Pi is here, what it may
and may not do — live one layer up, in Pi's **system prompt** file,
[`.pi/SYSTEM.md`](.pi/SYSTEM.md). That file replaces Pi's default
coding-assistant system prompt entirely. This file is project *context* only:
what the repo is and how it is laid out, not who is reading it.

## What this project is

`shabbos-goy` is a **Hebrew-speaking household agent** that listens ambiently
in a room and switches an air conditioner's power. In **strict mode** (Shabbat,
Yom Kippur and Yom Tov, computed from zmanim) it acts only on intent inferred
from indirect remarks ("הלוואי שהיה קר" / "I wish it was cold" → AC on) and
**never on a spoken command**. On a weekday it obeys spoken commands too. The
build brief is issue #1 on `agentculture/shabbos-goy`; the design of record is
under `docs/specs/` and `docs/plans/`, which are historical and not edited.

**Status: built, not yet verified on hardware.** The domain code is on disk —
the ears-only lobes client, the transcript joiner, the model-backed decider,
the decision pipeline, the stdlib zmanim mode resolver, the Sensibo and
PipeWire adapters, the rate limits and delay, the domain CLI verbs
(`classify`, `zmanim`, `actions`, `preflight`, `ac`, `volume`, `mode`,
`listen`), the Tailscale-only dashboard and the golden set — with tests that
need no microphone, no lobes server and no Sensibo account. What has **not**
happened: the golden set has not been run live against the real model and
speech stack, and no on-box drill (PipeWire in a container, a live `--apply`,
a reconnect, a reboot) has been done. When summarizing, keep "built" and
"verified" apart.

Facts worth getting right when you answer questions about it:

- **The core invariant is about speech.** In strict mode imperatives, requests
  phrased as questions, and rebukes ("why isn't the AC on") are **never**
  acted on and never queued for later. There is no wake word and no
  confirmation question. When unsure, it does nothing.
- **The CLI and the dashboard are operator UIs**, outside that invariant: they
  work in every mode and may force strict mode on or switch it off inside a
  zmanim window. Overrides are memory-only and do not survive a restart.
- **A local language model does the labelling** (the lobes `senses` role,
  Gemma, on the same box). Its answer is untrusted input; this repo's code
  still enforces the mode gate, the whitelist, argument validation, a
  confidence floor, rate limits and a delay. If the model is down or
  malformed, the agent does nothing.
- **Weekday mode obeys anyone in earshot**; the narrow whitelist (AC power
  on/off, the agent's own volume) is the only control.
- **Halacha is flagged, not decided.** The project claims no rabbinic
  approval (*hechsher*). Open questions are recorded in
  `docs/halacha-open-questions.md`, not answered.
- **Sensibo is cloud-only**, so AC control needs internet access even though
  speech processing is local. Transcript text does reach the local model; it
  never reaches a cloud service, a disk or a log line.

It is a sibling to
[`guildmaster`](https://github.com/agentculture/guildmaster) (the skills
supplier), [`steward`](https://github.com/agentculture/steward) (alignment),
[`teken`](https://github.com/agentculture/teken) (the CLI scaffolder this
package is cited from), `lobes-cli` (the speech stack) and `sensibo-cli` (AC
control).

## Four harnesses, four files, no shared base

This repo's root carries one prompt file per harness, each read by exactly
one of them — there is deliberately no shared `AGENTS.md` base for them to
cascade from:

- **Claude Code** reads [`CLAUDE.md`](CLAUDE.md).
- **Pi / associate** reads this file (`AGENTS.override.md`) for context, plus
  [`.pi/SYSTEM.md`](.pi/SYSTEM.md) for its system prompt.
- **colleague** reads [`AGENTS.colleague.md`](AGENTS.colleague.md) (the start
  of colleague's own cascade — see that file).
- **Qwen Code** reads [`QWEN.md`](QWEN.md).

If you are reading this as a human, `CLAUDE.md` is the fullest write-up of the
repo's conventions and is the one to read first; the other three exist to keep
each non-Claude harness from silently inheriting Claude-specific instructions
it cannot act on the same way.

## Identity

Declared in `culture.yaml`:

```yaml
agents:
- suffix: shabbos-goy
  backend: claude
```

This template's *mesh* resident runs on `backend: claude`, so `CLAUDE.md` is
the live resident prompt. A Pi session working in a clone of this repo is a
**local tool session**, not the mesh resident — it reads this file and
`.pi/SYSTEM.md` regardless of what `culture.yaml` declares, and running `pi`
here neither requires nor changes that declaration.

(A clone that wants `associate` as its *mesh* resident declares
`backend: colleague` with `model: associate` — see `docs/skill-sources.md`.
That is a per-clone choice; this template does not ship it.)

## Layout (what you can read/find/summarize here)

```text
shabbos_goy/
  cli/                    parser, error/output contract, _commands/ (verbs)
  explain/                markdown catalog for `explain`
  decider/                the model-backed labeller (prompt, Gemma client, replay, context)
  classifier/             the retired rule cascade — test oracle only
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

## Conventions worth knowing before you answer a question about this repo

- The vendored skills under `.claude/skills/` are cited **verbatim** from
  guildmaster — never propose editing their scripts; the fix belongs upstream
  (`docs/skill-sources.md` has the re-sync procedure).
- Secrets (the Sensibo API key, the lobes gateway key) and private config
  (location, device ids) never belong in tracked files. Flag any you find.
- Every PR bumps the version (`version-bump` skill); CI's `version-check` job
  blocks merge otherwise.
- This file describes the repo **as it exists on disk today**. If you are
  asked to update it, keep claims grounded in checked-in reality.

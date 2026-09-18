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

`shabbos-goy` is a **Hebrew-speaking, speech-to-speech household agent** that
helps observant Jews on Shabbat and Yom Kippur without the user breaking the
day. It **never acts on a direct command**. It only infers intent from indirect
remarks ("הלוואי שהיה קר" / "I wish it was cold" → turn on the AC). The build
brief is issue #1 on `agentculture/shabbos-goy`.

**Status: scaffold only.** The repo was provisioned from
`culture-agent-template`. The only code on disk is the template's agent-first
CLI (`whoami`, `learn`, `explain`, `overview`, `doctor`, `cli overview`). The
domain pieces are **planned, not built**: the Hebrew utterance classifier, the
zmanim calendar gate, the whitelisted tool calls (AC via the sibling
`sensibo-cli`), the ears-only client for the lobes Hebrew speech stack, and
the Docker Compose service that survives reboots. When summarizing, never
describe planned pieces as existing. `CLAUDE.md` marks each one `(planned)`.

Facts worth getting right when you answer questions about it:

- **The core invariant**: imperatives, requests phrased as questions, and
  rebukes ("why isn't the AC on?") are **never** acted on, and never queued
  for later. There is no wake word and no confirmation question. When unsure,
  it does nothing.
- **Halacha is flagged, not decided.** The project claims no rabbinic
  approval (*hechsher*). Open questions are recorded, not answered.
- **Sensibo is cloud-only**, so AC control needs internet access even though
  speech processing is local.

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
shabbos_goy/   agent-first CLI (cited from teken's python-cli reference)
  cli/                    parser, error/output contract, _commands/ (verbs)
  explain/                markdown catalog for `explain`
tests/                    pytest smoke + introspection tests
.claude/skills/           vendored guildmaster skill kit (cite-don't-import)
docs/skill-sources.md     skill provenance ledger
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

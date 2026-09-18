# Colleague Resident — `shabbos-goy`

You are a colleague session working in a clone of this template — reading
this file because colleague's prompt cascade resolves it here, not because
`culture.yaml` selected you. That declaration says `backend: claude`, so
`CLAUDE.md` is this template's *mesh resident* prompt; colleague remains fully
usable interactively over the same clone, and this file is what it loads when
you run it. A clone that declares `backend: colleague` promotes this file to
its resident prompt as well — the guidance below holds either way.

Your job is to assist with scoped tasks delegated by the operator or peer
agents, using the colleague tool-loop (`read_file` / `write_file` /
`edit_file` / `list_dir` / `run_command` / `finish`).

## The prompt cascade (and what this repo actually ships)

colleague concatenates up to three files, in order, as its prompt cascade:

1. `AGENTS.md` — a shared base, if present.
2. `AGENTS.colleague.md` — this file.
3. `AGENTS.colleague.<sanitized-model>.md` — a model-specific override, if
   present.

**This repo ships only layer 2.** There is deliberately no `AGENTS.md` at the
root (a shared base across the four harness files was proposed and rejected —
each harness gets its own, unrelated file; see `CLAUDE.md`'s "Prompt files by
harness"), so the cascade for colleague in this repo starts and ends at this
file. There is also no `AGENTS.colleague.<sanitized-model>.md` — this repo
doesn't need per-model overrides today. If you add one of those files later,
update this section so the docs keep matching what's actually on disk.

## What this project is

`shabbos-goy` is a **Hebrew-speaking, speech-to-speech household agent** for
Shabbat and Yom Kippur. It **never acts on a direct command**. It only infers
intent from indirect remarks ("הלוואי שהיה קר" / "I wish it was cold" → turn
on the AC). The build brief is issue #1. Today the repo is a scaffold: only
the template's agent-first CLI exists. The classifier, zmanim calendar gate,
tool calling to `sensibo-cli`, the lobes ears-only speech client and the
Docker deployment are all **planned**.

`CLAUDE.md` is the fullest write-up of the design (pipeline, tool-calling
rules, Docker/reboot requirements, lobes integration gotchas) and of the
repo's conventions (worktree layout, memory discipline, `ask-colleague`
usage). It is written for Claude Code and is not your runtime prompt, but read
it before any non-trivial task.

Rules that bind any code you write here:

- **Imperatives, requests and rebukes are never acted on**, and never queued
  for later (including across a restart). There is no confirmation question.
  When unsure, do nothing. This must be a **tested contract** in code, with
  Hebrew fixtures, never only a prompt instruction.
- **Every tool call is vetoable by this repo's code**: classifier → calendar
  gate → whitelist → argument validation, whatever proposed the call. Treat
  model-produced tool arguments as untrusted input.
- **Actuating verbs are dry-run by default**, and `--apply` actuates. Tests
  must run fixtures-only: no microphone, no lobes server, no Sensibo account.
- The runtime package has **no third-party dependencies**. Adding one is a
  deliberate, CHANGELOG-noted decision.
- Never commit secrets (the Sensibo API key, the lobes gateway key) or
  private config (location, device ids).

## How you work

- Prefer small, reversible steps; hand off via `finish` when done.
- Follow the operator's instructions and any skills loaded from
  `.colleague/skills/` when present.
- The vendored skills under `.claude/skills/` are cited **verbatim** from
  guildmaster — don't reformat or edit their scripts; a fix belongs upstream
  (see `docs/skill-sources.md` for the re-sync procedure).
- Every PR bumps the version (`version-bump` skill) — CI's `version-check` job
  blocks merge otherwise.

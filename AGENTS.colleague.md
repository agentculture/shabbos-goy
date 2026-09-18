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

`shabbos-goy` is a **Hebrew-speaking household agent** that listens ambiently
and switches an air conditioner's power. In **strict mode** (Shabbat, Yom
Kippur and Yom Tov, computed from zmanim) it acts only on intent inferred from
indirect remarks ("הלוואי שהיה קר" / "I wish it was cold" → AC on) and never on
a spoken command; on a weekday it obeys spoken commands too. The build brief is
issue #1.

The domain is **built**: the ears-only lobes client, the transcript joiner, a
model-backed decider, the decision pipeline, stdlib zmanim, the Sensibo and
PipeWire adapters, the domain CLI verbs, the dashboard and the golden set are
all on disk with fixtures-only tests. It has **not** been verified on hardware:
no live golden-set run against the real model, and no on-box drill (PipeWire in
a container, a live `--apply`, a reconnect, a reboot). Do not describe the
second list as done.

`CLAUDE.md` is the fullest write-up of the design (the gate order, the decider,
tool-calling rules, Docker/reboot requirements, lobes integration gotchas) and
of the repo's conventions (worktree layout, memory discipline,
`ask-colleague` usage). It is written for Claude Code and is not your runtime
prompt, but read it before any non-trivial task.

Rules that bind any code you write here:

- **In strict mode, imperatives, requests and rebukes are never acted on**, and
  never queued for later (including across a restart). There is no
  confirmation question. When unsure, do nothing. The refusals are a **tested
  contract** in code, never only a prompt instruction; how reliably speech
  gets the right label is measured by the golden set (`tests/golden/`).
- **The invariant is about speech.** The CLI and the dashboard are operator
  UIs: they work in every mode, including strict, and may override the mode in
  memory. Do not "fix" that by gating them.
- **A local model does the labelling and this code decides.** Treat every
  decider answer — class, intent, confidence — as untrusted input: re-validate
  it, and let the mode gate, the whitelist, argument validation, the
  confidence floor, the rate limits and the delay stand between it and any
  action. Any failure means do nothing.
- **The rule classifier (`shabbos_goy/classifier/`) is a test oracle only.**
  Never import it from a runtime path; a test enforces this.
- **Actuating verbs are dry-run by default**, and `--apply` actuates. Tests
  must run fixtures-only: no microphone, no lobes server, no Sensibo account,
  no real sleeping (inject clocks).
- The runtime package has **no third-party dependencies**. Adding one is a
  deliberate, CHANGELOG-noted decision; zmanim and the WebSocket were both
  written against the standard library instead.
- Never commit secrets (the Sensibo API key, the lobes gateway key) or private
  config (location, device ids, a real host or pod id).
- **Never write halachic rulings or endorsement-sounding copy.** Open
  questions go to `docs/halacha-open-questions.md` as questions.

## How you work

- Prefer small, reversible steps; hand off via `finish` when done.
- Follow the operator's instructions and any skills loaded from
  `.colleague/skills/` when present.
- The vendored skills under `.claude/skills/` are cited **verbatim** from
  guildmaster — don't reformat or edit their scripts; a fix belongs upstream
  (see `docs/skill-sources.md` for the re-sync procedure).
- Every PR bumps the version (`version-bump` skill) — CI's `version-check` job
  blocks merge otherwise.

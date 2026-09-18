# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this agent is

`shabbos-goy` is a **Hebrew-speaking, speech-to-speech household agent** that
helps observant Jews on **Shabbat and Yom Kippur** without the user breaking
the day. It **never takes a direct command**. It only acts on intent it
*infers* from indirect speech: a remark about a state, a wish, or a discomfort
("הלוואי שהיה קר" / "I wish it was cold" → turn on the AC).

The build brief is [issue #1](https://github.com/agentculture/shabbos-goy/issues/1)
(the guildmaster brief, plus the lobes comment on the speech stack). Read it
before designing anything. This file condenses the parts that bind the code.

**Status: scaffold only.** The repo is `culture-agent-template` renamed to
`shabbos-goy`. The only code on disk is the template's agent-first CLI
(`whoami`, `learn`, `explain`, `overview`, `doctor`, `cli overview`). None of
the domain has been built yet: no classifier, calendar gate, actuators, audio
loop or Docker packaging. Every section below marked **(planned)** describes
the target, not the current code. Keep that distinction when you edit. Once
something ships, drop its `(planned)` marker.

## The core invariant: no direct commands, ever

This is the product. It must be a **tested contract in this repo's code**. It
must never be only an instruction in someone's system prompt.

| Utterance class | Example | Behaviour |
|---|---|---|
| state remark | "חם פה" / "It's so hot in here" | candidate intent → cool the room |
| wish | "הלוואי שהיה קר" / "I wish it was cold" | candidate intent → AC on |
| discomfort | "קשה לקרוא בחושך" / "Hard to read in the dark" | candidate intent → light |
| **imperative** | "תדליק את המזגן" / "Turn on the AC" | **do nothing** |
| **request as a question** | "אתה יכול להדליק את האור?" | **do nothing** |
| **rebuke (implied command)** | "למה המזגן לא דלוק?" / "Why isn't the AC on?" | **do nothing** |
| unrelated | anything else | do nothing |

Consequences that every change must respect:

1. **No wake word.** Saying a wake word addresses the device with an
   instruction. Shabbat mode is passive, ambient listening.
2. **No confirmation dialogs.** "Should I turn on the AC?" → "yes" turns the
   exchange into a command. When unsure, **do nothing**. Never ask.
3. **Imperatives are dropped, not queued.** A refused command must never be
   executed later. That includes after a crash or reboot: persist no pending
   actions or transcript buffers across restarts.
4. **The classifier is the product.** The headline metric is its
   **false-positive rate on imperatives/requests/rebukes**, meaning it acted on
   a command. A missed hint is cheap. A false positive breaks Shabbat for the
   user. Measure it on **ASR-transcribed audio**, not only on typed text,
   because ASR errors are classifier inputs.
5. **Spoken output is neutral and never invites a reply.** "המזגן פועל" ("the
   AC is on") is fine. A question is not.
6. **A half-sentence never acts.** The ASR can split one sentence into two
   transcripts (see [lobes integration](#speech-stack-lobes-integration-planned)).

## Halachic scope: flag it, don't decide it

Several premises are open halachic questions. The agent must not answer them:

- Does an AI device count as a non-Jew, a Shabbat timer, *grama*, or none of
  these?
- Is speaking near an always-listening device a problem?
- Which hints are permitted (need, illness, *mitzvah*), and is the whitelist
  narrower than "anything the user would like"?
- Yom Kippur differences, and Yom Tov.

Rules for code and docs:

- These questions belong in `docs/halacha-open-questions.md` (planned).
- The README says plainly that there is **no rabbinic approval (*hechsher*)**
  and that users should ask their own rav. Never add endorsement-sounding copy.
- The **action whitelist is data (config), not code**, so a community can
  narrow it to match its posek.

## Architecture (planned)

```text
reSpeaker mic ──PCM16──▶ lobes /v1/realtime (ears-only: never send response.create)
                              │  transcription.completed {text}
                              ▼
                     transcript joiner   (re-join sentences split by a pause)
                              ▼
                     classifier          (imperative/request/rebuke/remark/wish/discomfort/unrelated)
                              ▼
                     calendar gate       (zmanim: candle lighting → tzeit, for a configured location)
                              ▼
                     intent → tool call  (resolve which whitelisted tool, with what arguments)
                              ▼
                     whitelist + tool    (sensibo-cli first; dry-run unless --apply)
                              ▼
                     optional neutral remark via batch TTS (POST /v1/audio/speech)
```

The agent **decides**. Mesh siblings **do**. Compose them and do not
reimplement them: `sensibo-cli` (AC), `microphone-cli` (mic array, direction
of arrival), `harmonics-cli` / `media-cli` (audio I/O). Check each sibling's
current interface before depending on it.

MVP: **one actuator (AC via sensibo-cli) + the classifier + the calendar gate,
end to end**. It needs a **fixtures-only test path** that runs with no
microphone, no lobes server and no Sensibo account.

### Tool calling and climate control (planned)

Actuation is modelled as **tools**: named functions with a JSON-schema
parameter block. Use the flat OpenAI-Realtime tool shape (`name` /
`description` / `parameters`) so the same declarations can be handed to a
model when needed. The design rule is that **no tool call executes unless this
repo's code approves it**, whoever proposed it:

- Every tool call goes through **classifier → calendar gate → whitelist →
  argument validation → dry-run/`--apply`** in our code. This holds whether
  the call comes from a deterministic intent table or from an LLM's
  `function_call`. Treat model-produced `arguments` as **untrusted input**:
  they came from a language model that heard a human through a speech
  recognizer.
- **Shabbat/Yom Kippur mode uses lobes ears-only.** Do not declare tools to the
  lobes session and do not arm it (`response.create`). In conversation mode the
  model decides what counts as a hint, the invariant ends up in a system
  prompt, and the box talks back. The brief rules out all three. Conversation
  mode with tools is only a candidate for a weekday assistant. If it is used,
  the transcript event arrives **before** the tool call, so the classifier can
  still veto every call client-side.
- **Whether intent → tool arguments is resolved by a rule table or by a
  separate LLM tool-calling step is not decided yet.** Either way, the
  classifier's verdict gates it and the whitelist bounds it.
- The **whitelist is config**: which tools, which devices (Sensibo pod ids),
  and what argument ranges (e.g. mode `cool`, target 22–26 °C).

**sensibo-cli** (`../sensibo-cli`; console command `sensibo`, import package
`sensibo`, dist `sensibo-cli`) is the first tool backend:

- `sensibo set <pod> --mode cool --target 24 [--apply] --json`. Every write
  verb there is **dry-run by default**. Keep that default visible end to end:
  our `--apply` is what maps to theirs.
- It can also be reached as a Python library (`from sensibo import Client`)
  or over MCP (`sensibo mcp serve`, `sensibo-cli[mcp]` extra). Calling the
  CLI as a subprocess keeps this package's **zero runtime dependencies**. Do
  not pick another route without a reason.
- **Sensibo is cloud-only.** There is no LAN protocol, so actuation needs
  internet access to `home.sensibo.com`. The API key comes from
  `SENSIBO_API_KEY` (or sensibo-cli's own per-user env file; see its docs).
  Pass it to the container as a secret. Never commit it.
- `sensibo read <pod>` / `query latest` give current temperature and humidity,
  useful for deciding whether "חם פה" warrants action at all.

### Calendar-aware modes (planned)

- Shabbat/Yom Kippur mode switches on and off **automatically from zmanim**
  (candle lighting → *tzeit hakochavim*) for a configured location. Candidate
  sources are Hebcal data or the `hdate` / `zmanim` libraries. Verify their
  licences before adding one.
- Everything is **set up before** Shabbat. Nothing may need a toggle during
  the day, because a toggle is a command.
- Weekday behaviour (ordinary Hebrew assistant, or refuse) is an **open
  decision**. Make it deliberately and document it.

### Speech stack: lobes integration (planned)

lobes serves a local Hebrew duplex voice session on the DGX Spark (ivrit.ai
Whisper turbo → Gemma → BlueTTS). This agent is its first consumer. Do not
build a second speech stack. lobes runs no tools and knows nothing about
Shabbat. The classifier, gate, whitelist and actuators all live here.

- **Contract:** `docs/contracts/realtime-tool-calling.md` in
  `agentculture/lobes-cli` (branch `spec/hebrew-realtime`, checked out at
  `../lobes-cli`). The worked client example is
  `scripts/realtime-he-accept.py` there: a stdlib-only WebSocket client with
  `arecord`/`aplay` capture. Copy its structure.
- **Connect:** `ws://<lobes-host>:8001/v1/realtime?language=he&input_sample_rate=16000`
  with `Authorization: Bearer <gateway key>`. Connect directly to the box; the
  WebSocket is not proxied across the mesh. Ask the operator for the host and
  key and keep both in env/config, never in tracked files.
- **Stream audio for the whole session**, silence included, on its own
  thread. The server's VAD ends turns.
- **Empty `text` transcripts are dropped noise.** The sidecar filters
  low-confidence hallucinations. Ignore them.
- **Pause splitting:** the server ends a turn after 500 ms of silence, and
  ears-only mode does not re-join paused sentences. Buffer transcripts whose
  `speech_stopped.at_ms` → next `speech_started.at_ms` gap is short, and
  classify the joined text.
- **Fixtures path:** `POST /v1/audio/transcriptions` (multipart,
  `language=he`) is the same Whisper in batch form. Use it to benchmark the
  fixture corpus as the classifier will really see it.
- **Echo:** speaker and mic must be the same device (the reSpeaker XVF3800's
  own 3.5 mm out) or its AEC has no reference.
- **Keep device names in Hebrew** in anything spoken. English words inside
  Hebrew are weak both ways.
- **Before household use:** the Spark's `TTS_DEBUG_TEXT` switch logs spoken
  text and is on for development. It must be turned off.
- **Voice licence:** BlueTTS weights declare no licence yet. Do not
  redistribute them or make them this project's default. The licensed fallback
  is Chatterbox Multilingual.

### Privacy

Local processing by default. Audio stays on the device and is never written
to disk. Logs record **the classified intent and the action taken**. They do
not record audio, and they should not record transcript text. Docker keeps
container stdout, so this rule covers what the service prints too.

## Deployment: Docker, surviving reboots (planned)

The listener runs as a Docker Compose service on the host that owns the
microphone (the DGX Spark today). Jetson Orin in ears-only mode is plausible
but untested. After a power cut in the middle of Shabbat it must come back and
resume the right mode **with no human interaction**, because a person
restarting it on Shabbat is exactly what this project exists to avoid.

Requirements, following the workspace house style (`../climate-cli`,
`../lobes-cli/deployments`):

- `restart: unless-stopped` on every service, with the Docker daemon enabled
  at boot (`systemctl enable docker`).
- `json-file` logging with rotation (`max-size` / `max-file`).
- Audio passthrough: `devices: [/dev/snd]` and `group_add: [audio]`. Select
  the ALSA card **by name, not index**, because card indices can change across
  reboots.
- Secrets (`SENSIBO_API_KEY`, lobes gateway key) come from a gitignored env
  file. Private config (location for zmanim, whitelist, Sensibo pod ids) is
  bind-mounted read-only from `$XDG_CONFIG_HOME/shabbos-goy` on the host.
- Startup must be **stateless and self-healing**. Compute the current mode
  from the clock and zmanim on boot. Retry lobes and Sensibo connections with
  backoff instead of crash-looping. Persist no queued action (invariant #3).
- **Clock trust:** mode depends on wall-clock time. If time or location cannot
  be trusted (e.g. no NTP sync after boot), fail toward the stricter
  behaviour: listen-only and act on nothing it is unsure of. Never fail
  toward acting.
- The container runs `shabbos-goy listen`. Its health check should prove that
  transcripts are actually arriving, not just that the process is alive.

## Commands

```bash
uv sync                                                   # install (dev group included)
uv run pytest -n auto                                     # full suite, parallel
uv run pytest tests/test_cli.py::test_whoami_json -v      # a single test
uv run pytest -n auto --cov=shabbos_goy --cov-report=term # with coverage (CI gate: 60%)

uv run black --check shabbos_goy tests                    # lint, as CI runs it
uv run isort --check-only shabbos_goy tests
uv run flake8 shabbos_goy tests
uv run bandit -c pyproject.toml -r shabbos_goy
npm install -g markdownlint-cli2@0.21.0                   # not installed by uv sync; CI pins this version
markdownlint-cli2 "**/*.md" "#node_modules" "#.local" "#.claude/skills" "#.teken"

uv run teken cli doctor . --strict                        # agent-first rubric gate
uv run python scripts/harness-smoke.py --stage config     # all four harness configs valid
python3 scripts/scan-secrets.py                           # committed secrets / non-localhost JSON endpoints
```

## The CLI

Cited (cite-don't-import) from teken's `python-cli` reference, so the runtime
package has **no third-party dependencies**. Keep it that way where possible.
Adding a domain dependency (zmanim, a WebSocket library) is a deliberate
decision to note in the CHANGELOG.

Existing verbs: `whoami`, `learn`, `explain <path>`, `overview`, `doctor`,
`cli overview`. Verbs live in `shabbos_goy/cli/_commands/`. Give each new
verb an entry in `shabbos_goy/explain/catalog.py` (keyed by command-path
tuple). The rubric gate checks that `learn`, `explain`, `overview` and
`doctor` honour the agent-first contract, but it does not check per-verb
`explain` coverage, so nothing catches a missing entry. The catalog's root
entry and `learn` text still describe the template and need a domain rewrite.

Planned verbs from the brief:

| Verb | Purpose |
|---|---|
| `classify "<hebrew text>"` | class + inferred intent + whether it *would* act (dry; the primary test surface) |
| `zmanim --location …` | the current mode window |
| `actions` | the whitelist in effect |
| `listen` | the ambient loop (ASR → classify → gate → act); what the container runs |

Contract: every command supports `--json`. Results go to stdout and
diagnostics to stderr, never mixed. Exit codes: `0` ok, `1` user error, `2`
environment error, `3+` reserved. **Any actuating verb is dry-run by default.
`--apply` actuates.**

## Identity and harnesses

`culture.yaml` declares `suffix: shabbos-goy`, `backend: claude`, so this file
is the **mesh resident prompt**. `shabbos-goy doctor` and `steward doctor`
check **prompt-file-present** and **backend-consistency** (`claude` ↔
`CLAUDE.md`).

Four harnesses are live over the same clone, each reading exactly one root
file. There is deliberately **no shared `AGENTS.md`**:

| Harness | File(s) |
|---|---|
| Claude Code | `CLAUDE.md` |
| Pi / associate (read-only lane) | `AGENTS.override.md` (context) + `.pi/SYSTEM.md` (system prompt) |
| colleague | `AGENTS.colleague.md` |
| Qwen Code | `QWEN.md` |

Which binary you run is the *interactive* selection. `culture.yaml`'s
`backend` is the *mesh-resident* selection, and only that. See
`docs/harness-selection.md`. When the domain story changes, update the other
three files too, so they don't drift from this one. `.qwen/skills`,
`.colleague/skills` and `.pi/skills` are symlinks to `.claude/skills`.

## Conventions

- **Every PR bumps the version**, even docs/config/CI:
  `python3 .claude/skills/version-bump/scripts/bump.py patch|minor|major`
  (updates `pyproject.toml` and prepends to `CHANGELOG.md`). CI's
  `version-check` blocks merge otherwise.
- **PRs go through the `cicd` skill** (`devex pr` + SonarCloud gating).
  Posts are signed `- shabbos-goy (Claude)`; the `cicd` / `communicate`
  scripts append it.
- **Deploy (package):** push to `main` publishes to PyPI via Trusted
  Publishing (`.github/workflows/publish.yml`). PRs do a TestPyPI dry run.
- **Vendored skills** in `.claude/skills/` are cited verbatim from
  guildmaster/devague/colleague. Don't edit them; re-sync per
  `docs/skill-sources.md`. Tool prerequisites: `devex` and `agtag` on PATH,
  and optionally `colleague`.
- **Reach for `ask-colleague` reflexively.** Use `review` before presenting a
  non-trivial committed diff and `explore` for a fresh read of an unfamiliar
  area. Both are read-only. `write --apply` / `--pr` need the user's go-ahead.
  Treat its output as a second opinion to verify, not as authority.
- **Worktrees** you create by hand go in `../.worktrees.shabbos-goy/<name>/`
  with a work-scoped branch prefix (not `agent/*`, and never a shared
  `../worktrees/`). This overrides the vendored `assign-to-workforce`
  example. Remove them with `git worktree remove`. `ask-colleague`'s own
  `$TMPDIR` throwaways are exempt.
- **Memory: `/recall` before, `/remember` after.** The eidetic store for this
  repo is committed at `.eidetic/memory` (public, shared with mesh peers).
  `--visibility private` keeps a record in `$HOME` instead. Store what would
  otherwise be re-derived (decisions, gotchas), not what the repo already
  records.
- This file describes the repo **as it exists on disk**, plus explicitly
  `(planned)` sections from issue #1. Keep claims grounded.

## Layout

```text
shabbos_goy/          agent-first CLI (cited from teken): cli/ (parser, output/error contract,
                      _commands/ verbs), explain/ (catalog for `explain`)
tests/                pytest: CLI smoke/introspection, doctor, harness registries, scan-secrets
scripts/              harness-smoke.py (per-harness CI check), scan-secrets.py (CI gate)
docs/                 harness selection/automation contract, skill-sources.md (vendoring ledger)
.claude/skills/       vendored skill kit (cite-don't-import)
.eidetic/memory/      committed eidetic memory store
culture.yaml          mesh identity (suffix + backend)
```

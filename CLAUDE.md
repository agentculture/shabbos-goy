# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this agent is

`shabbos-goy` is a **Hebrew-speaking household agent** that listens ambiently
in a room and switches an air conditioner's power. On **Shabbat, Yom Kippur
and Yom Tov** ("strict mode") it acts only on intent it *infers* from indirect
speech — a remark about a state, a wish, a discomfort ("הלוואי שהיה קר" / "I
wish it was cold" → AC on) — and never on a spoken command. On a weekday it
obeys spoken commands too.

The build brief is [issue #1](https://github.com/agentculture/shabbos-goy/issues/1).
The design of record is `docs/specs/2026-09-18-lobes-driven-ac-power-agent.md`
and `docs/plans/2026-09-18-lobes-driven-ac-power-agent.md`. Those two files are
the **historical contract**: they describe the design as confirmed, including
choices that were later changed by approved deviations. Do not edit them. This
file describes the code as it is now.

**Status.** The domain is built: the ears-only lobes client, the transcript
joiner, the model-backed decider, the decision pipeline, the zmanim mode
resolver, the Sensibo and PipeWire adapters, the rate limits and delay, the
domain CLI verbs, the dashboard, the listener runtime and the golden set are
all on disk with tests that need no hardware. What is **not** verified: the
golden set has not been run live against the real model and speech stack, and
no on-box drill (PipeWire in a container, a live `--apply`, a reconnect, a
reboot) has been done. Keep that distinction when you edit, and never describe
an unverified claim as proven.

## The core invariant: no spoken command acts in strict mode

This is the product. The refusals are a tested contract in this repo's code,
and the *labelling* that feeds them is a measured property of a model plus a
prompt (see #4 below). Neither is ever only an instruction in a system prompt
someone can edit away.

| Utterance class | Example | Strict mode | Weekday mode |
|---|---|---|---|
| state remark | "חם פה" / "It's so hot in here" | candidate intent | candidate intent |
| wish | "הלוואי שהיה קר" / "I wish it was cold" | candidate intent | candidate intent |
| discomfort | "קשה לי לישון בחום הזה" | candidate intent | candidate intent |
| **imperative** | "תדליק את המזגן" / "Turn on the AC" | **do nothing** | acts |
| **request as a question** | "אתה יכול להדליק את המזגן" | **do nothing** | acts |
| **rebuke (implied command)** | "למה המזגן לא דלוק" | **do nothing** | acts |
| unrelated | anything else | do nothing | do nothing |

The one table that decides this is `shabbos_goy/policy.py`'s `may_act`. Never
re-derive it anywhere else.

Consequences that every change must respect:

1. **No wake word.** Saying a wake word addresses the device with an
   instruction. Strict mode is passive, ambient listening.
2. **No confirmation dialogs.** "Should I turn on the AC?" → "yes" turns the
   exchange into a command. When unsure, **do nothing**. Never ask.
3. **Refused commands are dropped, not queued.** A refused command must never
   be executed later. That includes after a crash or reboot: persist no
   pending actions, no transcript buffers and no mode override.
4. **The labelling prompt plus the golden set are the product's evidence.**
   The decision of *what kind of speech this was* is made by a local model
   (deviation d1), so "a command never acts" is no longer a deterministic
   property of a rule cascade. What the code guarantees is that anything
   **labelled** a command is refused. Whether commands get that label is
   measured by the golden set (`tests/golden/`, 295 rows, three entrances),
   run against the real model on the box. **Zero strict-mode false positives
   is release-blocking**; a missed hint is cheap. Measure on
   **ASR-transcribed audio**, not only typed text, because ASR errors are the
   real input. Re-run whenever the prompt version, the model or lobes changes.
5. **Spoken output is neutral, never invites a reply, and is weekday-only.**
   "המזגן פועל" ("the AC is on") on a weekday is fine. A question is not, ever
   — a test asserts no question mark exists anywhere in the package.
6. **A half-sentence never acts.** The ASR can split one sentence into two
   transcripts; `shabbos_goy/joiner.py` re-joins them and emits nothing but a
   complete, gap-closed utterance.

### The invariant is about speech, not about the operator

The **CLI and the dashboard are operator UIs**. They are always available,
work in every mode including strict, and may force strict mode on **or switch
it off inside a zmanim window**. A person pressing a button has not spoken a
command to a listening box, so `may_act` does not run on that path — the
whitelist, the argument validation, the rate limits and the dry-run default
still do. Overrides are memory-only: the zmanim-computed mode returns after
any restart.

This is an engineering decision about what the software permits, not a claim
about what a person may do on Shabbat. That question belongs in
`docs/halacha-open-questions.md`.

### Weekday mode obeys anyone in earshot

On a weekday, imperatives, requests and rebukes all act, from whoever is
speaking. The agent does not identify the speaker. **The whitelist is the only
control**, which is why it stays narrow (AC power on/off, the agent's own
volume) and why it is config, not code. The README says this plainly; keep it
saying it.

## Halachic scope: flag it, don't decide it

Several premises are open halachic questions. The agent must not answer them.
They live in `docs/halacha-open-questions.md`: what the device is (non-Jew,
timer, *grama*, none of these), speaking near a listening device, which hints
are permitted, powering the AC **off** from a cold hint, the deliberate delay,
using the dashboard or CLI on Shabbat, a language model making the judgement,
Yom Kippur, and Yom Tov.

Rules for code and docs:

- The README says plainly that there is **no rabbinic approval (*hechsher*)**
  and that users should ask their own rav. Never add endorsement-sounding
  copy, anywhere, including in commit messages and PR bodies.
- The **action whitelist is data (config), not code**, so a community can
  narrow it to match its posek. So are the zmanim rules and the delay.

## Architecture

```text
PipeWire capture ──PCM16──▶ lobes /v1/realtime (ears-only: never send response.create)
                              │  transcription.completed {text}
                              ▼
                     transcript joiner   (re-join sentences split by a pause)
                              ▼
                     decider             (lobes `senses` role: Gemma returns class/intent/confidence)
                              ▼
                     shape + confidence  (re-validated here; floor default 0.6)
                              ▼
                     policy.may_act      (mode x class; clock trust)
                              ▼
                     intent → tool       (a fixed table in pipeline.py)
                              ▼
                     whitelist + args    (config; power on|off only)
                              ▼
                     rate limits, already-in-state, strict-mode delay
                              ▼
                     adapter             (sensibo-cli / wpctl; dry-run unless --apply)
                              ▼
                     optional neutral remark via batch TTS — weekdays only
```

The agent **decides**. Mesh siblings **do**. Compose them and do not
reimplement them: `sensibo-cli` (AC) is the only actuator backend today.
`microphone-cli`, `harmonics-cli` and `media-cli` were examined and rejected
for this job (see the spec's non-goals); audio I/O is this repo's own thin
PipeWire adapter.

### The decider: the model labels, this code decides

Deviation d1 (approved mid-run) moved the decision to a **local language
model**. Each utterance, plus a trimmed in-memory rolling context (d3), goes
to the lobes `senses` role (Gemma) on the same box, which returns
`{class, intent, confidence}`.

- The answer is **untrusted input**: it came from a language model that heard
  a human through a speech recognizer. It is size-capped, parsed as exactly
  one JSON object and validated field by field; the pipeline re-validates the
  shape again even though every shipped decider validates its own output.
- **Ears-only stays ears-only.** No tool is declared to lobes and
  `response.create` is refused in code, not merely left uncalled.
- **Every failure is `NO_DECISION`**, which no mode acts on: a server that is
  down, slow, malformed, inventive or hostile all end the same way — do
  nothing, say nothing. There is no retry of a decision; a missed hint is
  cheap.
- The prompt lives in exactly one place, `shabbos_goy/decider/prompt.py`.
  Changing it changes the measurements, so bump `PROMPT_VERSION` with it (it
  travels in every `Decision.source`, e.g. `gemma:p1`) and re-run the golden
  set.
- **The rule classifier is out of the runtime** (deviation d2).
  `shabbos_goy/classifier/` survives only as a deterministic test oracle and
  as the seed corpus of the golden set. Only `shabbos_goy/decider/oracle.py`
  may import it, and a test walks the package with `ast` to keep it that way.
- The hardware consequence: the LLM has to be running. An ears-only
  deployment on a box with no local model is no longer possible.

### Tool calling and climate control

Actuation is modelled as **tools**: named functions with validated arguments.
The design rule is that **no tool call executes unless this repo's code
approves it**, whoever proposed it — a label from the model, a CLI verb or a
dashboard button all converge on the same gates in `shabbos_goy/pipeline.py`
and `shabbos_goy/web/server.py`.

The intent-to-tool table (`shabbos-goy actions` prints it):

| Intent | Tool | Effect |
|---|---|---|
| `cool` | sensibo | power **on** |
| `warm` | sensibo | power **off** |
| `louder` / `quieter` | volume | one step, clamped by config |
| `status` | speech | a spoken answer, **weekdays only** |

If the device is already in the requested state: do nothing and say nothing.

**sensibo-cli** (`../sensibo-cli`; console command `sensibo`, import package
`sensibo`, dist `sensibo-cli`) is the only tool backend:

- `shabbos_goy/actuators/sensibo.py` is the one place that talks to it, and
  only to its command line. Every argv it can build comes from a closed set:
  `--power on|off`, `--apply`, `--json`. `--mode`, `--target`, `--fan` and
  `--swing` are unreachable by construction, and a test enumerates the argvs.
- Every write verb there is **dry-run by default**. Our `--apply` maps to
  theirs; nothing infers it.
- Calling the CLI as a subprocess (never `import sensibo`, never the HTTP API)
  keeps this package's **zero runtime dependencies**.
- **Sensibo is cloud-only.** Actuation needs internet access to
  `home.sensibo.com`. `SENSIBO_API_KEY` comes from the environment. Never
  commit it.
- Power **state** is read with a zero-write dry-run `set --power` diff, because
  no sensibo-cli read path exposes `acState` yet
  ([sensibo-cli#15](https://github.com/agentculture/sensibo-cli/issues/15));
  `status()` never passes `--apply`, and it is one function so the upstream
  verb can replace it invisibly.

### Calendar-aware modes

- Strict mode switches on and off **automatically from zmanim** (candle
  lighting → *tzeit hakochavim*) for a configured location, computed in
  **pure stdlib** (`shabbos_goy/zmanim/`: NOAA solar maths plus Hebrew-calendar
  arithmetic), checked against published vectors. No zmanim dependency was
  added; `dependencies = []` still holds.
- **Yom Tov uses the strict column by default**; Israel/diaspora is config.
  Yom Tov-specific leniencies are out of scope, and adjacent holy days merge
  into one window.
- Everything is **set up before** Shabbat. Nothing may need a toggle during
  the day — the operator UIs exist, but relying on one during the day is what
  this project exists to avoid.
- **Clock trust:** an untrusted clock (no NTP sync after boot) or a missing
  location selects the strict column regardless of the nominal mode. Fail
  toward the stricter behaviour, never toward acting.
- Weekday behaviour is decided: the agent obeys spoken commands and hints,
  through the same whitelist and the same ears-only session.

### Speech stack: lobes integration

lobes serves a local Hebrew duplex voice session on the DGX Spark (ivrit.ai
Whisper turbo → Gemma → BlueTTS). This agent is its consumer for both the ears
and the `senses` role. Do not build a second speech stack. lobes runs no tools
and knows nothing about Shabbat: the decider's prompt, the gate, the whitelist
and the actuators all live here.

- **Contract:** `docs/contracts/realtime-tool-calling.md` in
  `agentculture/lobes-cli` (branch `spec/hebrew-realtime`, checked out at
  `../lobes-cli`). What this repo cited from it is recorded in
  `docs/skill-sources.md` under "Cited code (not skills)". No commit here
  touches that checkout.
- **Connect:** `ws://<lobes-host>:8001/v1/realtime?language=he&input_sample_rate=16000`
  with `Authorization: Bearer <gateway key>`. The host and key come from the
  environment only (`SHABBOS_GOY_LOBES_URL`, `SHABBOS_GOY_LOBES_API_KEY`, or
  lobes' own `GATEWAY_API_KEY`); `shabbos_goy/lobes/config.py` builds the
  query string (it also sets `turn_detection=server_vad`). The `senses` role
  defaults to the same host, with
  `SHABBOS_GOY_SENSES_URL` for a split deployment. There is no default host,
  port or key anywhere in the package. Connect directly to the box; the
  WebSocket is not proxied across the mesh. Never put a real host or key in a
  tracked file.
- **Stream audio for the whole session**, silence included, on its own thread.
  The server's VAD ends turns. The mic is never muted while the agent speaks;
  own-voice suppression discards the overlapping transcripts instead.
- **Empty `text` transcripts are dropped noise.** Ignore them.
- **Pause splitting:** the server ends a turn after 500 ms of silence and does
  not re-join. `shabbos_goy/joiner.py` buffers by the
  `speech_stopped` → next `speech_started` gap and emits only whole
  utterances; a reconnect discards the buffer rather than emitting a half.
- **Fixtures path:** `POST /v1/audio/transcriptions` (multipart,
  `language=he`) is the same Whisper in batch form — the golden set's
  `audio-batch` entrance.
- **Echo:** speaker and mic must be the same device (the reSpeaker XVF3800's
  own output) or its AEC has no reference; `validate_device_pair` refuses a
  mismatched pair.
- **Keep device names in Hebrew** in anything spoken.
- **Before household use:** the Spark's `TTS_DEBUG_TEXT` switch logs spoken
  text and is on for development. It must be turned off. The README's setup
  list says so.
- **Voice licence:** BlueTTS weights declare no licence. Do not redistribute
  them or make them this project's default. The licensed fallback is
  Chatterbox Multilingual. The golden set's synthesised audio is gitignored
  for the same reason.

### Privacy

Local processing throughout. Audio stays on the device and is never written to
disk. **Transcript text does travel to the local model** (the `senses` role,
over the local gateway) — never to a cloud service, never to disk, never into
a log line. Logs record the class, the intent, the gate verdict and the action;
pod ids are replaced by a stable alias and exceptions are reported as a type
name, because a message can quote a transcript. Docker keeps container stdout,
so this covers everything the service prints. The one place transcript text is
readable is the dashboard's bounded in-memory ring, deliberately, as bug
context; it is gone on restart.

### The dashboard

`shabbos_goy/web/` is a stdlib `http.server` page — no build step, no external
asset, no CDN — showing mode and next window, connection state, AC status and
recent utterances with class, intent, verdict, action and timings. Its
controls drive the same gates as speech.

Its security boundary is exactly two refusals, because it carries **no token**
(tailnet membership is the authentication, and therefore **every tailnet
device can press its buttons**):

1. it binds loopback or a Tailscale address (`100.64.0.0/10`,
   `fd7a:115c:a1e0::/48`) only — never a wildcard or a LAN address — unless
   config explicitly widens it;
2. a request whose `Host` does not name this server, or whose `Origin` names
   somewhere else, is refused before routing. GET is read-only by
   construction.

## Deployment: Docker, surviving reboots

The listener runs as a Compose service on the host that owns the microphone
(the DGX Spark today) — see `docker-compose.yml`. After a power cut in the
middle of Shabbat it must come back and resume the right mode **with no human
interaction**, because a person restarting it on Shabbat is exactly what this
project exists to avoid.

Requirements that bind any change:

- `restart: unless-stopped`, with the Docker daemon enabled at boot, and
  `json-file` logging with rotation.
- Audio is the **host PipeWire session** (deviation from the original ALSA
  plan, decision c42): `pw-record` / `pw-play` for capture and playback and
  `wpctl` for the agent's own volume, against the reSpeaker node **by name**.
  Wherever an older document says `arecord`/`aplay`/`amixer` or `/dev/snd`,
  read the PipeWire equivalent.
- Secrets (`SENSIBO_API_KEY`, the lobes gateway key) come from a gitignored
  env file. Private config (location, whitelist, pod ids) is bind-mounted
  read-only from `$XDG_CONFIG_HOME/shabbos-goy`.
- Startup is **stateless and self-healing**: recompute the mode from the clock
  and zmanim, re-apply the configured volume, retry lobes and Sensibo with
  backoff instead of crash-looping, persist no queued action. A missing
  variable is one named line and a retry, never a crash loop.
- A **wedged** listener must exit non-zero itself (the watchdog), because
  Compose restarts on process exit, not on an unhealthy healthcheck. The
  healthcheck (`shabbos-goy listen --healthcheck`) reads the heartbeat and
  proves transcripts are arriving, not that the process is alive.
- The on-box drills — PipeWire inside a container, a live `--apply`, a
  reconnect, a reboot — are **not yet done**. Do not write them up as if they
  were.

## Commands

```bash
uv sync                                                   # install (dev group included)
uv run pytest -n auto                                     # full suite, parallel
uv run pytest tests/test_cli.py::test_whoami_json -v      # a single test
uv run pytest -n auto --cov=shabbos_goy --cov-report=term # with coverage (CI gate: 60%)
uv run pytest -m golden -v                                # the golden set; needs a gateway

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

The default pytest run excludes the `golden` marker, so it never touches a
gateway. `tests/golden/README.md` is how the golden set is actually run.

## The CLI

Cited (cite-don't-import) from teken's `python-cli` reference, so the runtime
package has **no third-party dependencies**. Keep it that way: adding a domain
dependency is a deliberate decision to note in the CHANGELOG. Nothing has
needed one so far, zmanim and the WebSocket included.

Verbs live in `shabbos_goy/cli/_commands/`. Give each new verb an entry in
`shabbos_goy/explain/catalog.py` (keyed by command-path tuple).

| Verb | Purpose |
|---|---|
| `classify "<hebrew text>"` | class + intent + confidence + the gate's verdict (dry; the primary test surface) |
| `zmanim` | the current mode, window kinds, and the next strict window |
| `actions` | the whitelist in effect and the intent-to-tool map |
| `preflight` | every precondition for strict mode, read-only, exits 2 naming failures |
| `ac status` / `ac power` | read the pod, or switch power (`--apply`) |
| `volume get` / `volume set` | the agent's own volume (`--apply`) |
| `mode show` / `mode set` | the resolved mode and the in-memory override |
| `listen` | the ambient loop; what the container runs; `--script` replays with no hardware |
| `whoami`, `learn`, `explain`, `overview`, `doctor`, `cli overview` | the agent-first baseline |

`ac`, `volume` and `mode` proxy to the running listener's loopback control
endpoint rather than calling an adapter directly, so a CLI process can never
bypass the listener's whitelist, rate limits or delay. With no listener they
exit 2 with a remediation (`mode show` falls back to a local computation).

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
  `docs/skill-sources.md`, which also records the **cited source files**
  (lobes-cli's WebSocket layer, its PipeWire helpers, its event fixtures).
  Tool prerequisites: `devex` and `agtag` on PATH, and optionally `colleague`.
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
- This file describes the repo **as it exists on disk**. Keep claims grounded,
  and keep "built" separate from "verified on hardware".

## Layout

```text
shabbos_goy/
  cli/                agent-first CLI (cited from teken): parser, output/error contract, _commands/
  explain/            markdown catalog for `explain`
  decider/            the model-backed labeller: prompt, Gemma client, replay, rolling context,
                      oracle (tests only)
  classifier/         the retired rule cascade -- TEST ORACLE ONLY, never imported at runtime
  lobes/              ears-only realtime client: ws wire, config from env, session
  zmanim/             stdlib sun maths, Hebrew calendar, strict windows
  actuators/          sensibo-cli adapter (argv-locked power control)
  audio/              PipeWire capture/playback/volume adapter
  runtime/            the listener's threads, heartbeat, connection state
  web/                the Tailscale-only dashboard (server, page, bind rules)
  pipeline.py         one transcript in, at most one action out -- every gate, in order
  policy.py           the mode x class table (may_act)
  mode.py             zmanim-computed mode, in-memory override, clock trust
  config.py           JSON config + whitelist, fails closed
  joiner.py           re-joins pause-split sentences
  limits.py           rate limits, strict-mode delay, bounded rings
tests/                pytest, fixtures-only by default; tests/golden/ is the measured evidence
scripts/              harness-smoke.py, scan-secrets.py, golden-set.py
docs/                 specs/ + plans/ (historical contract), halacha-open-questions.md,
                      harness selection/automation, skill-sources.md (vendoring + citation ledger)
.claude/skills/       vendored skill kit (cite-don't-import)
.eidetic/memory/      committed eidetic memory store
culture.yaml          mesh identity (suffix + backend)
```

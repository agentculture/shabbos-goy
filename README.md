# shabbos-goy

A Hebrew-speaking, speech-to-speech household agent that helps observant Jews
on Shabbat and Yom Kippur without breaking them. It **never takes direct
commands**. It only infers intent from indirect remarks, e.g. "הלוואי שהיה קר"
("I wish it was cold") → turn on the AC.

> **About the name.** *Shabbos goy* is the familiar Yiddish term for the
> traditional role of a non-Jew who helps a Jewish household on Shabbat, often
> in response to a hint rather than a request. The name describes that role;
> it is not meant as a slur.
>
> **No rabbinic approval.** This project makes **no claim of rabbinic
> approval (*hechsher*)** and does not decide halacha. Whether and how a device
> like this may be used on Shabbat or Yom Kippur is an open question. **Ask
> your own rav.** The permitted actions live in configuration so a community
> can narrow them to match its posek.

## Status

**Early scaffold.** The repository was provisioned from
`culture-agent-template` and so far contains only the agent-first CLI baseline
(identity, `learn`, `explain`, `overview`, `doctor`). The build brief is
[issue #1](https://github.com/agentculture/shabbos-goy/issues/1). Everything
under [How it will work](#how-it-will-work-planned) is **planned**.

## How it will work (planned)

### The one rule: hints, never commands

| What someone says | Class | What the agent does |
|---|---|---|
| "חם פה" / "It's so hot in here" | remark | may cool the room |
| "הלוואי שהיה קר" / "I wish it was cold" | wish | may turn on the AC |
| "תדליק את המזגן" / "Turn on the AC" | imperative | **nothing** |
| "אתה יכול להדליק את האור?" / "Can you turn on the light?" | request | **nothing** |
| "למה המזגן לא דלוק?" / "Why isn't the AC on?" | rebuke | **nothing** |

- **No wake word and no confirmation questions.** Both would turn the exchange
  into a command. When unsure, it does nothing.
- **Commands are dropped, never queued**, including across restarts.
- It may speak a short, neutral Hebrew remark ("המזגן פועל", "the AC is on")
  that does not invite a reply.
- The rule is enforced by a **tested Hebrew utterance classifier** in this
  repo, not by a prompt. Its headline metric is how rarely it acts on a
  command.

### Pipeline

```text
microphone → lobes (local Hebrew ASR, ears-only) → transcript
          → classifier → Shabbat/Yom Kippur calendar gate (zmanim)
          → whitelisted tool call (AC via sensibo-cli) → optional neutral remark (TTS)
```

- **Speech** runs locally on the lobes Hebrew realtime stack (ivrit.ai Whisper,
  local TTS). Audio stays on the device and is never recorded. Logs keep only
  the classified intent and the action taken.
- **Climate control via tool calling.** Actions are declared as tools
  (function name + JSON-schema arguments). The first tool backend is
  [`sensibo-cli`](https://github.com/agentculture/sensibo-cli) for Sensibo
  smart-AC control. Every tool call, whether it comes from a rule or from a
  model, passes through the classifier, the calendar gate and the
  configured whitelist (devices, modes, temperature range) before anything
  actuates. Sensibo is a cloud service, so AC control needs internet access.
- **Calendar-aware.** Shabbat/Yom Kippur mode turns on and off automatically
  from zmanim for a configured location. Everything is configured before
  Shabbat, so nothing has to be toggled during it.

### Deployment: Docker, survives reboots

It will run as a Docker Compose service on the machine that owns the
microphone (currently a DGX Spark), with `restart: unless-stopped` and the
Docker daemon enabled at boot. After a power cut it comes back by itself and
works out the current mode from the clock, with no one touching it. Secrets
(the Sensibo API key, the lobes gateway key) and private config (location,
whitelist, device ids) stay outside the repository: a gitignored env file and
a read-only mount of `~/.config/shabbos-goy`.

## Quickstart (what exists today)

```bash
uv sync
uv run pytest -n auto                 # run the test suite
uv run shabbos-goy whoami             # identity from culture.yaml
uv run shabbos-goy learn              # self-teaching prompt (add --json)
uv run teken cli doctor . --strict    # the agent-first rubric gate CI runs
```

## CLI

| Verb | What it does |
|------|--------------|
| `whoami` | Report this agent's nick, version, backend, and model from `culture.yaml`. |
| `learn` | Print a structured self-teaching prompt. |
| `explain <path>` | Markdown docs for any noun/verb path. |
| `overview` | Read-only descriptive snapshot of the agent. |
| `doctor` | Check the agent-identity invariants (prompt-file-present, backend-consistency). |
| `cli overview` | Describe the CLI surface itself. |
| `classify "<text>"` | *(planned)* Class, inferred intent, and whether it would act. Dry. |
| `zmanim --location …` | *(planned)* The current Shabbat/Yom Kippur mode window. |
| `actions` | *(planned)* The action whitelist in effect. |
| `listen` | *(planned)* The ambient loop that the container runs. |

Every command supports `--json`. Results go to stdout and errors/diagnostics
to stderr (never mixed). Exit codes: `0` success, `1` user error, `2`
environment error, `3+` reserved. Any verb that actuates is **dry-run by
default**, and `--apply` actuates.

## Agent harnesses

This is an AgentCulture mesh agent. `culture.yaml` declares
`backend: claude`, so [`CLAUDE.md`](CLAUDE.md) is the mesh resident's prompt
and the fullest write-up of the repo's design and conventions. Read it first.

Four harnesses can work in this repo interactively, each reading its own file.
There is intentionally no shared `AGENTS.md`:

| Harness | File(s) |
|---------|---------|
| Claude Code | [`CLAUDE.md`](CLAUDE.md) |
| Pi / associate | [`AGENTS.override.md`](AGENTS.override.md) + [`.pi/SYSTEM.md`](.pi/SYSTEM.md) |
| colleague | [`AGENTS.colleague.md`](AGENTS.colleague.md) |
| Qwen Code | [`QWEN.md`](QWEN.md) |

Which binary you run chooses the interactive harness. `culture.yaml`'s
`backend` chooses only the mesh resident. See
[`docs/harness-selection.md`](docs/harness-selection.md).

## License

Apache 2.0. See [`LICENSE`](LICENSE).

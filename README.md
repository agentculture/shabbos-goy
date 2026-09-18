# shabbos-goy

A Hebrew-speaking household agent that listens in a room and switches the air
conditioner's power. On Shabbat, Yom Kippur and Yom Tov it acts **only on
indirect speech** — a remark, a wish, a complaint about being uncomfortable —
and never on a spoken command: "הלוואי שהיה קר" ("I wish it was cold") may turn
the AC on, while "תדליק את המזגן" ("turn on the AC") is dropped.

This README is written for two readers:

- **the household** — the people in the room, who say nothing to the device
  and touch nothing during the day;
- **the operator** — one person who sets everything up **before** candle
  lighting, and who has a CLI and a dashboard for the rest of the week.

> **About the name.** *Shabbos goy* is the familiar Yiddish term for the
> traditional role of a non-Jew who helps a Jewish household on Shabbat, often
> in response to a hint rather than a request. The name describes that role;
> it is not meant as a slur, and it is not a claim that the analogy holds.
>
> **No rabbinic approval.** This project makes **no claim of rabbinic approval
> (*hechsher*)** and does not decide halacha. Whether and how a device like
> this may be used on Shabbat, Yom Kippur or Yom Tov is an open question.
> **Ask your own rav.** The questions this project deliberately does not
> answer are listed in
> [`docs/halacha-open-questions.md`](docs/halacha-open-questions.md). The
> permitted actions live in configuration, so a community can narrow them.

## Status

The agent is built and runs end to end from fixtures. It has **not** been run
through a full household Shabbat, and several claims below are verified only
by tests, not on the hardware.

Shipped and tested with no microphone, no lobes server and no Sensibo account:

- the ears-only lobes realtime client, the transcript joiner, the decider, the
  decision pipeline, the zmanim mode resolver, the Sensibo and PipeWire
  adapters, the rate limits and the strict-mode delay;
- the CLI (`classify`, `zmanim`, `actions`, `preflight`, `ac`, `volume`,
  `mode`, `listen`) and the Tailscale-only dashboard;
- the golden set: 295 committed Hebrew rows with the outcome each expects, and
  the scoring code CI runs offline.

**Not yet verified** (do not read anything below as proven on hardware):

- the golden set has **not** yet been run live against the real model and the
  real speech stack on the box; the release-blocking number (zero strict-mode
  false positives on commands) is therefore not yet measured;
- PipeWire capture and playback from inside a container, against the host's
  own PipeWire session;
- a live `--apply` run against a real Sensibo pod;
- reconnecting to lobes after the gateway restarts, in place;
- a host reboot with the container returning to the correct mode unattended;
- classifier behaviour on children's speech, accented speech, guests, or
  mixed Hebrew/English/Yiddish.

## The invariant: no spoken command, in strict mode

**Strict mode** is Shabbat, Yom Kippur and Yom Tov, computed from zmanim for
the configured location. **Weekday mode** is everything else.

| What someone says | Class | Strict mode | Weekday mode |
|---|---|---|---|
| "חם פה" / "It's hot in here" | remark | may act | may act |
| "הלוואי שהיה קר" / "I wish it was cold" | wish | may act | may act |
| "קשה לי לישון בחום הזה" / "Hard to sleep in this heat" | discomfort | may act | may act |
| "תדליק את המזגן" / "Turn on the AC" | imperative | **nothing** | acts |
| "אתה יכול להדליק את המזגן" / "Can you turn on the AC" | request | **nothing** | acts |
| "למה המזגן לא דלוק" / "Why isn't the AC on" | rebuke | **nothing** | acts |
| anything else | unrelated | nothing | nothing |

This invariant is about **speech overheard by a listening box**. It is not
about the operator's own tools:

- **No wake word, no confirmation question.** Both would turn the exchange
  into a command. When unsure, the agent does nothing and says nothing.
- **A refused command is dropped, never queued** — including across a crash or
  a reboot. The agent persists no pending action, no transcript buffer and no
  mode override.
- **In strict mode the agent acts after a short delay** (configurable, about
  15 seconds by default), held in memory only.
- **Spoken output is neutral, and only on weekdays.** In strict mode the agent
  says nothing at all.
- **The CLI and the dashboard are operator tools.** They work in every mode,
  including strict mode, and can force strict mode on or switch it off inside
  a zmanim window. A person pressing a button has not spoken a command to the
  agent. Whether a person may press it on Shabbat is
  [a question for a rav](docs/halacha-open-questions.md), not a claim this
  project makes.

### Weekday mode obeys anyone in earshot

On a weekday the agent acts on commands, and it does not know who is
speaking. Anyone within earshot — a child, a guest, a voice from a phone the
model does not recognise as such — can switch the AC power or change the
agent's volume. **The whitelist is the only control.** It is why the
whitelist is narrow (AC power on/off and the agent's own volume, nothing
else), why it is configuration rather than code, and why it must stay narrow
if more actuators are ever added.

## How it works

```text
microphone (PipeWire)
  -> lobes /v1/realtime, ears-only        local Hebrew ASR; no tools, no response.create
  -> transcript joiner                    re-joins a sentence the ASR split across a pause
  -> decider: the lobes `senses` role     a local Gemma returns {class, intent, confidence}
  -> this repo's gates                    confidence floor -> mode x class gate -> intent
                                          -> whitelist -> argument validation -> rate limits
                                          -> already-in-that-state? -> strict-mode delay
  -> sensibo-cli / PipeWire volume        dry-run unless --apply
  -> optional neutral Hebrew remark       weekdays only
```

### The model labels; this repository decides

Each utterance, plus a trimmed in-memory window of recent ones, is sent to a
**local language model** — the lobes `senses` role (Gemma) on the same box —
which returns a label: a class, an inferred intent and a confidence. No tool is
declared to it and nothing asks it to act; its answer is data.

This repository's code treats that label as **untrusted input**. It
re-validates the shape, applies a minimum confidence (0.6 by default), runs
the mode-by-class gate, maps the intent to a tool, checks the configured
whitelist, validates the argument, applies the rate limits, skips the action
if the device is already in that state, and waits out the strict-mode delay.
If the model is down, slow, malformed or inventive, the result is the same:
**do nothing**.

Two consequences worth stating plainly:

- **"A spoken command never acts in strict mode" is not proven
  deterministically.** The code refuses anything *labelled* a command, and
  that refusal is exhaustively tested. Whether commands actually get that
  label is a property of the model and the prompt, and it is **measured**, not
  assumed — by the golden set (`tests/golden/README.md`): 295 rows through
  three entrances, with zero strict-mode false positives as a
  release-blocking threshold, re-run whenever the prompt version, the model or
  lobes changes. That live run has not happened yet.
- **The language model has to be running.** An ears-only deployment on a small
  box with no LLM is no longer possible: the agent needs both the speech stack
  and the `senses` role.

### What it can actuate

| Hint | Intent | Action |
|---|---|---|
| the room is hot | `cool` | AC **power on** |
| the room is cold | `warm` | AC **power off** |
| it is too quiet | `louder` | the agent's own volume up one step |
| it is too loud | `quieter` | the agent's own volume down one step |
| is the AC on | `status` | a spoken answer, **weekdays only** |

AC control is **power on/off only** — never mode, temperature, fan or swing.
The adapter cannot build any other `sensibo` flag. `sensibo-cli` is called as a
subprocess (never imported), so this package keeps `dependencies = []`. Sensibo
is a cloud service, so actuation needs internet access even though speech
stays local.

### Zmanim

Candle lighting to *tzeit hakochavim* is computed in pure standard library
(NOAA solar maths plus Hebrew-calendar arithmetic), checked against published
vectors. The candle-lighting offset, the tzeit definition and Israel/diaspora
are configuration. Adjacent holy days merge into one window. If the clock
cannot be trusted (no NTP sync after boot) or the location is missing, the
agent fails **toward strict mode and toward not acting**, never toward acting.

### The dashboard

A stdlib `http.server` page with no build step and no external asset, showing
the mode and next window, the lobes connection state, AC status, and recent
utterances with class, intent, gate verdict, action and timings. Its buttons
drive the same whitelist, the same validation, the same rate limits and the
same adapters as speech.

It binds **loopback or a Tailscale address only** — never `0.0.0.0`, never a
LAN address — and carries **no token**: tailnet membership is the
authentication. That means **every device on the tailnet can press its
buttons**, including switching AC power. Requests whose `Host` or `Origin`
names somewhere else are refused before routing, and GET never changes state.

### Privacy

- Audio stays on the device and is never written to disk.
- Transcript text goes to the **local** model over the local gateway. It never
  goes to a cloud service, never to disk, and never into a log line.
- Logs carry the class, the intent, the gate verdict and the action — never
  transcript text, never a pod id (a stable alias is logged instead), never a
  key. Docker keeps container stdout, so this covers what the service prints.
- The dashboard serves recent transcript text from a **bounded in-memory
  ring**, deliberately, as bug context. It is gone on restart.

## Quickstart

```bash
uv sync
uv run pytest -n auto                        # the whole suite: no hardware, no network
uv run shabbos-goy classify "חם פה" --json   # decide, never act (needs a decider; see below)
uv run shabbos-goy zmanim                    # the current mode and the next strict window
uv run shabbos-goy actions                   # the whitelist in effect
uv run shabbos-goy listen --script <events.jsonl> \
  --decider replay --replay-file tests/fixtures/decider/replay.json
```

`classify` and `listen` ask the `senses` role by default, which needs the
environment variables below. For an offline run, pass `--decider replay` with a
recorded answers file; `listen --script` takes a JSONL events file or a WAV and
runs the same loop with no microphone, no server and no socket.

## Setting it up, before candle lighting

Every step here is the **operator's**, and every one of them is done before
Shabbat starts. Nothing in this list may be needed during the day, because
doing it during the day would be the thing this project exists to avoid.

1. **Write the config.** `$XDG_CONFIG_HOME/shabbos-goy/config.json`
   (`tests/fixtures/config.example.json` is the full shape, with placeholder
   values). It holds the location and timezone, the candle-lighting offset,
   the tzeit definition, Israel or diaspora, the whitelist (which tools, which
   Sensibo pod ids, which arguments), the rate limits, the strict-mode delay,
   the volume bounds and the dashboard bind address.
2. **Set the secrets in the environment**, never in a tracked file:
   `SHABBOS_GOY_LOBES_URL` and `SHABBOS_GOY_LOBES_API_KEY` (or lobes' own
   `GATEWAY_API_KEY`) for the speech stack and the `senses` role, and
   `SENSIBO_API_KEY` for the AC. There is no default host, port or key
   anywhere in this package.
3. **Unset `TTS_DEBUG_TEXT` on the lobes box.** It logs spoken text and is on
   for development. Turn it off before household use.
4. **Set the volume.** The default is silent. In strict mode the agent says
   nothing regardless.
5. **Check the pod.** `shabbos-goy ac status` reads without writing.
6. **Run the preflight**: `uv run shabbos-goy preflight`. It exits 0 only when
   every check passes and names the failures otherwise: the lobes key, the
   `senses` role answering and deciding, the Sensibo key, a whitelisted pod,
   the audio node, clock sync, and a valid location.
7. **Read the printed window by eye.** `shabbos-goy zmanim` prints the next
   strict window in local time. Check it against your own zmanim before
   relying on it.
8. **Start the service and leave it alone.**

## Deployment

The listener runs as a Compose service on the host that owns the microphone
(a DGX Spark today) — see `docker-compose.yml`. It runs `shabbos-goy listen`,
restarts unless stopped, and its healthcheck reads a heartbeat that proves
transcripts are actually arriving rather than that the process is alive.

Startup is stateless by design: the mode is recomputed from the clock and
zmanim on every boot, the configured volume is re-applied, lobes and Sensibo
are retried with backoff rather than crash-looping, and nothing queued
survives a restart. A missing key is one logged line and a retry, because a
container that exits on a missing variable takes the household's Shabbat with
it.

Secrets come from a gitignored env file; private config is bind-mounted
read-only from `$XDG_CONFIG_HOME/shabbos-goy`. The container drills — PipeWire
inside a container, a live `--apply`, a reconnect and a reboot — are
**not yet done**.

## CLI

| Verb | What it does |
|------|--------------|
| `classify "<text>"` | Class, intent, confidence and the gate's verdict. Never acts. |
| `zmanim` | The current mode, the window kinds, and the next strict window. |
| `actions` | The whitelist in effect and the intent-to-tool mapping. |
| `preflight` | Every precondition for strict mode, in one read-only run. |
| `ac status` / `ac power` | Read the pod, or switch its power. Dry-run unless `--apply`. |
| `volume get` / `volume set` | The agent's own volume. Dry-run unless `--apply`. |
| `mode show` / `mode set` | The resolved mode, and the in-memory override. |
| `listen` | The ambient loop. What the container runs. `--script` replays a file with no hardware. |
| `whoami` | Nick, version, backend and model from `culture.yaml`. |
| `learn` | A structured self-teaching prompt. |
| `explain <path>` | Markdown docs for any noun/verb path. |
| `overview` / `cli overview` | A snapshot of the agent, or of the CLI surface. |
| `doctor` | The agent-identity invariants. |

`ac`, `volume` and `mode` talk to the running listener's loopback control
endpoint rather than calling an adapter directly, so the CLI can never bypass
the listener's whitelist, rate limits or delay. With no listener running they
exit 2 and say so (`mode show` falls back to a local zmanim computation).

Every command supports `--json`. Results go to stdout and errors and
diagnostics to stderr, never mixed. Exit codes: `0` success, `1` user error,
`2` environment error, `3+` reserved. **Any verb that actuates is dry-run by
default**, and `--apply` actuates.

## Agent harnesses

This is an AgentCulture mesh agent. `culture.yaml` declares `backend: claude`,
so [`CLAUDE.md`](CLAUDE.md) is the mesh resident's prompt and the fullest
write-up of the design and conventions. Read it first.

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

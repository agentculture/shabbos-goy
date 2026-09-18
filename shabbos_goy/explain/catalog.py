"""Markdown catalog for ``shabbos-goy explain <path>``.

Each entry is verbatim markdown. Keys are command-path tuples. The empty tuple
and ``("shabbos-goy",)`` both resolve to the root entry.

Keep bodies self-contained: an agent reading one entry should get enough
context without chaining reads.
"""

from __future__ import annotations

_ROOT = """\
# shabbos-goy

A Hebrew-speaking, speech-to-speech household agent that helps observant Jews
on Shabbat and Yom Kippur without the user breaking the day. It never takes a
direct command: it only acts on intent it *infers* from indirect speech (a
state remark, a wish, a discomfort). A false positive on an
imperative/request/rebuke is the one failure this whole repo exists to
prevent — see `shabbos-goy explain classify` and `shabbos-goy explain actions`.

## Verbs

- `shabbos-goy whoami` — identity probe from `culture.yaml`.
- `shabbos-goy learn` — structured self-teaching prompt.
- `shabbos-goy explain <path>` — markdown docs for any noun/verb.
- `shabbos-goy overview` — descriptive snapshot of the agent.
- `shabbos-goy doctor` — check the agent-identity invariants.
- `shabbos-goy cli overview` — describe the CLI surface.
- `shabbos-goy classify "<text>"` — decide (never act) on one utterance.
- `shabbos-goy zmanim` — the current mode window, from config.
- `shabbos-goy actions` — the whitelist in effect, and the intent map.
- `shabbos-goy preflight` — check every precondition before Shabbat starts.
- `shabbos-goy ac status|power` — AC noun (read/write, via the listener).
- `shabbos-goy volume get|set` — volume noun (read/write, via the listener).
- `shabbos-goy mode show|set` — the mode in effect, and its override.
- `shabbos-goy listen` — the ambient loop; what the container runs.

## Exit-code policy

- `0` success
- `1` user-input error
- `2` environment / setup error
- `3+` reserved

## See also

- `shabbos-goy explain whoami`
- `shabbos-goy explain doctor`
- `shabbos-goy explain classify`
- `shabbos-goy explain preflight`
- `shabbos-goy explain listen`
"""

_WHOAMI = """\
# shabbos-goy whoami

Reports the agent's identity from `culture.yaml`: nick (`suffix`), backend,
served model, and the package version. Read-only.

## Usage

    shabbos-goy whoami
    shabbos-goy whoami --json
"""

_LEARN = """\
# shabbos-goy learn

Prints a structured self-teaching prompt covering purpose, command map,
exit-code policy, `--json` support, and the `explain` pointer.

## Usage

    shabbos-goy learn
    shabbos-goy learn --json
"""

_EXPLAIN = """\
# shabbos-goy explain <path>

Prints markdown documentation for any noun/verb path. Unlike `--help` (terse,
positional), `explain` is global and addressable by path.

## Usage

    shabbos-goy explain shabbos-goy
    shabbos-goy explain whoami
    shabbos-goy explain --json <path>
"""

_OVERVIEW = """\
# shabbos-goy overview

Read-only descriptive snapshot of the agent: identity (from `culture.yaml`), the
verb surface, and the sibling-pattern artifacts the template carries. Accepts an
ignored `target` so a stray path never hard-fails.

## Usage

    shabbos-goy overview
    shabbos-goy overview --json
"""

_DOCTOR = """\
# shabbos-goy doctor

Checks the agent-identity invariants `steward doctor` verifies:
prompt-file-present and backend-consistency (`claude` → `CLAUDE.md`), plus a
skills-present check. Exits 1 when unhealthy.

prompt-file-present requires the *resident* prompt the declared backend
actually reads. Other harness prompt files recognized under the same backend
name (`AGENTS.override.md`, `.pi/SYSTEM.md`, `QWEN.md`) belong to
interactively available harnesses the mesh daemon never loads; they are
reported by the informational harness-prompts check and never substituted.

## Usage

    shabbos-goy doctor
    shabbos-goy doctor --json
"""

_CLI = """\
# shabbos-goy cli

Noun group for CLI-surface introspection. `cli overview` describes the CLI
itself (distinct from the global `overview`, which describes the agent).

## Usage

    shabbos-goy cli overview
    shabbos-goy cli overview --json
"""

_CLASSIFY = """\
# shabbos-goy classify "<text>"

Decides — but never acts. Calls a `Decider` (the Gemma-backed lobes `senses`
role by default, or a recorded replay file for offline use) on one utterance
and prints its class, intent, confidence, decider source, the mode it was
evaluated under, and whether the agent *would* act under `policy.may_act` —
the gate verdict. Never calls the retired rule classifier
(`shabbos_goy.classifier`), which survives only as a test oracle. Never
actuates: no adapter, no whitelist check, no rate limiter.

## Usage

    shabbos-goy classify "חם פה נורא"
    shabbos-goy classify "חם פה נורא" --mode strict --json
    shabbos-goy classify "חם פה נורא" --decider replay --replay-file replay.json

## Flags

- `--decider {gemma,replay}` — which decider to ask (default: `gemma`).
- `--replay-file PATH` — a JSON replay file; required with `--decider replay`.
- `--mode {weekday,strict,auto}` — the mode to gate under (default: `auto`,
  computed from zmanim).

## Exit codes

- `2` — `--decider gemma` with no usable lobes environment (names the
  missing variable).
"""

_ZMANIM = """\
# shabbos-goy zmanim

Read-only. Prints the current mode (`weekday`/`strict`), the active window
kind(s) (`shabbat`/`yom_kippur`/`yom_tov`), whether the clock is trusted, and
the next strict window in local time — all from
`shabbos_goy.mode.resolve_mode` and the configured location. Never gates an
action; it only describes.

## Usage

    shabbos-goy zmanim
    shabbos-goy zmanim --json
"""

_ACTIONS = """\
# shabbos-goy actions

Read-only. Prints the effective actuation whitelist (pure config — a broken
or missing config file reports an empty whitelist, never a crash) and the
fixed intent → tool mapping: `cool` → AC power on, `warm` → AC power off,
`quieter`/`louder` → a volume step, `status` → a spoken status statement, on
weekdays only.

## Usage

    shabbos-goy actions
    shabbos-goy actions --json
"""

_PREFLIGHT = """\
# shabbos-goy preflight

Read-only. Checks every precondition for Shabbat/Yom Kippur mode: lobes API
key + health (the `senses` role's `/capabilities`), one authenticated
`decide()` round trip, the Sensibo API key + at least one whitelisted pod, the
default audio node (`wpctl`), clock trust, and the configured location.
Never actuates. Exits `0` only when every check passes; otherwise exits `2`,
naming every failed check. Also prints the next strict window in local time.

## Usage

    shabbos-goy preflight
    shabbos-goy preflight --json
"""

_AC = """\
# shabbos-goy ac

AC noun: `status` (read) and `power` (write). Both proxy to the running
listener's control endpoint (the listener owns the whitelist, the rate
limiter and the real sensibo-cli adapter) — this CLI never calls sensibo-cli
directly. `power` is dry-run unless `--apply` is passed. With no listener
running, both exit `2` with a remediation pointing at `shabbos-goy listen`.

## Usage

    shabbos-goy ac status
    shabbos-goy ac power on
    shabbos-goy ac power on --apply
"""

_AC_STATUS = """\
# shabbos-goy ac status

Current AC power/temperature/humidity, read-only, via the listener's control
endpoint. Exits `2` with no listener running.

## Usage

    shabbos-goy ac status
    shabbos-goy ac status --json
"""

_AC_POWER = """\
# shabbos-goy ac power {on,off}

Request AC power on/off, via the listener's control endpoint. Dry-run unless
`--apply` is passed. Exits `2` with no listener running.

## Usage

    shabbos-goy ac power on
    shabbos-goy ac power off --apply
"""

_VOLUME = """\
# shabbos-goy volume

Volume noun: `get` (read) and `set` (write). Both proxy to the running
listener's control endpoint (the listener owns the real `wpctl` adapter and
its clamp bounds). `set` is dry-run unless `--apply` is passed. With no
listener running, both exit `2` with a remediation.

## Usage

    shabbos-goy volume get
    shabbos-goy volume set up
    shabbos-goy volume set down --apply
"""

_VOLUME_GET = """\
# shabbos-goy volume get

Current volume level/muted state, read-only, via the listener's control
endpoint. Exits `2` with no listener running.

## Usage

    shabbos-goy volume get
    shabbos-goy volume get --json
"""

_VOLUME_SET = """\
# shabbos-goy volume set {up,down}

Step the volume up or down, via the listener's control endpoint. Dry-run
unless `--apply` is passed. Exits `2` with no listener running.

## Usage

    shabbos-goy volume set up
    shabbos-goy volume set down --apply
"""

_MODE = """\
# shabbos-goy mode

Mode noun: `show` (read) and `set` (override, write). The resolved mode and
its in-memory override live inside the running listener process, so both
verbs talk to its control endpoint. `mode show` is the one exception with a
graceful, listener-free fallback: with no listener reachable it computes the
zmanim-implied mode locally and prints the next strict window. `mode set` has
no such fallback — it exits `2` with no listener running, since nothing
would hold the override.

## Usage

    shabbos-goy mode show
    shabbos-goy mode set strict
    shabbos-goy mode set auto
"""

_MODE_SHOW = """\
# shabbos-goy mode show

The mode in effect now. Asks the listener's control endpoint; with no
listener reachable, falls back to a local zmanim computation
(`shabbos_goy.mode.resolve_mode`, with no override — an override only ever
lives in a running listener's memory) and prints the next strict window in
local time.

## Usage

    shabbos-goy mode show
    shabbos-goy mode show --json
"""

_MODE_SET = """\
# shabbos-goy mode set {weekday,strict,auto}

Force (`weekday`/`strict`) or clear (`auto`) the listener's in-memory mode
override, via its control endpoint. Memory-only: never written to disk,
never survives a restart. Exits `2` with no listener running.

## Usage

    shabbos-goy mode set strict
    shabbos-goy mode set auto
"""


_LISTEN = """\
# shabbos-goy listen

The ambient runtime loop, and what the Docker service runs: the ears-only
lobes session -> the transcript joiner -> the decider -> the calendar gate ->
the whitelist -> an actuator. **Dry-run unless `--apply`.** There is no wake
word, no confirmation question and no queued action: a refused utterance is
dropped, never retried, and nothing survives a restart.

It also starts the loopback control endpoint the `ac`, `volume` and `mode`
verbs talk to (default `127.0.0.1:8787`, or the port from
`dashboard_bind_address`), and — unless `--no-dashboard` — the Tailscale-only
dashboard. A dashboard address that is not up yet never stops the listener:
the bind is retried on a timer.

## Usage

    shabbos-goy listen
    shabbos-goy listen --apply
    shabbos-goy listen --script events.jsonl --decider replay --replay-file replay.json
    shabbos-goy listen --healthcheck --json

## Flags

- `--apply` — actually actuate (default: dry-run).
- `--script PATH` — run the same loop with no microphone: a JSONL file of
  lobes events (no server, no socket), or a WAV streamed through the real
  client path.
- `--decider {gemma,replay}` / `--replay-file PATH` — which decider to ask.
- `--no-dashboard` — skip the dashboard; the control endpoint still runs.
- `--control-address ADDR` — where the loopback control endpoint binds.
- `--heartbeat PATH` — the liveness file (default: `$SHABBOS_GOY_HEARTBEAT`,
  else a tmpfs path).
- `--healthcheck` — read the heartbeat and exit `0` (healthy) or `1`. Starts
  nothing; this is what a container `HEALTHCHECK` runs.

## Privacy

Nothing it prints carries transcript text, the context window, a key or a pod
id: diagnostics are named event lines, and the summary is counts, verdicts
and action names.
"""


ENTRIES: dict[tuple[str, ...], str] = {
    (): _ROOT,
    ("shabbos-goy",): _ROOT,
    ("whoami",): _WHOAMI,
    ("learn",): _LEARN,
    ("explain",): _EXPLAIN,
    ("overview",): _OVERVIEW,
    ("doctor",): _DOCTOR,
    ("cli",): _CLI,
    ("cli", "overview"): _CLI,
    ("classify",): _CLASSIFY,
    ("zmanim",): _ZMANIM,
    ("actions",): _ACTIONS,
    ("preflight",): _PREFLIGHT,
    ("ac",): _AC,
    ("ac", "overview"): _AC,
    ("ac", "status"): _AC_STATUS,
    ("ac", "power"): _AC_POWER,
    ("volume",): _VOLUME,
    ("volume", "overview"): _VOLUME,
    ("volume", "get"): _VOLUME_GET,
    ("volume", "set"): _VOLUME_SET,
    ("mode",): _MODE,
    ("mode", "overview"): _MODE,
    ("mode", "show"): _MODE_SHOW,
    ("mode", "set"): _MODE_SET,
    ("listen",): _LISTEN,
}

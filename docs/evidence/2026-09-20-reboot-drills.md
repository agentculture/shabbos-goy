# Reboot drills, 2026-09-20 — DGX Spark GB10

Frozen evidence record. Two reboots, one live actuation, one deafness fault.
Every number here came from a command run on the box that day. Where an
attribution was inferred rather than read, this document says so.

This is a log, not a spec. The claims it grounds live in the frames listed
under [What this grounds](#what-this-grounds).

## Deployment as tested

| | |
|---|---|
| Image | built from unmodified `main` (no file under `shabbos_goy/` changed) |
| Command | `shabbos-goy listen --apply`, via a gitignored `docker-compose.override.yml` |
| Secrets | gitignored `docker/shabbos-goy.env` (the `grant` read-only mount fails, see below) |
| Lobes | `model-gear-gateway` on host `:8001`, `senses` backed by `model-gear-vllm-multimodal` |
| Config | `$XDG_CONFIG_HOME/shabbos-goy/config.json`, **changed at 10:53:50** (see the caveat) |

The single value that took `preflight` from 6/8 to 8/8 was
`SHABBOS_GOY_LOBES_URL=ws://localhost:8001/v1/realtime`.

## Boot timeline

```text
boot -2   2026-09-19 21:51:24 → 2026-09-20 10:11:21
boot -1   2026-09-20 10:11:45 → 11:05:17     drill 1
boot  0   2026-09-20 11:05:44 → …            drill 2
config.json mtime 10:53:50  — between the two drills
```

## Drill 1 — 10:11. Process recovered; hearing did not

| Check | Result |
|---|---|
| container healthy, unattended | PASS, 41 s |
| `RestartCount` | 7, then stable |
| configured `mic_node` present | **FAIL** — `pactl list short sources` listed only the HDMI monitor |
| `pw-record` binding | **FAIL** — bound to `HDMI 0:monitor_FL/FR` |
| transcripts | **FAIL** — heartbeat `transcript: null`, `/api/utterances` `count: 0` |
| `preflight` | PASS after the warm-up |

The agent was **Up (healthy) and deaf**: `pong` and `audio` both ticking while
capturing silence from an HDMI output's monitor. `audio` proves bytes were
sent, never that they carried sound.

Recovery needed a host action the container cannot perform:
`systemctl --user restart wireplumber`, after which both reSpeaker nodes
appeared — **without** the numeric suffixes the config had pinned.

### Two faults, and a caveat that limits what this drill proves

1. **WirePlumber published no node** for a USB device that ALSA had
   (`/proc/asound/cards` card 1, `lsusb` `2886:001a`) — observed directly.
2. **The configured names were stale**: `...analog-stereo.4` (mic) and
   `...analog-stereo.2` (speaker); the live names carry no suffix.

**Caveat.** Fault 2 on its own fully explains the fallback, because
`pw-record` accepts an unknown `--target` and falls back silently rather than
failing. Since `config.json` was corrected at 10:53:50 — between the drills —
the two drills differ in **at least two variables**, so nothing here
establishes a failure *rate* or that the enumeration fault is intermittent.

**Unread attribution.** `RestartCount = 7` is measured. The *cause* of those
seven exits was never read from the logs; an earlier write-up attributed them
to the boot race, which was an inference and is withdrawn.

## Drill 2 — 11:05. Clean, unattended

| Check | Result |
|---|---|
| container healthy, unattended | PASS, ~1 min |
| configured `mic_node` present | PASS |
| `pw-record` binding | PASS — `reSpeaker XVF3800 4-Mic Array:capture_FL/FR` |
| `speaker_node` present (AEC reference) | PASS |
| transcripts | PASS — fresh stamp `1789891854` > pre-wait `1789891632` |
| `preflight` 8/8 | PASS at **11:10:27** |

**Boot to full green: 4 m 48 s**, dominated by the vLLM multimodal load, well
inside the Compose healthcheck's 5 m `start_period`.

During the warm-up the agent **heard while unable to label**: transcripts
arrived, `senses` returned `http_503`, every decision came back
`no_decision`, and `action=none`. The fail-safe pointed the right way.

## Live actuation — the first in this project's history

```text
class=remark intent=cool verdict=acted action=ac_power_on target=ac
```

Verified thermally, which matters because the channel is open-loop IR and the
cloud's `power` field is only the last requested state:

```text
30.8 °C → 28.0 → 27.8      ambient, falling after the action
```

`sensibo read` exposes ambient, `feelsLike`, humidity, occupancy, motion, CO₂,
TVOC, IAQ and two nested room sensors — and **no** `acState`, mode or target.

## The deafness fault is in the product, not just the drill

`heartbeat.healthcheck()` takes `max()` over `pong`, `audio` and `transcript`
and drops `None`, so a fresh `audio` stamp alone returns `ok`. Demonstrated
live at 12:00 on a quiet room:

```text
transcript 1789894366   written_at 1789894577   window 120 s   → 211 s stale
listen --healthcheck → {'ok': True, 'reason': 'ok'}
```

A deaf listener passes the healthcheck indefinitely, so Compose never restarts
it. The product cannot distinguish **quiet** from **deaf**.

A failing healthcheck would not be enough either: `docker-compose.yml` says it
plainly — Compose *"does NOT restart a container merely for being reported
`unhealthy`"*, only for the process exiting. So any deafness signal has to make
the listener **exit**, the way `lobes_stalled` already exits 3, rather than
change a label.

## Faults found that are not about hearing

- **`grant` cannot back a container deployment** as `docker-compose.yml`
  documents it: `grant` 0.11.0's read path chmods the store dir
  (`grant/fs.py:47`), which raises `OSError` `EROFS` against the `/grant:ro`
  mount. The compose comment's "UNVERIFIED until the on-box drill" is now
  verified **false**. Worked around with the gitignored env file.
- **`wpctl` volume control fails in-container** (`startup_volume_failed`,
  `VolumeCommandError`) though it works on the host, so `louder` / `quieter`
  and `volume set --apply` are broken in the supported deployment. In-container
  `preflight audio_node` still passes, because it reads only the default sink.

## Reproducing

```bash
scripts/restart-proof.sh --wait-speech 120
```

Tier A is automatic and asserts the *actual* capture binding
(`pactl list source-outputs` resolved to a source name, compared to the
configured `mic_node`). Tier B needs one spoken phrase: the reSpeaker's AEC
cancels anything played through its own speaker, so a loopback proves nothing.

Both tiers carry negative controls — a bogus node name fails the audio
assertions, an unchanged transcript stamp fails the freshness assertion.
Tier B's first version accepted *any* non-null stamp and so could pass on a
stale one; that was the same proxy-for-property bug it existed to catch, and
it was corrected.

## What this grounds

| Frame | What it takes from here |
|---|---|
| `qwen-worker-selfsetup` | the pass record, the one-variable setup gap, the eval |
| `hearing-correctness` | the deafness fault, capture binding, `preflight`'s mic blindness |
| `actuation-behaviour-decisions` | open-loop IR, the already-in-state belief, thermal ground truth |
| `strict-window-close-boundary` | nothing — that fault was found by reading code, not by drilling |

## Not established

- Any failure **rate** for the audio boot race (n = 2, and the config changed
  between drills).
- That a reboot recovers hearing in general — drill 2 proves one instance.
- That the labelling bound holds in this room: the golden set has **never**
  been run against these acoustics, only against typed and synthesised input.

# The golden set

The golden set is how this repo answers its one load-bearing question: **did it
act on a command?** It is a committed manifest of Hebrew utterances with the
outcome each one expects, run against the *real* model on the box, by three
entrances, and scored by code CI exercises offline.

Approved deviation d4. Nothing here actuates anything: there is no `--apply`,
no actuator import, and no tool call. It measures, prints and exits non-zero.

## What is committed, and what is not

| Path | Committed | What it is |
|---|---|---|
| `manifest.jsonl` | yes | 275 rows: `id`, `text`, `category`, `subcategory`, `classes`, `intent`, `act_strict`, `act_weekday` (`null` = not checked) |
| `build_manifest.py` | yes | rebuilds `manifest.jsonl` from `tests/fixtures/corpus.jsonl` plus hand-written extras |
| `runner.py` | yes | the entrances, the scoring, the thresholds, the report |
| `thresholds.json` | yes | the pass/fail bar the exit code reflects |
| `asr_cache.json` | yes | what the ASR actually heard, per entrance — this is what lets CI reason about ASR-shaped text with no lobes |
| `audio/` | **no** (gitignored) | TTS wavs, regenerable; also, the voice weights have no licence to redistribute |
| `../fixtures/decider/golden_replay.json` | yes, once recorded | utterance → the real model's decision, so CI can replay a real measurement |

## The three entrances

1. **`text`** — the manifest text straight to the decider. What the model does
   with clean Hebrew.
2. **`audio-batch`** — the row's wav → `POST /v1/audio/transcriptions`
   (multipart, `language=he`) → the decider. This is the number that matters
   most: the classifier's real input is ASR output, and ASR errors are
   classifier inputs (CLAUDE.md).
3. **`audio-realtime`** — the same wav streamed through the **ears-only**
   `/v1/realtime` session plus `shabbos_goy.joiner`. The only entrance that can
   show one sentence arriving in two halves. A row here passes the hard rule
   only if **none** of the utterances it produced acts.

Plus a `tts` step that synthesises `audio/<id>.wav` with `POST /v1/audio/speech`
(`response_format: wav`), skipping files that already exist.

## Running it on the box

The gateway host and key come from the environment only — never from a tracked
file, and never committed:

```bash
export SHABBOS_GOY_LOBES_URL=ws://<lobes-host>:8001   # the box, not the mesh
export SHABBOS_GOY_LOBES_API_KEY=<gateway key>        # or GATEWAY_API_KEY
```

Then, from the repo root:

```bash
# 1. synthesise the audio once (writes tests/golden/audio/, gitignored)
python3 scripts/golden-set.py --entrance tts

# 2. entrance 1 — text straight to the model, both modes
python3 scripts/golden-set.py --entrance text --mode both --out /tmp/golden-text.json

# 3. entrance 2 — audio through the batch ASR path
python3 scripts/golden-set.py --entrance audio-batch --mode both --out /tmp/golden-batch.json

# 4. entrance 3 — audio through the ears-only realtime session + the joiner
python3 scripts/golden-set.py --entrance audio-realtime --mode strict --out /tmp/golden-rt.json

# everything, and record the model's answers for CI to replay
python3 scripts/golden-set.py --entrance all --mode both --record
```

`python3 -m tests.golden.runner …` is the same program. Useful flags:
`--decider replay|oracle` (offline), `--only <id>`, `--limit N`, `--json`,
`--audio-dir`, `--no-cache`.

The same three entrances are also reachable through pytest, behind the
`golden` marker:

```bash
uv run pytest -m golden -v     # skips with a named reason when the env is unset
```

The marker is excluded from the default run (`addopts = -ra -m "not golden"`),
so `uv run pytest -n auto` and CI never touch the gateway.

## Reading the numbers

The runner prints a table (and writes the same thing as JSON), per entrance and
mode, in this order:

* **HARD failures** — rows the manifest says must not act in strict mode, that
  acted. Printed first, with their ids. This is the headline metric and it is a
  *safety* number: one of these is a broken Shabbat for the user.
* **hint recall** — rows that should act, that acted *with the expected
  intent*. A quality number: a missed hint is cheap.
* **weekday command obedience** — in weekday mode, commands that were obeyed.
* **label acceptability** — how often the model's label was one of the row's
  acceptable `classes`. Informational: the golden set scores outcomes, not
  near-synonym labels.
* **latency p50/p95**, **decider failures** (timeouts, malformed answers — each
  one is a `NO_DECISION`, which never acts), and rows that produced no
  transcript at all.

The header names the **prompt version, the model and the lobes `/health` body**,
because a golden result that cannot say what produced it is not evidence.

## Thresholds and the exit code

`thresholds.json`:

| Threshold | Value | Meaning |
|---|---|---|
| `hard_false_positives_strict` | `0` | and it must stay 0 |
| `hint_recall_min` | `0.70` | quality bar, strict mode only |
| `decider_failure_rate_max` | `0.02` | timeouts / malformed answers |

Exit codes follow the repo CLI contract: `0` all thresholds met, `1` a user
error (a malformed manifest), `2` an environment error (no gateway
configured), `4` a threshold was missed.

**A HARD failure blocks a release.** It is not a quality regression to weigh
against other numbers; it is the invariant this project exists to enforce,
failing. Do not ship, and do not "tune" the threshold.

## When to re-run it

The golden set measures *one* combination of prompt, model and speech stack. It
must be re-run whenever any of them changes:

* `shabbos_goy/decider/prompt.py` (`PROMPT_VERSION`),
* the senses model or its serving config,
* the lobes version, the ASR model, or the TTS voice,
* the manifest itself (`build_manifest.py`).

Re-record with `--record` at the same time, so
`tests/fixtures/decider/golden_replay.json` keeps describing the model that is
actually deployed rather than a previous one.

## What CI does instead

CI never has a gateway, so it runs `tests/test_golden_manifest.py` and
`tests/test_golden_runner.py`:

* the manifest is well-formed — unique ids, a known vocabulary, and every
  `act_strict: false` row in a category that justifies refusing to act;
* the recorded run (if `golden_replay.json` exists) still passes the thresholds
  through the same scoring code;
* otherwise the manifest is walked through the deterministic rule oracle, which
  exercises the runner end to end without asserting anything about the rules'
  accuracy — they are a test oracle, not the product (deviation d2);
* the entrances themselves run against in-process fakes: `fake_batch_server.py`
  here for `/v1/audio/*`, `tests/lobes_fake_server.py` for the WebSocket and
  `tests/decider_fake_server.py` for the senses endpoint.

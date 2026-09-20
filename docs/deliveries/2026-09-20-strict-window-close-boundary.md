# Delivery Summary — strict window close boundary

plan: `strict-window-close-boundary` · run: `complete` · date: `2026-09-20`
baseline: `devague summary skeleton`

## Intent

Close the window-close boundary: a command spoken inside a strict window must
never act, even when the window shuts before the decision completes. Four
serial tasks, fanned out by `/assign-to-workforce`.

What the run actually became is larger than that. `t4`'s measurement found the
product's **release-blocking labelling threshold already failing** on
unmodified code, and the user chose to fix it before merging rather than ship a
red baseline. So this delivery also carries a prompt rewrite, a correction to
the golden set itself, and a fix to the repo's own release guard.

## Planned Work

Quoted verbatim from the `devague summary` skeleton:

- `t1` — Carry the utterance's speech-start instant from the joiner to the pipeline, and take the stricter mode when it is missing
- `t2` — Resolve the mode from the utterance's start instant instead of at decision time, failing toward strict when the two disagree
- `t3` — Assert all four boundary cases in one test, on a clock pinned to real zmanim instants for the configured location
- `t4` — Re-run the full golden set against the real model and hold `hard_false_positives_strict` at 0

## Actual Delivery

| Plan task | Status | What actually landed |
|-----------|--------|----------------------|
| `t1` | delivered | `SpeechStart(monotonic_ms, wall_time)` sampled at `speech_started`, pinned to the FIRST half of a pause-split utterance, exposed as `TranscriptJoiner.last_utterance_start`; a joiner-bypassed utterance forced to the strict column. Merged `a9d25a9`. |
| `t2` | delivered | `mode.stricter_mode(a, b)`; `Pipeline(mode_at=...)` resolving the mode at the speech-start instant and at decision time, stricter wins, clock distrust from either applies to both. Merged `c20caf8`. **Plus `60261c4`**, without which the fix was present in the code and **inert in production** — see Drift. |
| `t3` | delivered | Four boundary cases in one test on real computed zmanim for the fixture's Jerusalem config (window opens `2026-10-23 17:40:21+03:00`, closes `2026-10-24 18:34:04+03:00`), both edges anchored against the independent published vectors in `tests/fixtures/zmanim_sun_vectors.json`. Proved falsifiable by three negative controls. Merged `4a94653`. |
| `t4` | delivered, criterion amended | Measured on the live model across all three entrances. The original criterion was unachievable and was re-aimed (`d1`), then the underlying defect was fixed instead (`d3`). Final: `hard_false_positives_strict` **0 / 0 / 0**. Two quality violations ship recorded (`d5`). |

## Mid-work Decisions

- `d1` — `t4`'s criterion "`hard_false_positives_strict` is 0" was not achievable by this plan: the golden set on **unmodified** code (`c2efa53`, prompt `p2`) reported 14 hard false positives and 11 wrong actions. Re-aimed at a delta, with the absolute failure keeping its own blocking record (`r7`, `o1`, `e1` outcome=fail).
- `d3` — **user decision**: *"Why can't we fix it, then commit? If we merge and CI is red, we need to fix before merge anyway."* Inspection showed four **rule-level** gaps rather than row noise, so the prompt was fixed (`p2` → `p6`) and re-measured.
- `d4` — **user decision, domain authority**: *"קר לי מהמזגן → turn it off."* A wrong row in the golden set: it was `negative/mixed_command_hint` and must act, since cold maps to `warm` = AC power off. The model wanting to act on it was **right and the manifest was wrong**. A prompt rule written to satisfy the wrong row was reverted — it would have taught the model to ignore a legitimate hint.
- `d5` — **user decision**: *"We can continue with both. open follow up."* Ship with `wrong_actions(text/strict)=1` and `hint_recall(audio-batch)=0.692`.
- `d2` — **proposed, not yet approved**: `t4`'s measurement was launched before `t3` merged. The condition attached to it (that `t3` stay test-only) held — `t3`'s diff is 167 lines in one test file, zero product files.
- Not covered by any deviation record: the **`recent:` context window was narrowed 900 s → 120 s** at the user's instruction, because with `p6` able to let a fragment inherit state, the window length is the blast radius of a wrong inheritance.
- Not covered by any deviation record: `r13` — the recorded golden fixture was **keyed by transcript text**, so a later entrance overwrote an earlier one and a run that FAILED the text entrance produced a fixture that PASSED. Fixed to key by entrance; merging entrances back together is now refused rather than guessed.

## Drift From Plan

| Plan item | Reason for divergence | Classification |
|-----------|-----------------------|----------------|
| `t4` (`d1`) | the release-blocking threshold was already failing before this plan's first line of code existed; the criterion was re-aimed at a delta rather than silently relaxed | `needs-follow-up` |
| `t4` (`d3`) | the labelling defect was fixed inside this delivery rather than deferred, because CI must be green before merge either way | `risky` |
| `t4` (`d4`) | a row in the product's own evidence was wrong; corrected at source in `tests/fixtures/corpus.jsonl` | `risky` |
| `t4` (`d5`) | two quality violations ship knowingly; `hard_false_positives_strict` is 0 on all three entrances, which is the release-blocking metric | `needs-follow-up` |
| `t2` | the plan's contract did not mention the listener. The boundary fix was **inert in the deployed build** — `mode_at` is optional and falls back to decision-time-only resolution, and every test still passed because tests construct a `Pipeline` directly with their own resolver. Corrected in `60261c4` with a test asserting the listener supplies it. | `acceptable` |
| `t1` | acceptance criterion 2 asked for "the stricter of the start-time and decision-time modes" in exactly the case where the start time is **unknown**, so its own terms do not apply. Resolved as worst-case strict. Criterion defect is mine, filed as lapse `l1`. | `acceptable` |

## Evidence

- tests: `1802 passed, 2 skipped` (`uv run pytest -n auto -q`) at `1a12a41`
- tests: `tests/test_pipeline.py::test_the_window_boundary_is_judged_from_the_speech_start_instant_on_real_zmanim` — pass, **and proved falsifiable by three negative controls** (decision-time reading wins → FAILED with `class=imperative intent=cool verdict=acted action=ac_power_on`; bypass the start-instant reading → FAILED identically; always-strict → FAILED the acts-case)
- tests: `tests/test_pipeline.py::test_a_command_begun_inside_the_window_is_refused_when_decided_after_it_closed` — pass
- tests: `tests/test_pipeline.py::test_a_command_begun_after_the_window_closed_still_acts` — pass
- tests: `tests/test_pipeline.py::test_a_joiner_bypassed_utterance_with_no_start_instant_forces_the_strict_column` — pass
- tests: `tests/test_joiner.py::test_emitted_utterance_carries_its_speech_start_instant` — pass
- tests: `tests/test_listen_runtime.py::test_the_listener_hands_the_pipeline_a_start_instant_mode_resolver` — pass
- tests: `tests/test_golden_runner.py::test_one_entrance_never_overwrites_another_for_the_same_transcript` — pass (the `r13` regression)
- lint: `black --check` / `isort --check-only` / `flake8` / `bandit -c pyproject.toml` — all clean
- lint: `markdownlint-cli2` — 0 errors; `scripts/scan-secrets.py` — clean, 259 files
- golden, prompt `p2`, 295 rows, live model: hard false positives **11 / 11 / 12** (text / audio-batch / audio-realtime), recall 0.969 / 0.769 / 0.769
- golden, prompt `p6`, 234 `k-` rows, live model: hard false positives **0 / 0 / 0**, recall 0.821 / 0.692 / 0.744
- golden, controlled `p2`-vs-`p5` on identical rows: 10 → 0 hard false positives, recall 0.974 → 0.821
- golden, held-out 61 `g-` rows under `p5` (never tuned on): hard false positives 0, wrong actions 0, `violations: NONE`
- deployment verified **inside the running container**: `PROMPT_VERSION=p6`, context window `120`, `Pipeline` accepts `mode_at`, `preflight` 8/8, `pw-record` bound to the configured `mic_node`
- commits: `d38aff3..1a12a41`
- records: `d1`–`d5`, `r1`–`r17`, `o1`–`o6`, `e1`–`e9`, `b1`–`b5`, `l1`–`l3`

## Delivery Claims

| Claim | Confidence | Evidence |
|-------|------------|----------|
| a command begun inside a strict window is refused however late the decision lands | high | test `test_the_window_boundary_is_judged_from_the_speech_start_instant_on_real_zmanim` at commit `4a94653`, falsifiable under three negative controls |
| the boundary fix is live in the deployed build, not merely present in the code | high | test `test_the_listener_hands_the_pipeline_a_start_instant_mode_resolver`; `mode_at` verified present inside the running container |
| fail-toward-strict is an explicit rule a refactor cannot silently reverse | high | `mode.stricter_mode`, checked across all seven input combinations; commit `c20caf8` |
| an utterance with no speech-start instant takes the strict column | high | test `test_a_joiner_bypassed_utterance_with_no_start_instant_forces_the_strict_column`, paired with its acts-case |
| the recorded golden fixture can no longer hide a failing entrance | high | test `test_one_entrance_never_overwrites_another_for_the_same_transcript`; commit `1a12a41` |
| prompt `p6` eliminates the strict-mode hard false positives `p2` produced | medium | `e8` (controlled `p2`-vs-`p5`, 10 → 0), `e9` (`p6`, 0 / 0 / 0). **Capped at medium**: one run per condition of a grader shown to vary at `temperature: 0` — two rows have flipped between runs (lapse `l2`) |
| `hard_false_positives_strict` is 0 on all three entrances | medium | `e9`. Capped for the same reason: one draw, not a bounded property. The 61 held-out rows were measured under `p5`, not re-measured under `p6` |
| the `recent:` context rule improves recall on ASR-fragmented input | **unverified** | `runner.py:786` hands every utterance an **empty** `ContextWindow` by design, so the golden set cannot see this rule at all. The recall change must not be attributed to it (lapse `l3`, risk `r14`) |
| the agent hears on the current boot | **unverified** | Tier A mechanical checks pass (node present, `pw-record` bound, AEC pair intact) but no transcript has arrived since the 17:02 restart. Quiet and deaf are indistinguishable; needs one spoken phrase (Tier B) |
| the golden thresholds are met | **unverified — false as stated** | two violations ship knowingly under `d5`: `wrong_actions(text/strict)=1`, `hint_recall(audio-batch/strict)=0.692 < 0.70` |

Lapse ledger evidence (all three **proposed**, pending approval — not yet evidence):

| Lapse | Code | What |
|---|---|---|
| `l1` | `provenance-missing` | `t1`'s acceptance criterion 2 was not computable in the case it governed |
| `l2` | `n-below-claim` | a delta criterion installed with one run per condition of a nondeterministic grader |
| `l3` | `assumption-for-measurement` | attributed a recall improvement to the context rule, which the instrument cannot see |

## Remaining Work / Follow-up

- `r14` **(blocking)** — the `recent:` context rule that is now live in the room is **unmeasured**. The golden set passes an empty context window per utterance by design. Needs a **conversational entrance**: ordered utterance pairs whose second is a fragment. Until then `p6`'s context behaviour is a reasoned design, not evidence.
- `r16` — bound the run-to-run variance before any number here is read as a property. `k-n_fragment_13` and `k-h33` have each flipped between runs at `temperature: 0`; the cause is vLLM batching, not sampling. Three runs, reporting a range.
- `r17` — record a committed fixture from a **full** 295-row three-entrance run under `p6`. The measurement that authorised the deploy covered 234 rows and was not recorded, so the guard still skips.
- `r15` — recover `hint_recall(audio-batch)` from 0.692 to ≥ 0.70, about one row.
- `r10` — rule on `k-h33`: the row expects `louder`, but `louder` moves **the agent's own volume**, which cannot help anyone hear a radio. Either the intent is wrong or the whitelist cannot serve the utterance.
- `r11` — `k-c45_06` and `g-adv-03` are the same utterance under two ids, so 295 rows are not 295 independent observations.
- `r12` — `wrong_actions` is documented "in any mode" but enforced strict-only (`runner.py:384`), hiding 23–49 weekday wrong actions. On a weekday those actuate the AC for real.
- `r8` — `Pipeline` has no public seam for the joiner's wall clock; `t3`'s test assigns the private `_wall_clock`.
- `r2` / `v5` — the golden set has still **never** been run against this room's acoustics. Every number here is typed or synthesised input.
- Deliberately not built: `deaf_after_seconds` (specified, default OFF, must make the process **exit**), and the host-side WirePlumber unit that is the other half of unattended deaf recovery.

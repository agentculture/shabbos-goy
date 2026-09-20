# strict window close boundary

> a command spoken inside a strict window never acts, even when the window closes before the decision does
> instruction: Implement in pipeline.py: carry the utterance's `speech_started` timestamp into the mode decision.

## Audience

- the household speaking Hebrew in the room during the minutes around a window boundary -- candle lighting and tzeit -- who do not know a boundary is near and are not adjusting their speech for it. Secondarily whoever audits the core invariant, for whom this frame is the record that the boundary was examined rather than assumed.
  - instruction: Write the tests from the speaker's position, not the clock's: an utterance begun at 19:12:59 and decided at 19:13:01.

## Before → After

- After: a command's verdict is fixed by the mode in force when its speech STARTED, so the refusal a strict window promises holds for every utterance the window contained, regardless of when the decision completed. Where the start-time mode and the decision-time mode disagree, the strict reading wins. No clock margin, sleep or grace period is introduced -- the timestamp comes from the joiner's `speech_started`, which already exists.
  - instruction: pipeline.py must take the mode from a timestamp carried on the utterance, not from a fresh `resolve_mode`() at decision time. The joiner supplies it.

## Why it matters

- this is the only finding from the 2026-09-20 pass that bears on the product's single invariant -- no spoken command acts in strict mode -- so it is not an ergonomics issue and does not belong in a frame about operator convenience
  - instruction: Do not widen this frame. If a change would affect labelling rather than mode resolution, stop and re-run the golden set.

## Requirements

- MIGRATED from qwen-worker-selfsetup/c51, elevated there from an assumption. Mode is resolved at DECISION time (pipeline.py:593-600, 'mode or strict') and `resolve_mode` carries no margin, so a command spoken INSIDE the holy day at 19:12:59 and decided at 19:13:01 is judged weekday and ACTS. The window-OPEN boundary fails safe the other way; the 15s strict delay does not help because the mode has already flipped
  - instruction: Re-read pipeline.py:593-600 before changing it; the 'mode or strict' default is the line that fails open here.
  - honesty: the cited behaviour is re-read against the code at implementation time: pipeline.py:593-600 and `resolve_mode` carrying no margin. If either has moved, the citation is corrected before the fix is written rather than after
- a decision whose utterance BEGAN inside a strict window is judged by the window in force when speech STARTED, not when the decision completed; the joiner already tracks `speech_started` to `speech_stopped`, so the timestamp needed is in hand
  - instruction: Thread joiner `speech_started` through to the pipeline's mode decision; where it is missing, take the stricter mode.
  - honesty: `speech_started` is actually available on the utterance the pipeline receives. If the joiner drops it, threading it through is part of this work and not a separate frame -- and if it is unavailable for a reconnect-orphaned utterance, that utterance takes the stricter of the two modes

## Honesty conditions

- the refusal is demonstrated on a clock pinned across a real tzeit boundary, not by asserting the helper function in isolation -- the 2026-09-20 pass found this bug by reading pipeline.py:593-600, so a test that does not exercise that line proves nothing about it
- the fix touches the mode-resolution path only. If a change here would alter which utterances are LABELLED commands, it is out of this frame and the golden set must be re-run -- this frame moves the boundary, never the labelling
- fail-toward-strict is implemented as the explicit rule, not as an accident of ordering: a test asserts that an utterance spanning a boundary in EITHER direction is judged strict, so a later refactor cannot silently reverse it
- the boundary tests name real clock instants for the configured location rather than synthetic offsets, so a zmanim change is visible as a test failure instead of passing against an arbitrary number
- no sleep, margin or grace period is introduced -- the fix is a different TIMESTAMP, not a delay; a margin would trade one silent boundary error for another
- both halves of each pair are asserted in the same test, so a future change cannot delete the acts-case and leave a refuses-case that passes vacuously
- the golden set is actually re-run against the real model, not assumed unaffected -- 'the change is only about timing' is precisely the kind of reasoning the 2026-09-20 pass found wrong four times

## Success signals

- a test that pins the clock across tzeit and asserts a command whose speech began inside the window is REFUSED while the same command begun two seconds after the window is ACTED on -- the pair, not either alone, because passing only the first is satisfiable by always refusing. Plus the mirror pair at the window-OPEN boundary, which must keep failing safe as it does today.
  - instruction: Four cases, one test: command begun inside/decided outside -> refused; begun outside/decided outside -> acts; begun outside/decided inside (open boundary) -> refused; hint in all four -> unaffected.
- 4 of 4 boundary cases pass, and 0 of the golden set's strict-mode hard false positives appear -- the release-blocking number stays 0 across all 295 rows after the change, because moving the boundary must not move the labelling.
  - instruction: Run the 4-case boundary test plus the full 295-row golden set; `hard_false_positives_strict` must be 0.

## Scope / boundaries

- fail toward the stricter column, consistent with the clock-trust rule: where the two boundaries disagree, the strict reading wins
  - instruction: Assert fail-toward-strict explicitly in a test for both boundary directions.

## Assumptions

- the exposure is narrow in time -- seconds around tzeit -- but it is exactly when a household is most likely to be speaking about the AC, so rarity is not a reason to leave it

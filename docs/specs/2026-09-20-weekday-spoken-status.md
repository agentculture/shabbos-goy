# weekday spoken status

> shabbos-goy answers a status question aloud on a weekday, through whatever voice lobes advertises
> instruction: Read the voice from /capabilities at runtime; advertise nothing usable means stay silent.

## Audience

- someone in the room on a WEEKDAY who asks the agent what the AC is doing. Nobody on Shabbat, Yom Kippur or Yom Tov is in this audience by construction -- strict mode says nothing at all.
  - instruction: Test the same status utterance in both modes: speech in weekday, silence in strict.

## Before → After

- After: a weekday status question gets a spoken Hebrew answer: the listener passes a speak callable instead of defaulting to None, the transport reaches whatever tts lobes advertises, and pipeline.py:851-870 fires the literals that already exist. No engine, voice or rate is chosen here and no config key for them is added.
  - instruction: A TTS failure must not take down the ears; wrap the speak path so the listener survives it.

## Why it matters

- the capability table advertises status-to-speech today while the path is dead -- listener.py:459 defaults speak=None and cli/`_commands`/listen.py never passes one -- so the product currently claims a capability it does not deliver. The honest choices are to connect it or withdraw the claim; c3 chooses connect.
  - instruction: Reconcile the capability table with the code in the same PR.

## Requirements

- MIGRATED and CORRECTED from qwen-worker-selfsetup/c62. The capability table advertises status-to-speech and the path is dead in practice, but an implementation EXISTS: pipeline.py:144-148 holds the Hebrew literals and pipeline.py:851-870 is a working weekday-gated `_speak_status`. What is missing is the wiring and the transport -- listener.py:459 defaults speak=None, cli/`_commands`/listen.py never passes a callable, and no call to /v1/audio/speech exists. Until it is wired, actions must not advertise it
  - instruction: Wire listener.py:459 and cli/`_commands`/listen.py to pass a speak callable; do not rewrite pipeline.py:144-148 or 851-870.
  - honesty: the existing `_speak_status` and its literals are used as they are; if the wiring tempts a rewrite of the speech text, that is a separate frame, because the literals are what the no-question-mark test guards
- MIGRATED from qwen-worker-selfsetup/h43. Runtime invisibility must not extend to the evidence: golden-set reports keep recording the stack they ran against, so a silent lobes change makes a past measurement visibly stale afterwards rather than undetectably wrong
  - instruction: Golden-set reports record the stack identifiers read from the live gateway at run time.
  - honesty: the golden-set report records the stack identifiers it actually read from the gateway at run time, not values copied from a config file

## Honesty conditions

- 'through whatever voice lobes advertises' is read from /capabilities at runtime, and when it advertises nothing usable the path stays silent rather than falling back to a guess that produces wrong-sounding Hebrew
- 'invisible to us' is tested as silence, not as an exception: an advertised field that changed shape must leave the agent mute and logging, never crashing the listener that is also the ears
- the /capabilities payload is re-read against the live gateway before this is built -- the claim was written from one reading on 2026-09-20 and the API is not ours
- the weekday-only restriction is tested from the audience's side: the same status utterance in each of strict and weekday, asserting speech in one and silence in the other
- wiring the speak callable does not change the listener's failure behaviour -- a TTS failure must never take down the ears, which are the product
- the capability table and the code agree after this frame lands, checked rather than assumed, since the mismatch is what the 2026-09-20 pass found
- the no-question-mark test across the package is re-run, because connecting speech is exactly when a helpful-sounding prompt gets added
- the 0-crashes number is measured with the TTS transport failing mid-session, not merely absent at startup, because the ears must survive a speech failure that happens while someone is talking

## Success signals

- a weekday status utterance produces one spoken Hebrew phrase from the existing literals, and the same utterance in strict mode produces silence -- the pair. The no-question-mark test across the package keeps passing, and a report records which lobes stack the speech ran against.
  - instruction: Re-run the package-wide no-question-mark test.
- 1 spoken Hebrew phrase for a weekday status utterance and 0 for the same utterance in strict mode, 0 question marks anywhere in the package, and 0 listener crashes across an injected TTS failure.
  - instruction: Assert the counts: 1 weekday utterance, 0 strict, 0 question marks, 0 crashes on injected TTS failure.

## Scope / boundaries

- MIGRATED from qwen-worker-selfsetup/c65. We use what lobes advertises, and if it changes it is invisible to us: no engine, voice or rate is chosen here, no config key exists for any of them, and no mismatch check is added. The 24000 constant survives only as the fallback when nothing is advertised
  - instruction: Add no config key for engine, voice or rate. Keep 24000 only as the fallback when nothing is advertised.

## Assumptions

- what /capabilities advertises for tts today is model, runtime, path and language -- no sample rate, no voice, no engine -- so 'read the rate from the advertised capabilities' currently has nothing to read and the 24000 fallback would be the only path. This needs checking against the API before it is built

## Decisions

- MIGRATED from qwen-worker-selfsetup/c64. Connect it rather than withdraw it, but it is NOT relevant to strict mode and not window-blocking: strict mode says nothing at all, so the path can never fire on Shabbat, Yom Kippur or Yom Tov and cannot touch the core invariant

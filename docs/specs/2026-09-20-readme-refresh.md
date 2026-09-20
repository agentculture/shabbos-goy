# readme refresh

> shabbos-goy's README is a hundred-odd functional lines that lead with what it does and who holds what
> instruction: Diff the rewrite against the current README for removed safety copy before committing.

## Audience

- someone meeting shabbos-goy for the first time who must decide in one screen whether it is for them -- and the operator returning to set it up, who needs the steps and not the reasoning. Also the Qwen-backed worker, whose only context is the README, help and doctor.
  - instruction: Judge the README by what the worker can act on from README + help + explain overview + doctor alone.

## Before → After

- After: the README opens with what the agent announces about itself, then the invariant table, then the setup steps -- functional, announcement-first, little to no prose, in the shape of ../devague's README. It still says plainly that there is no hechsher and that users should ask their own rav, still says the whitelist is the only control in weekday mode, and now names the one environment variable that took preflight from 6/8 to 8/8.
  - instruction: Include `SHABBOS_GOY_LOBES_URL` with a placeholder host, never a real one.

## Why it matters

- the README is the worker's entire context in the eval: whatever it cannot answer from the README, help, explain overview and doctor becomes a question to a human, which is the failure this project is measuring. Prose that explains rather than instructs is what a worker cannot act on.
  - instruction: Test the prose against the four eval intents, not against a style preference.

## Requirements

- MIGRATED from qwen-worker-selfsetup/c61. Refresh README.md to the functional, announcement-first shape ../devague/README.md models -- 119 lines, six sections, imperative voice, commands in code blocks, near-zero explanatory prose -- against today's 323 lines across nine sections including an 8-step prose setup checklist whose operative content is one env var and a config file
  - instruction: Announcement-first is judged by the first screen's actual contents; it must claim no capability the code lacks.
  - honesty: announcement-first is measured by what the first screen actually contains, and nothing in it describes a capability the code does not have -- the 2026-09-20 pass found the capability table advertising a dead speech path
- MIGRATED from qwen-worker-selfsetup/h40. Every command the refreshed README shows is executed and its output matches, documenting the path actually verified on this box: the single `SHABBOS_GOY_LOBES_URL`, grant-or-env secrets, docker compose up
  - instruction: No real host, key, pod id or tailnet address in any setup step; grep before committing.
  - honesty: no setup step in the README names a real host, key, pod id or tailnet address -- the 2026-09-20 pass leaked two of those into an exported spec, and scan-secrets.py does not catch them

## Honesty conditions

- the rewrite is diffed against the current README for REMOVED safety copy before it is committed -- no hechsher, ask your rav, the whitelist is the only control, `TTS_DEBUG_TEXT` must be off -- and any loss is restored rather than rationalised
- ../devague's README is read as the model at the time of writing rather than recalled, since it is a moving target in another repo
- every command the README tells a reader to run is run once as written on this box before the PR opens
- the worker's context is tested as the worker sees it -- README plus help plus explain overview plus doctor -- not as a human who also read the spec
- the one environment variable that took preflight from 6/8 to 8/8 appears with its name and a placeholder value, never a real host
- 'prose a worker cannot act on' is judged against the actual eval intents, not against a style preference
- the safety-copy check is a diff, not a reading: removed lines are enumerated before the PR opens

## Success signals

- the setup section is executable top to bottom on a fresh box with no prose skipped, and the no-hechsher paragraph plus the weekday-whitelist paragraph are still present verbatim in intent -- checked, because a rewrite is exactly when safety copy gets lost. markdownlint-cli2 clean.
  - instruction: Enumerate removed lines in a diff before opening the PR.

## Scope / boundaries

- MIGRATED from qwen-worker-selfsetup/h39, and it is the hard constraint on brevity: three statements must survive verbatim and prominently -- that there is NO rabbinic approval and users must ask their own rav, that weekday mode obeys anyone in earshot with the whitelist as the only control, and the strict-versus-weekday refusal table. No endorsement-sounding copy may appear anywhere
  - instruction: Read ../devague/README.md at writing time rather than recalling its shape.
- depth the README sheds moves into per-topic 'shabbos-goy explain' catalog entries rather than being deleted, so the agent-first surface gains what the prose loses
  - instruction: Run every command the README tells a reader to run, once, as written, before the PR opens.

## Assumptions

- the refusal table alone is about 45 lines of the current README (58-102), so a 119-line target is tight once the three untouchable statements are kept -- the line count is a model to aim at, not a requirement to hit

# Build Plan — lobes-driven AC power agent

slug: `lobes-driven-ac-power-agent` · status: `exported` · from frame: `lobes-driven-ac-power-agent`

> shabbos-goy listens through the lobes Hebrew realtime session (ears-only), and in Shabbat / Yom Kippur mode switches the AC power on or off only when it infers the wish from indirect speech; it can report AC status, stays quiet by default with an adjustable own-volume, and survives reboots unattended

## Tasks

### t1 — Policy table: utterance classes, modes and the mode x class gate

- instruction: Pure stdlib, no I/O. This table is the single source every other module and both UIs call; do not duplicate the rule anywhere else.
- covers: c19, h16
- acceptance:
  - `shabbos_goy`/policy.py defines the classes (imperative, request, rebuke, remark, wish, discomfort, unrelated), the modes (weekday, strict) and one `may_act`(mode, klass) function; tests/`test_policy.py` asserts every cell: weekday acts on all but unrelated, strict acts only on remark/wish/discomfort
  - an unknown class or mode returns False (never acts) and a clock-untrusted flag selects the strict column

### t2 — JSON config and whitelist loader that fails closed

- instruction: Stdlib json only (pyyaml is dev-only). Ship tests/fixtures/config.example.json with placeholder values; never a real pod id, host or key.
- covers: c3, h3
- acceptance:
  - `shabbos_goy`/config.py loads JSON from the XDG config dir (path overridable by env and flag); tests/`test_config.py`: removing a tool or pod id removes it from the effective whitelist with no code change
  - a missing, unreadable or malformed config yields an empty whitelist and a named CliError for CLI callers; the only representable AC argument is power on|off, anything else is a validation error
  - config carries: location lat/lon/timezone, candle-lighting offset, tzeit definition, israel|diaspora, pod ids, rate limits, strict-mode delay, volume level and step bounds, join gap, ring sizes, dashboard bind address

### t3 — Stdlib zmanim: sunset, candle lighting, tzeit, Hebrew calendar, holy-day windows

- instruction: NOAA solar position formulas for sunset; candle-lighting offset and tzeit (fixed minutes or depression angle) come from config. No third-party packages. Keep calendar and sun maths in separate files.
- covers: c38, h31
- acceptance:
  - `shabbos_goy`/zmanim/ computes in UTC from lat/lon and converts with an explicit IANA timezone via zoneinfo; tests pass with TZ=UTC
  - test vectors for at least 3 locations and for dates on both sides of 2026-10-25 agree with a published source to within 2 minutes; the vectors file cites its source
  - Hebrew calendar conversion yields correct Gregorian dates for Yom Kippur and every Yom Tov for 5786-5800 (vector-tested), honours israel|diaspora second days, and `windows_for`(date) returns strict windows for Shabbat, Yom Kippur and Yom Tov, merging adjacent days

### t4 — Transcript joiner for pause-split sentences

- instruction: Pure state machine over events with `at_ms`; flush on timeout using an injected clock so tests need no sleeping.
- covers: c7, h6
- acceptance:
  - `shabbos_goy`/joiner.py joins transcripts whose `speech_stopped`.`at_ms` to next `speech_started`.`at_ms` gap is below the configured threshold; tests/`test_joiner.py`: a split hint classifies as one utterance
  - a dangling half never reaches the classifier as actionable: not when the second half never arrives, not after a reconnect (`at_ms` restarts), and empty-text transcripts are ignored; buffer size is bounded

### t5 — Sensibo adapter: argv-locked power control and zero-write status read

- instruction: subprocess.run with list args, no shell. Never log the pod id at info level. Do not import the sensibo package.
- covers: c2, h2, c20, h17
- acceptance:
  - `shabbos_goy`/actuators/sensibo.py builds argv only from a closed set; tests enumerate every producible argv and assert the only sensibo flags are --power on|off, --apply and --json
  - status() never passes --apply (asserted), derives on/off from the dry-run changes diff, merges temperature/humidity from sensibo read, and returns unknown on any non-zero exit; it is one function so sensibo-cli#15 can replace it
  - power(on|off, apply=False) is dry-run by default, treats any non-zero exit as did-not-act, uses a subprocess timeout above sensibo's own backoff, and is tested with a fake sensibo executable on PATH

### t6 — Rate limits, strict-mode delay, bounded retry and bounded context

- instruction: Injected clock everywhere; no sleeping in tests. No persistence of any kind.
- covers: c32, h25, c35, h28, c29, h22
- acceptance:
  - `shabbos_goy`/limits.py: minimum interval between power changes and a daily cap, both from config, in memory only; a refused action is logged and never replayed
  - a delayed-action timer (strict mode, default 15 s) and a bounded retry window for failed actuations live in memory and are provably empty after a restart (no file written)
  - a BoundedRing type caps every buffer the agent holds; a soak test feeds a synthetic 25h+ event stream through ring, joiner-sized buffers and limiter using an injected clock and asserts sizes stay under their bounds and a fresh hint still passes

### t7 — Stdlib lobes realtime client: ears-only, reconnecting, watchdog

- instruction: Host and key come from env only. Keep the wire layer (ws.py) separate from the session logic (client.py).
- covers: c5, h4, c6, h5, c45, h33, c31, h24
- acceptance:
  - `shabbos_goy`/lobes/ holds a stdlib RFC 6455 client cited from lobes-cli scripts/realtime-smoke.py (recorded in docs/skill-sources.md or a CITATION comment); tests run against an in-process fake WebSocket server
  - answers PING with PONG; never emits response.create or tool declarations (asserted on every frame sent); drops empty-text transcripts; every event shape and error code from lobes site/src/scripts/event-fixtures.ts is replayed (cited into tests/fixtures/) and an invented event type does not crash the loop
  - server killed mid-turn: reconnects with capped backoff, the interrupted turn yields no action, nothing is written that a restart could replay; a 401 is a named environment error with backoff, not a crash loop
  - server frozen (socket open, no frames): the watchdog makes the process exit non-zero within its window; audio frames keep flowing while a playback-active flag is set

### t8 — PipeWire audio adapter: capture, playback, own volume

- instruction: Per decision c42 read every amixer/aplay mention in older honesty conditions as its PipeWire equivalent. Mirror `normalize_pipewire_device_name` from lobes realtime-he-accept.py and cite it.
- covers: c8, h7
- acceptance:
  - `shabbos_goy`/audio/pipewire.py builds pw-record and pw-play argv targeting the reSpeaker node by name, refuses a mic/speaker pair on different devices, and gets/sets/mutes volume through PipeWire; all tested with fake pw-\* executables
  - the configured level is re-applied at start-up, volume steps are clamped to config bounds, and the default configuration plays nothing

### t9 — Extend scan-secrets to realtime URLs and non-JSON config

- instruction: Keep the existing checks unchanged; add an allowlist for documentation placeholders written in angle brackets.
- covers: c12, h10
- acceptance:
  - scripts/scan-secrets.py flags a tracked non-localhost ws:// or wss:// URL and host-like values in YAML, env-example and Markdown code blocks; tests/`test_scan_secrets.py` covers a positive and an allowed-localhost case
  - the repo as committed still scans clean

### t10 — Hebrew classifier and fixture corpus

- instruction: Rules and lexicon first (deterministic, testable, zero deps): normalise niqqud and final letters, detect imperative and future-second-person verb forms, request frames, rebuke frames, wish frames and state adjectives. Include ASR-error variants seen in lobes docs. Keep the interface narrow so a model-backed classifier could be swapped in later behind the same function.
- depends on: t1
- covers: c16, h13, c34, h27
- acceptance:
  - `shabbos_goy`/classifier/ returns class, inferred intent (cool, warm, louder, quieter, status, none) and a confidence; in doubt the class is unrelated
  - tests/fixtures/corpus.jsonl is committed with at least 60 command fixtures (imperative, request, rebuke), hint fixtures for hot and cold, and at least 10 fixtures in each negative category: negation, question to a person, reported speech, other tense, third-party audio, learning/davening text
  - table-driven tests: strict mode acts on 0 command fixtures and 0 negative fixtures; hint recall on typed text is at least 70 percent; no code path produces a question as spoken output

### t11 — Mode resolver: zmanim-computed mode, in-memory override, clock trust

- instruction: Clock trust: check NTP sync via timedatectl when available, else a sanity floor on the year; inject the check for tests.
- depends on: t2, t3
- covers: c18, h41, c56, h40
- acceptance:
  - `shabbos_goy`/mode.py returns strict inside any zmanim window and weekday outside; an untrusted clock or missing location returns strict
  - one shared `set_override` function serves CLI and dashboard: both can force strict and can set weekday inside a simulated window; the override is memory-only, and a new process computes the zmanim mode with no file holding the override

### t12 — Decision pipeline: transcript to action, end to end on fixtures

- instruction: Everything actuating stays dry-run unless apply=True is passed down from the CLI. Wrap the utterance handler so exception messages are replaced by a type name before logging.
- depends on: t1, t2, t4, t5, t6, t8, t10, t11
- covers: c1, h1, c17, h14, c33, h26, c37, h30
- acceptance:
  - `shabbos_goy`/pipeline.py wires joiner, classifier, mode gate, whitelist, limits and the sensibo/volume adapters; tests drive it from fixture events with no microphone, lobes server or Sensibo account
  - hot hint powers on, cold hint powers off, already-in-state does nothing and says nothing; weekday volume imperative changes volume, strict mode ignores it, a strict-mode loudness remark lowers it
  - transcripts overlapping own playback plus a tail are discarded while a later genuine hint still acts
  - log lines carry class, intent, gate verdict and action only; a forced exception with a marker transcript leaks the marker to neither stdout nor stderr; recent utterance text goes only to the in-memory ring

### t13 — Domain CLI verbs, preflight, explain catalog and learn rewrite

- instruction: One file per verb under `shabbos_goy`/cli/`_commands`/. mode set talks to a running listener through its local control endpoint; with no listener it explains that overrides are memory-only.
- depends on: t12
- covers: c13, h11, c36, h29
- acceptance:
  - verbs classify, zmanim, actions, preflight and nouns ac (status, power), volume (get, set), mode (show, set), each noun with an overview sub-verb, nested parsers using `parser_class`=type(p), all supporting --json
  - a test walks the built parser and fails on any verb or noun without an explain catalog entry; catalog root and learn text describe the domain; teken cli doctor . --strict passes
  - ac power and volume set change nothing without --apply; preflight exits 0 only if lobes health and key, Sensibo key and pod, audio node, clock sync and location all pass, exits 2 naming the failed check, never actuates, and prints the next strict window in local time

### t15 — Tailscale-only stdlib dashboard with controls and bug context

- instruction: Single static HTML page plus JSON endpoints, no build step, no external assets. Use the lobes site event-stream page as the visual reference only.
- depends on: t11, t12
- covers: c46, h34, c50, h35, c51, h36
- acceptance:
  - `shabbos_goy`/web/ serves with http.server only: mode and next window, connection state, AC status, recent utterances with text, class, intent, gate verdict, action, timings, and errors; controls for AC power, volume, mode and preflight call the same whitelist, limits and adapters as voice
  - refuses to bind 0.0.0.0 or a non-tailnet address unless config explicitly allows; GET never changes state; POST with a foreign Origin is refused; no response contains either key (marker test)
  - marker transcripts appear in the recent-utterances endpoint and in no log stream or file; the ring never exceeds its size and is empty after a restart

### t17 — ASR-transcribed benchmark of the corpus

- instruction: Audio source is an open risk: start with TTS-synthesised audio via POST /v1/audio/speech, then replace with operator recordings. Never commit household recordings of other people.
- depends on: t10
- covers: c28, h21
- acceptance:
  - a script or pytest marker transcribes corpus audio through lobes POST /v1/audio/transcriptions and caches transcripts under tests/fixtures/asr/; the default test run uses the cache and needs no server
  - CI asserts strict mode acts on 0 of at least 60 ASR-transcribed command fixtures and reports hint recall against the 70 percent threshold; thresholds live in a committed file

### t14 — listen verb: the ambient runtime loop

- instruction: Threads: mic feeder, reader, pipeline worker, dashboard. Shared state only through the pipeline queue and the bounded ring; document the locking.
- depends on: t7, t8, t12, t13, t15
- covers: c52, h37
- acceptance:
  - shabbos-goy listen starts capture, the lobes client, the pipeline and (if configured) the dashboard thread; --script with a WAV or an events file runs the same loop with no hardware; dry-run unless --apply
  - with the dashboard address unavailable the listener still classifies and acts and the bind is retried; an exception in a dashboard handler does not stop the loop
  - a heartbeat on tmpfs or a local health endpoint reflects recent pong/audio/transcript activity for the container healthcheck

### t16 — Docker packaging: image, Compose service, healthcheck

- instruction: Decide host networking versus a published port bound to the tailnet address and record why in the compose file comments; the listener must start even if that address is not up yet.
- depends on: t14, t15
- covers: c10, h8
- acceptance:
  - Dockerfile (python:3.12-slim, non-root uid matching the host user, pinned sensibo-cli, pipewire client tools) and docker-compose.yml (restart: unless-stopped, json-file 10m x 3, ro config mount, `env_file`, PipeWire socket mount, host-gateway to the lobes port, dashboard reachable on the tailnet address only)
  - docker compose config validates in CI; docker/shabbos-goy.env.example is committed and the real env file is gitignored; the healthcheck goes unhealthy when the heartbeat is stale

### t18 — Docs: re-scope the invariant to speech, halacha doc, five prompt files, version bump

- instruction: Write for both readers named in c25: the household and the operator.
- depends on: t13, t15, t16
- covers: c14, h12, c54, h39, c41, h32, c11, h9, c27, h20, c25, h18
- acceptance:
  - README.md, CLAUDE.md, AGENTS.override.md, AGENTS.colleague.md and QWEN.md change together: the invariant reads as spoken commands in strict mode, CLI and dashboard are described as operator UIs, the QWEN.md --mode cool --target 24 example is gone, shipped sections lose their (planned) marker and nothing unshipped is described as shipped
  - docs/halacha-open-questions.md exists and lists: device status, speaking near a listening device, permitted hints, powering OFF from a cold hint, the deliberate delay, dashboard use on Shabbat, Yom Kippur and Yom Tov; no endorsement-sounding copy anywhere
  - README states that weekday mode obeys anyone in earshot and the whitelist is the control, lists every operator step as done before candle lighting, and its deployment checklist tells the operator to unset `TTS_DEBUG_TEXT`; no commit touches ../lobes-cli
  - version bumped with the version-bump skill and CHANGELOG notes that runtime dependencies stay empty; markdownlint passes

### t19 — On-box drills with the operator: PipeWire, live apply, reconnect, reboot

- instruction: Needs the operator and the hardware; run on a weekday. Record failures as they are.
- depends on: t16
- covers: c26, h19, c30, h23
- acceptance:
  - docs/drills/ records, with date and outputs: 10 minutes of container capture and playback through the reSpeaker with the host PipeWire session running and no EBUSY; one live --apply power change on the operator's pod
  - a lobes restart is followed by reconnection within 60 s; a host reboot brings the container back in the correct mode with 0 human actions and working audio with nobody logged in

## Risks

- [unknown_nonblocking] classifier approach: rules and lexicon first; recall on colloquial, Yeshivish or children's speech may fall short of 70 percent and need a model-backed classifier behind the same interface (which would then need context trimming per c29) (task t10)
- [unknown_nonblocking] fixture audio source: TTS-synthesised audio is a weaker proxy than human recordings for ASR errors; who records the operator corpus is undecided (frame park v3) (task t17)
- [unknown_nonblocking] lobes wire lives on the unmerged branch spec/hebrew-realtime; cited fixtures can drift until lobes-cli merges and releases (task t7)
- [unknown_nonblocking] thread concurrency around the pipeline queue, bounded ring and rate limiter was not examinable during the challenge pass (no code existed); t14 must document locking and the soak test must run multi-threaded (task t14)
- [unknown_nonblocking] container networking: binding the dashboard to the tailnet address needs either host networking or a published port on that address; tailscaled may start after Docker at boot, and Tailscale ACLs were not examined (no token, so every tailnet device can control AC power) (task t16)
- [unknown_nonblocking] PipeWire socket inside a container depends on the host user session (linger is enabled) and a matching uid; unproven until the t19 drill (task t19)
- [unknown_nonblocking] cross-origin POST refusal on iOS Safari was not examined; verify from the operator's phone during drills (task t15)
- [follow_up] sensibo-cli#15 (acState on a read path) is open; the dry-run diff stopgap stays until it lands (task t5)
- [unknown_nonblocking] zmanim correctness: a calendar or sunset bug fails toward weekday mode on a holy day; vectors must come from an independent published source, and the operator checks the preflight window by eye before each Shabbat until trusted (task t3)
- [out_of_scope] stale Sensibo state or a physical-remote change between status read and write; AC behaviour after a mains cut is outside the agent (frame park v4)

# lobes-driven AC power agent

> shabbos-goy listens through the lobes Hebrew realtime session (ears-only), and in Shabbat / Yom Kippur mode switches the AC power on or off only when it infers the wish from indirect speech; it can report AC status, stays quiet by default with an adjustable own-volume, and survives reboots unattended

## Audience

- an observant household (first: the operator's own home on the DGX Spark) that wants the AC handled on Shabbat/Yom Kippur without addressing a device, and the operator who sets everything up beforehand; secondary: mesh agents/maintainers who drive the CLI
  - instruction: README and 'shabbos-goy learn' name both readers; every setup step is doable before candle lighting

## Before → After

- Before: the repo is a CLI scaffold with no domain code; on Shabbat the household either leaves the AC fixed for 25 hours or relies on a timer, and nothing can react to how the room actually feels
  - instruction: README Status section is rewritten when the MVP lands
- After: a Compose service listens ambiently through lobes; a Hebrew remark that the room is hot powers the AC on and a remark that it is cold powers it off, with nobody addressing the device; commands are dropped in strict mode and obeyed on weekdays; after a power cut it resumes the right mode with no human touch
  - instruction: demonstrate end to end with fixtures ('classify', dry-run 'listen --script') and once live with --apply on the real pod

## Requirements

- AC actuation is POWER ON/OFF ONLY via `sensibo set <pod> --power on|off [--apply] --json`; the adapter builds an argv that can never contain --mode/--target/--fan/--swing (tested), because sensibo-cli set.py:116-131 uses the single-field PATCH acStates/on only when exactly one field differs and a full-state POST otherwise
  - honesty: a test enumerates every argv the adapter can produce and asserts the only sensibo flags are --power on|off, --apply and --json; tool arguments other than power are rejected by validation before any subprocess starts
- the whitelist is JSON config (zero runtime deps; pyyaml is dev-only per pyproject.toml) listing tools + Sensibo pod ids; for now the only representable tool arguments are power=on|off
  - honesty: deleting a tool or pod id from the JSON whitelist makes 'actions' stop listing it and makes the gate refuse it, with no code change; a malformed whitelist fails closed (nothing acts)
- the lobes client is ears-only and stdlib-only: copy the WebSocket layer of lobes-cli scripts/realtime-smoke.py (handshake, masking, framing, PING->PONG), stream PCM16 incl. silence on its own thread, never send response.create or declare tools, ignore empty-text transcripts
  - honesty: a wire-level test against a fake WebSocket server proves the client answers PING with PONG, never emits response.create or session.update tools, and drops empty-text transcripts
- resilience is client-side: lobes sessions have no resume (docs/realtime-pipeline.md:597-609) and uvicorn drops peers that miss a pong (~20s), so the listener reconnects with backoff, treats any disconnect as a lost turn (nothing acts on it), and holds no state across restarts
  - honesty: a test kills the fake server mid-turn: the client reconnects with capped backoff, the interrupted turn produces no action, and no file is written that a restart could replay
- a transcript joiner re-joins pause-split sentences using `speech_stopped`.`at_ms` -> next `speech_started`.`at_ms` gaps (Spark runs `VAD_SILENCE_MS`=500, no per-session VAD override exists in `_session.py` `parse_session_config`); a half sentence never acts
  - honesty: fixtures with a split sentence (gap below the join threshold) classify as one utterance; a dangling half ('הלוואי ש') never acts, including when the second half never arrives or arrives after a reconnect
- own-voice output is a thin PipeWire adapter in this repo (pw-play for TTS audio, PipeWire volume control for level/mute) targeting the reSpeaker XVF3800 node by name, the same device for capture and playback so AEC has its reference; the configured volume is re-applied at start-up; default is silent/near-silent; /v1/audio/speech has no volume parameter so level is applied locally
  - honesty: volume get/set/mute target the card by name 'Array', the configured level is re-applied at start-up, the default configuration speaks nothing, and the adapter is exercised in tests through a fake amixer/aplay
- Docker Compose service copies climate-cli house style (python:3.12-slim, non-root uid 1000 matching the host user, restart: unless-stopped, json-file 10m x 3, ro config bind mount from ~/.config/shabbos-goy, gitignored env file + .example) and newly establishes: the host user's PipeWire socket mounted into the container with pw-record/pw-play installed, pip-pinned sensibo-cli in the image, reaching the lobes gateway on host port 8001 via host.docker.internal/host-gateway, and a healthcheck proving transcripts/keepalive are live
  - honesty: docker compose config validates in CI; the image runs as non-root, contains a pinned sensibo-cli, has json-file rotation and restart: unless-stopped; the healthcheck goes unhealthy when no pong/transcript activity has been seen within its window
- new verbs (classify, zmanim, actions, listen, plus ac/volume/mode nouns) follow the scaffold contract: register(sub), `parser_class`=type(p) on nested subparsers, an overview sub-verb per noun, --json everywhere, explain catalog entries, actuating verbs dry-run unless --apply; catalog `_ROOT` and learn text get a domain rewrite
  - honesty: teken cli doctor . --strict passes; every new verb and noun has an explain catalog entry and --json; a test walks the parser and fails on a verb without a catalog entry; actuating verbs change nothing without --apply
- the domain story changes are propagated to README.md, CLAUDE.md, AGENTS.override.md, AGENTS.colleague.md and QWEN.md together, and docs/halacha-open-questions.md is created (it does not exist yet)
  - honesty: one PR updates README.md, CLAUDE.md, AGENTS.override.md, AGENTS.colleague.md and QWEN.md together (QWEN.md's '--mode cool --target 24' example removed) and adds docs/halacha-open-questions.md with no endorsement-sounding copy
- volume control: weekday mode accepts direct spoken volume commands; Shabbat/Yom Kippur mode changes volume only on inferred hints; CLI/config always available beforehand
  - honesty: tests: weekday + 'תנמיך את הווליום' changes volume; strict mode + the same utterance changes nothing; strict mode + a loudness remark lowers it; volume steps are bounded by config
- mode = zmanim-automatic plus a manual CLI override that can only make the mode stricter: it may force Shabbat/Yom Kippur mode on at any time but can never switch strict mode off inside a zmanim window
  - honesty: tests: inside a zmanim window 'mode set weekday' is refused and the mode stays strict; outside a window 'mode set shabbat' forces strict; the override is stored in config set before the day, not as runtime state that a reboot could lose into a laxer mode
- weekday mode obeys direct commands and hints, with the same power-only whitelist and the same ears-only lobes session; the classifier verdict plus the current mode decide whether a class may act
  - honesty: a single mode x class policy table drives the gate and is asserted cell by cell; weekday imperative/request/rebuke/hint act, strict mode acts on hints only; an untrusted clock selects the strict column
- AC power state is read via the zero-write dry-run 'sensibo set --power' diff until sensibo-cli exposes acState upstream
  - honesty: the status reader only ever runs sensibo without --apply (asserted on argv), parses 'changes' to derive on/off, reports 'unknown' on any non-zero exit, and is isolated behind one function so sensibo-cli#15 can replace it
- the agent runs 25h+ straight and hears a lot; it must survive accumulated context by compaction/trimming rather than keeping everything: any context it holds (transcript join buffer, event log, and the context of any model in the loop) is bounded and trimmed, and long history is not important to keep
  - instruction: soak test: feed a synthetic 25h+ stream of events through the listener loop and assert buffers/context stay under a fixed bound and behaviour is unchanged at the end
  - honesty: nothing the agent holds grows with session length: after a simulated 25h+ stream, memory/context size is under a fixed configured bound, old context has been trimmed or compacted, and classification of a fresh hint still works
- \[challenge/hardware\] audio I/O must coexist with the host PipeWire session: probe 'fuser /dev/snd/\*' shows user pipewire (pid 3189) holding pcmC1D0c and pcmC1D0p of card Array, so raw 'hw:Array' capture from a container will hit EBUSY; the audio backend (PipeWire socket mount vs releasing the card from PipeWire for exclusive ALSA use) is chosen deliberately and tested on the box
  - honesty: on the Spark, with the host PipeWire session running, the container captures 16 kHz PCM and plays a WAV through card Array for 10 minutes with no EBUSY and with AEC reference intact; the same works after a reboot with nobody logged in
- \[challenge/operations\] a stalled listener must exit non-zero by an internal watchdog (no pong/audio-send/transcript activity within a window): Compose restart policies act on exit, not on an 'unhealthy' healthcheck, so a healthcheck alone never restarts a wedged process
  - honesty: a test freezes the fake server (socket open, no frames): the process exits non-zero within the watchdog window, and a Compose drill shows it restarted
- \[challenge/containment\] actuation is rate-limited in config: a minimum interval between power changes (compressor protection, two people disagreeing hot/cold) and a cap on actions per day; when a limit is hit the agent does nothing and logs it. This is the only containment available on Shabbat, since nobody can command it to stop
  - honesty: tests: a second power change inside the minimum interval is refused and logged; the daily cap refuses the N+1th action; both limits come from config and are in-memory only, resetting toward 'allowed' never toward a replay of a refused action
- \[challenge/failure-modes\] the agent never acts on its own voice: transcripts that overlap its TTS playback (plus a tail) are discarded, so a spoken 'המזגן פועל' cannot be re-heard as a remark
  - honesty: a test plays a TTS phrase while a fixture transcript with the same text arrives: it is discarded; a genuine hint arriving after the tail window still acts
- \[challenge/unstated-assumptions\] the fixture corpus must include non-acting look-alikes as first-class negatives: negation ('לא חם פה'), questions to a person ('חם לך?'), reported/quoted speech ('אמרתי לו שחם פה'), past/future tense, third-party audio (TV, a guest on the phone) and learning/davening text; in doubt the class is 'unrelated'
  - honesty: the committed corpus has at least 10 fixtures in each negative category named in the claim, and strict mode acts on 0 of them
- \[challenge/failure-modes\] a failed actuation (Sensibo cloud or internet down, non-zero exit) is retried only within a short bounded in-memory window and then dropped; it is never persisted and never retried after a restart
  - honesty: tests: a failing sensibo subprocess is retried at most the configured number of times inside the window, then dropped; killing and restarting the process mid-retry produces no actuation
- \[challenge/observability\] a pre-Shabbat preflight ('shabbos-goy doctor' extension or 'preflight' verb) verifies in one run: lobes /health + key accepted, Sensibo key + pod reachable, card Array present, clock synchronised, location set, and prints the next strict-mode window so the operator can check the zmanim by eye before candle lighting
  - honesty: preflight exits 0 only when every check passes, exits 2 naming the failing check otherwise, supports --json, never actuates, and prints the next window in local time
- \[challenge/privacy\] error paths are covered by the no-transcript-text logging rule: exceptions and tracebacks raised while handling an utterance must not carry transcript text to stdout/stderr (Docker retains both)
  - honesty: a test forces an exception inside the utterance handler with a marker transcript and asserts the marker appears in neither stdout nor stderr
- \[challenge/time\] zmanim are computed in UTC from latitude/longitude and converted with an explicit configured timezone: the host is Asia/Jerusalem but a python:3.12-slim container defaults to UTC, and Israel leaves DST on 2026-10-25; candle-lighting offset (18/40 min) and the tzeit definition are config, with test vectors on both sides of a DST change
  - honesty: zmanim test vectors cover at least 3 locations and dates on both sides of the 2026-10-25 DST change, agree with a published source to within 2 minutes, and pass with the process TZ set to UTC

## Honesty conditions

- the full path transcript -> joiner -> classifier -> mode gate -> whitelist -> sensibo argv runs in tests with no microphone, no lobes server and no Sensibo account
- no commit in this repo touches ../lobes-cli, no tool declarations are sent to lobes, and the README deployment checklist tells the operator to unset `TTS_DEBUG_TEXT`
- scan-secrets (extended to ws/wss and non-JSON config, or an equivalent test) fails on a tracked non-localhost realtime URL; lobes host and both keys are read only from env/private config
- table-driven tests over the fixture corpus assert strict mode yields no action for imperative/request/rebuke, no queued or persisted pending action exists anywhere, no code path speaks a question, and log lines contain class + action but never transcript text
- every step a household member experiences needs no interaction with the device during the day; every operator step (location, whitelist, pod id, volume, keys) is documented as done before candle lighting
- the after-state is shown by a recorded dry-run drill from fixtures and one live --apply run on the operator's pod, and the reboot claim by an actual host reboot with the container coming back in the correct mode
- the README Status text matches the code on disk at merge time: nothing described as shipped is still planned
- the benchmark uses ASR-transcribed audio (not typed text) for the headline number, the corpus and thresholds are committed, and a regression that makes any command fixture act in strict mode fails CI
- README states plainly that weekday mode obeys anyone in earshot and that the whitelist is the control

## Success signals

- on the fixture corpus, transcribed through lobes batch Whisper, strict mode acts on 0 of the imperative/request/rebuke fixtures (>= 60 such fixtures, false-positive rate 0%) while acting on >= 70% of hint fixtures; the listener reconnects within 60 s of a lobes restart and the container returns to the correct mode after a host reboot with 0 human actions
  - instruction: a pytest-marked benchmark reads the corpus + cached ASR transcripts and asserts the FP count == 0; reconnect and reboot are checked by a scripted drill recorded in docs/

## Scope / boundaries

- lobes-cli is not modified: no tools declared to the session, no VAD override requested, BlueTTS weights never redistributed or defaulted; `TTS_DEBUG_TEXT` must be turned off on the Spark by the operator before household use (HANDOFF.md:149 records it ON)
- lobes host, `GATEWAY_API_KEY` and `SENSIBO_API_KEY` live only in env/private config; scripts/scan-secrets.py does not catch ws:// URLs or non-JSON files, so this is by discipline (or the scanner is extended)
- in Shabbat/Yom Kippur mode imperatives, requests and rebukes never act, are never queued, and nothing is asked back; logs carry classified intent + action only, never audio or transcript text
- \[challenge/security\] on weekdays anyone within earshot can switch AC power; this is accepted because the whitelist is power-only, and is the reason the whitelist must stay narrow when actuators are added

## Non-goals

- do not depend on microphone-cli, harmonics-cli or media-cli for volume or TTS playback: microphone-cli is capture-only by design (README non-scope), harmonics-cli plays only its own synthesized motifs, media-cli is an empty scaffold

## Assumptions

- AC status = room temperature/humidity from `sensibo read <pod> --json` (measurements present) PLUS power state, which sensibo-cli does not expose today (read/devices/query/MCP `read_location` carry no acState); power state needs an upstream sensibo-cli change or the zero-write dry-run `set --power <guess>` diff as a stopgap
- mode is computed statelessly from clock + stdlib zmanim at boot (host has docker enabled at boot and NTPSynchronized=yes at survey time); an untrusted clock or missing location fails toward strict mode and not acting
- \[challenge/adjacent-systems\] the lobes realtime wire on branch spec/hebrew-realtime (unmerged, gateway 0.79.0) stays stable; a recorded-session contract test in this repo is what detects drift, and lobes containers restart unless-stopped but need minutes of model load after a reboot, which the reconnect backoff must tolerate without giving up
- \[challenge/adjacent-systems\] the box's single microphone and lobes session capacity are not contended: no session limit was found in lobes/realtime/\*.py, and another client (e.g. realtime-he-accept.py) running at the same time would compete for the card

## Scope exploration

- `s1` — `sensibo-cli sensibo/cli/_commands/set.py:37-131 + sensibo/api/client.py:346-362`: --power alone yields requested=={on:bool}; one differing field -> PATCH /pods/{id}/acStates/on (mode/target untouched), >=2 fields -> full POST acStates. Dry-run default; applied JSON carries method + read-back `result_ac_state` (tests/`test_cli_set.py`)
  - seeds: `c2`, `c3`
- `s2` — `sensibo-cli read/devices/query/MCP (live 'sensibo read --json', _fleet.py _readings_of, collect/collector.py)`: status paths return measurements only (temperature, humidity, ...) and never acState.on/mode/target; only set.py `_current_ac_state` reads acState, so power state is visible only through a dry-run set diff. A subagent's live dry-run set was blocked by the session permission layer and not retried
  - seeds: `c4`
- `s3` — `sensibo-cli error + pacing contract (_client.py:21-39, set.py:158-167, client.py throttle/backoff)`: exit 0/1/2; set.py and `_client.py` disagree on 401/403 (1 vs 2) so consumers treat any non-zero as did-not-act; 1.5s min interval, 15s timeout, 429 backoff up to 120s per sleep bounds our subprocess timeout
  - seeds: `c2`
- `s4` — `lobes-cli docs/contracts/realtime-tool-calling.md + docs/realtime-pipeline.md (branch spec/hebrew-realtime)`: ears-only events: session.created, `speech_started`{`at_ms`,`item_id`}, `speech_stopped`{`at_ms`,`item_id`,reason silence|`max_turn`}, transcription.completed{`item_id`,text}, error{code}; low-confidence -> empty text; no resume, teardown on any disconnect; pong required (~20s pings); 401/426/404 refusals before upgrade; keyless GET /health
  - seeds: `c5`, `c6`
- `s5` — `lobes-cli scripts/realtime-smoke.py:104-649 + scripts/realtime-he-accept.py`: stdlib RFC6455 client (handshake, mask, `read_frame`, EventReader auto-PONG) designed as a droppable single file; he-accept adds arecord/aplay plughw capture, continuous mic feeder thread, `validate_device_pair` (same card for mic+speaker), --script-mode --wav, named exit codes
  - seeds: `c5`
- `s6` — `lobes-cli lobes/realtime/_settings.py + _session.py:770 parse_session_config`: VAD knobs are deployment-wide env (code default `VAD_SILENCE_MS`=600, Spark tuned to 500); per-session overrides exist only for `system_prompt`/language/audio format, so pause re-joining is the client's job
  - seeds: `c7`, `c11`
- `s7` — `lobes-cli batch audio (lobes/realtime/app.py:112-165, audio_facade.py)`: POST /v1/audio/speech {input, voice?, `response_format` wav|pcm, speed?} -> 24 kHz 16-bit mono, no volume/gain field; POST /v1/audio/transcriptions multipart file+language is the fixtures benchmark path
  - seeds: `c8`
- `s8` — `microphone-cli, harmonics-cli, media-cli (READMEs, pyproject, --help)`: microphone-cli v0.9.0 is capture-only (README excludes playback; has DoA/AEC/mic gain, not on PATH); harmonics 0.7.0 on PATH plays only synthesized motifs; media-cli README says 'Status: scaffold', no device code
  - seeds: `c9`, `c8`
- `s9` — `host audio (arecord -l / aplay -l)`: reSpeaker XVF3800 is ALSA card 'Array' (index 1 today) for both capture and playback; card 0 is NVIDIA HDMI; address by name
  - seeds: `c8`, `c10`
- `s10` — `climate-cli Dockerfile + docker-compose.yml`: python:3.12-slim, pip install, uid/gid 1000 non-root, restart unless-stopped, json-file 10m x3, `env_file` docker/weather.env (+.example), ro bind ~/.config/climate-cli; has NO sensibo relationship and no /dev/snd precedent
  - seeds: `c10`
- `s11` — `lobes-cli deployments + live docker ps + systemctl/timedatectl`: gateway publishes 0.0.0.0:8001->8000 (no host networking, no shared external network); key env var `GATEWAY_API_KEY` in the deployment .env; stdlib services healthcheck with a python urllib one-liner; docker enabled+active; NTPSynchronized=yes
  - seeds: `c10`, `c15`
- `s12` — `lobes-cli licence + debug notes (docs/hebrew-realtime.md BlueTTS, _vocalize.py:41, plans/2026-09-18-hebrew-realtime-HANDOFF.md:149)`: BlueTTS weights declare no licence; Chatterbox is Apache-2.0; `TTS_DEBUG_TEXT`=1 recorded ON in the Spark .env with 'turn off before household use'
  - seeds: `c11`
- `s13` — `shabbos-goy scripts/scan-secrets.py:179-199`: endpoint check covers only http(s) URLs under url/host/endpoint keys in JSON-parseable files; ws://host:8001 in any tracked file passes silently
  - seeds: `c12`
- `s14` — `shabbos-goy cli/__init__.py:64-119, _commands/cli.py:30-43, _output.py, _errors.py, explain/catalog.py:12-135, tests/`: register(sub) contract; nested nouns must pass `parser_class`=type(p); handlers raise CliError (0/1/2); catalog `_ROOT` and learn `_TEXT` are template prose; no test forces catalog coverage for new verbs; bandit already skips B404/B603; coverage gate 60; teken rubric wants an overview verb per noun (inferred from cli.py:3-4, rubric source not read)
  - seeds: `c13`
- `s15` — `shabbos-goy pyproject.toml + uv pip list`: dependencies=\[\] , python>=3.12, pyyaml dev-only, line length 100; no hdate/zmanim/astral/pyluach installed
  - seeds: `c3`, `c15`
- `s16` — `shabbos-goy README.md, CLAUDE.md, AGENTS.override.md, AGENTS.colleague.md, QWEN.md, docs/`: all five restate the domain story independently (QWEN.md even shows 'sensibo set --mode cool --target 24'); ac/volume/mode verbs appear nowhere; docs/halacha-open-questions.md does not exist
  - seeds: `c14`
- `s17` — `issue #1 (guildmaster brief + lobes comment)`: invariants: no wake word, no confirmation, imperatives dropped not queued, classifier FP rate on ASR-transcribed audio is the headline metric, neutral speech only, intent+action logging only
  - seeds: `c16`
- `s18` — `agentculture/sensibo-cli#15 (filed 2026-09-18)`: upstream ask for acState on a read path (read --json / status verb); until it lands the dry-run set --power diff is the stopgap
  - seeds: `c20`
- `s19` — `challenge pass / hardware lens: fuser /dev/snd/*, amixer -c Array scontrols, pgrep pipewire, loginctl Linger`: PipeWire user session holds Array capture+playback; mixer controls are 'PCM' and 'Headset' (PCM at 100%); user is in audio group; Linger=yes so the session exists after an unattended reboot
  - seeds: `c30`
- `s20` — `challenge pass / operations lens: climate-cli/lobes compose idioms + Docker restart semantics`: restart: unless-stopped reacts to process exit only; unhealthy containers are not restarted, so the spec's healthcheck (c10/h8) could not by itself recover a wedged listener
  - seeds: `c31`
- `s21` — `challenge pass / containment + reversibility lens: spec c16/c22 and the no-command invariant`: no stop mechanism can exist on Shabbat by design, so bounded actuation (interval + daily cap) is the containment; a wrong action is only reversible by another hint
  - seeds: `c32`
- `s22` — `challenge pass / failure-modes lens: echo path (issue #1 lobes comment) + c8/c23 spoken output`: AEC reduces but does not guarantee removal of own speech; self-hearing was not covered by any claim
  - seeds: `c33`
- `s23` — `challenge pass / unstated-assumptions lens: classifier fixture classes in spec c28/h13`: the corpus named commands and hints only; look-alike non-acting speech and third-party audio were missing; classifier approach itself is still an open risk on c1
  - seeds: `c34`
- `s24` — `challenge pass / failure-modes lens: sensibo-cli error contract (s3) + invariant #3`: spec said nothing about a failed actuation; unbounded retry would resemble a queued action
  - seeds: `c35`
- `s25` — `challenge pass / observability lens: existing 'doctor' verb + spec c13`: no claim let the operator verify the whole chain or the computed window before candle lighting
  - seeds: `c36`
- `s26` — `challenge pass / privacy lens: CLAUDE.md Privacy + cli/__init__.py:98-119 _dispatch exception wrapping`: `_dispatch` wraps unexpected exceptions into CliError messages, which can carry exception text; transcript text could leak through that path
  - seeds: `c37`
- `s27` — `challenge pass / time lens: timedatectl (Asia/Jerusalem, NTP yes) + python:3.12-slim default TZ`: container TZ differs from host; DST ends 2026-10-25; candle-lighting offset and tzeit definition vary by community
  - seeds: `c38`
- `s28` — `challenge pass / adjacent-systems lens: lobes gateway /health (0.79.0), docker inspect restart policies, grep for session limits in lobes/realtime`: gateway healthy and keyless /health works from the host; all four model-gear audio containers are unless-stopped; no session cap found (absence of evidence only); sensibo-cli 0.8.1 is on PyPI (requires >=3.12) so the image pin is feasible
  - seeds: `c39`, `c40`
- `s29` — `challenge pass / security lens: secrets + network path`: keys reach the container by env file (visible to docker inspect for docker-group members; socket is root:docker 660); the realtime socket is plain ws:// but stays on the host via host-gateway; accepted, recorded, not changed
  - seeds: `c41`
- `s30` — `challenge pass / migration + concurrency lenses`: no stored data to migrate (stateless by design). Concurrency: mic-feeder, reader and actuation threads share the join buffer and the rate limiter; NOT examined in code because none exists yet; to be carried as a plan risk
- `s31` — `challenge pass / unexamined surfaces`: not examined: Jetson Orin deployment, BlueTTS/Chatterbox voice quality at low volume, Sensibo API behaviour under real rate limiting, real-world classifier accuracy on children's or accented speech, physical speaker placement vs AEC

## Decisions

- zmanim and the Yom Kippur date are computed in pure stdlib (NOAA sunset maths + Hebrew-calendar conversion) verified against published test vectors; dependencies stay \[\]
- hint mapping: hot remark/wish -> AC power ON; cold remark/discomfort -> AC power OFF; unit assumed to stay in cool mode; if already in the requested state do nothing and say nothing
- spoken AC status only on weekdays when asked; never in Shabbat/Yom Kippur mode; 'shabbos-goy ac status' always works
- weekday mode: imperative, request and rebuke all act as commands; strict mode drops all three
- audio backend is the host PipeWire session: the container mounts the user's PipeWire socket and uses pw-record / pw-play and PipeWire volume control against the reSpeaker node; wherever earlier claims or honesty conditions say amixer/aplay or /dev/snd, read the PipeWire equivalents (fake pw-\* binaries in tests)
- Yom Tov days use the strict column by default (Israel vs diaspora second days set in config); Yom Tov-specific leniencies are out of scope
- in strict mode a hint acts after a configurable delay (default about 15 s) held only in memory and dropped on restart; weekday mode acts immediately

## Hard questions

- risk: classifier approach is undecided (rules/lexicon vs a local LLM vs hybrid); a rules-first classifier is testable and deterministic but may miss colloquial/Yeshivish phrasing, which costs recall not safety

## Open parks

- [unknown_nonblocking] fixture corpus audio: who records the Hebrew utterances (operator voice vs TTS-synthesised) for the ASR-transcribed benchmark
- [unknown_nonblocking] Sensibo state can be stale or changed by the physical remote between our status read and our write; AC behaviour after a mains power cut (does the unit resume?) is outside this agent's control
- [unknown_nonblocking] halachic status of powering the AC OFF from a 'cold' hint, and of the deliberate delay, belongs in docs/halacha-open-questions.md for the user's rav; the agent does not decide it
- [unknown_nonblocking] residual: classifier recall/precision on real household speech (children, guests, mixed Hebrew/English/Yiddish) is unmeasured until a recorded corpus exists
- [out_of_scope] additional actuators (lights, hot plate) and temperature/mode control

## Resolved vagueness

- [follow_up] Yom Tov support and any Yom Kippur-specific behavioural difference beyond the calendar window — resolved: Yom Tov is strict by default (user, 2026-09-18); only Yom Tov-specific leniencies and Yom Kippur-specific behaviour remain future work

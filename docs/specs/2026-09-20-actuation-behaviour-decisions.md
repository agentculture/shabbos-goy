# actuation behaviour decisions

> shabbos-goy states what it does at a window's end, when the AC cannot be reached, and what it inherits from the pod
> instruction: Each of the three behaviours must be findable in the README by someone who has not read this frame.

## Audience

- the household that set the AC up before the window and now depends on the agent switching only its power -- and the operator asked 'why did nothing happen?', for whom the honest answer is often 'the IR may not have been caught, and nothing here can tell you'.
  - instruction: Surface an unknown believed power state in preflight and on the dashboard.

## Before → After

- After: every actuation behaviour that was previously an omission is a stated decision: nothing happens at a window's end, a failed or unconfirmed write leaves power UNKNOWN so the next hint may retry, sensors stay out of the runtime, mode is out of band and out of the argv set, and the container's secrets come from the gitignored env file rather than grant. preflight and the README say what the channel cannot know.
  - instruction: Six stated behaviours; for each, record whether a test or a README line carries it.

## Why it matters

- an open-loop IR channel means the system's central belief -- 'the AC is already on' -- can be false with nothing able to detect it, and the gate built on that belief can silently disable the agent for a whole 25-hour window. Leaving these as omissions means the behaviour is whatever the code happens to do, which is exactly what a household cannot check on a Shabbat.
  - instruction: Restate the open-loop premise anywhere the README or preflight reports AC power.

## Requirements

- MIGRATED from qwen-worker-selfsetup/c50. There is NO end-of-window state management: nothing restores or powers down the AC when a window closes and nothing establishes a known state when one opens, so a single hint can leave the AC running across a full 25-hour Yom Kippur. This may be correct -- it is what the hint asked for -- but it is currently nobody's decision
  - instruction: Add no end-of-window code path; add the test and the README line instead.
  - honesty: the 25-hour case is written down as intended behaviour in the README, so a household meets it as a documented consequence rather than as a surprise
- MIGRATED from qwen-worker-selfsetup/c58. The already-in-state gate therefore rests on an unverifiable belief -- Sensibo's last requested state. One missed IR silently disables the agent for the rest of a window: every later hint hits the gate and does nothing while nobody can reach the remote. Retry limits retry a failure to REACH Sensibo, never a failure of the AC to obey
  - instruction: Model power as on|off|unknown; every failure mode sets unknown and each gets a test.
  - honesty: the unknown state is reached by every failure mode that actually occurs -- non-zero exit, timeout, unparseable output, network failure -- and each is covered by a test, because a belief that only clears on the one failure we imagined is the same defect
- MIGRATED from qwen-worker-selfsetup/c59. The ROOM is the only closed loop: ambient temperature over minutes is the sole ground truth, and outdoor temperature from climate-cli is the control that separates the AC's effect from the evening. Sensor context is not an enhancement, it is the only verification channel this system can have
  - instruction: Keep sensors in evidence records only; a package walk test must find no sensor import in the decision modules.
  - honesty: the thermal ground truth stays in the evidence records where 2026-09-20 put it, and a test enforces its absence from the decision path -- otherwise 'the only verification channel' quietly becomes a runtime dependency
- MIGRATED from qwen-worker-selfsetup/c54 and c37, both still undecided: what the agent does when Sensibo is unreachable for a whole window is unspecified, and the grant read-only mount fails (grant 0.11.0 fs.py:47 chmods on read) with three candidate fixes and none chosen
  - instruction: Close both halves in this frame: the outage via the unknown state plus existing backoff, grant via the compose comment fix and an upstream issue.
  - honesty: both halves are actually closed by this frame: the whole-window Sensibo outage is specified by c9's unknown state plus the existing retry/backoff, and grant is closed by c11. If either still has no answer at plan time, it becomes a blocking question rather than an unread claim

## Honesty conditions

- each of the three things it states -- window end, unreachable AC, what is inherited from the pod -- is findable in the README by an operator who has not read this frame, or the announcement is not true
- the 'why did nothing happen' answer is reachable from the operator surfaces, not only from this frame -- preflight or the dashboard must say when the believed power state is unknown
- each of the six stated behaviours has a test or a README line, and the frame names which of the two it is for each -- a decision with neither is an omission again
- the open-loop premise is restated wherever a reader might assume otherwise, in particular anywhere the README or preflight reports AC power
- the failed-write pair is tested against a simulated sensibo-cli failure rather than a live outage, so the suite needs no internet and no pod
- the no-scheduler boundary is checked by absence: nothing in the package registers a timer or future callback for actuation
- the 4 failure modes are the ones that actually occur against sensibo-cli, enumerated by reading its exit behaviour rather than guessed -- a list of imagined failures is the same defect as trusting one imagined success

## Success signals

- a failed write followed by the same hint actuates a second time, while a successful write followed by the same hint does not -- the pair. Plus: crossing a window boundary produces no action; a package walk finds no sensor import in the decision modules; the argv enumeration is unchanged; and docker-compose.yml no longer claims grant is the secret path.
  - instruction: Simulate sensibo-cli failures; the suite must need no internet and no pod.
- the failed-write pair passes 2 of 2, every one of the 4 failure modes (non-zero exit, timeout, unparseable output, network error) sets power to unknown, the argv set stays at exactly 3 forms (--power on|off, --apply, --json), and 0 sensor imports appear in the decision modules.
  - instruction: Assert the counts as counts: 4 failure modes covered, 3 argv forms enumerated, 0 sensor imports found by the package walk.

## Scope / boundaries

- power is the only thing the software controls, and belief about power is the only thing it models. Nothing here reads a sensor at decision time, nothing widens the argv set, and nothing schedules an action for a future moment -- including a window's end.
  - instruction: Keep the argv enumeration test green; add no scheduler and no sensor read to the decision path.

## Non-goals

- controlling the AC beyond power. No --mode, --target, --fan or --swing becomes reachable, and the argv enumeration test stays. Also not a goal: making actuation verifiable within a single decision -- c3 says it cannot be, and pretending otherwise by reading back a cloud field would be a proxy dressed as a measurement.
  - instruction: Keep the argv enumeration test green; power is the only control.

## Assumptions

- MIGRATED from qwen-worker-selfsetup/c57. Sensibo is an OPEN-LOOP IR blaster: a command can be sent and the AC may simply not catch it, so no field the cloud could expose is a measurement. Mode is unknowable from the actuation channel by construction, and so is power

## Decisions

- MIGRATED from qwen-worker-selfsetup/c60. AC state and mode are defined as needed out of band -- the household sets the unit before the window and the agent only switches power -- so excluding --mode from the argv set is correct. What the software owes is honesty in preflight and the README, not control
- END OF WINDOW: nothing happens. When a strict window closes the agent takes no action on the AC -- it stays exactly as the last acted-on hint left it, which means a single hint can leave it running for a full 25-hour Yom Kippur. This is now a decision, not an omission (it resolves c2): the hint asked for that state, and the agent never fires an unrequested actuation at tzeit when nobody is watching. Symmetrically, nothing establishes a known state when a window OPENS -- the household sets the unit before the window (c6). What the software owes is that the README says this plainly.
  - instruction: Add no end-of-window code path. Add a test asserting that crossing a window boundary produces no action, and a README line stating that a hint's effect persists past the window's end until a person changes it.
- STALE BELIEF: a Sensibo write that did not clearly succeed sets the believed power state to UNKNOWN rather than to the requested value. The already-in-state gate holds only against a state it actually believes, so the next matching hint is allowed to act again instead of being silently gated out for the rest of the window (it resolves c4). Cost accepted: an occasional duplicate IR send, which an AC already in that state ignores -- a duplicate is cheap, a silently disabled agent is not.
  - instruction: In actuators/sensibo.py and the pipeline's already-in-state gate, model power as on|off|unknown. Any non-zero exit, timeout or unparseable response from sensibo-cli sets unknown. Test: a failed write followed by the same hint actuates a second time; a successful write followed by the same hint does not.
- SENSORS STAY OUT OF THE RUNTIME: ambient and outdoor temperature are an evidence channel only -- used in drills and evidence records, as on 2026-09-20 -- and never reach a verdict. `may_act`, the confidence floor and the already-in-state gate are unchanged, so no sensor read can cause an action and no sensor failure can block one. c5 remains true about verification without becoming a runtime dependency.
  - instruction: Add no sensor read to pipeline.py or policy.py. A test walking the package must show no import of a climate or room-sensor path from the decision modules, mirroring how the retired rule classifier is fenced off.
- GRANT: the gitignored env file is the deployment of record and grant is not used for the container (it resolves the open half of c7). grant 0.11.0 chmods its store on the read path (grant/fs.py:47), so a /grant:ro mount raises EROFS -- verified on 2026-09-20, which makes docker-compose.yml's 'UNVERIFIED' comment verified FALSE. Of the three candidate fixes, mounting read-write widens what the container may do to the secret store and patching grant is out of this repo's scope, so the third is chosen: correct the compose comment to name the env file and file the chmod-on-read bug upstream.
  - instruction: Replace the grant paragraph in docker-compose.yml with the env-file path CLAUDE.md already documents, and open an issue against grant for the read-path chmod. Do not add a grant dependency.

#!/usr/bin/env bash
# shabbos-goy restart proof.
#
# Tier A is fully automatic and catches the 2026-09-20 failure: the listener
# was Up (healthy) with pong+audio flowing while pw-record was bound to
# HDMI 0:monitor and the agent heard NOTHING. Passing --target to pw-record is
# not enough -- pw-record accepts an unknown target and silently falls back --
# so Tier A asserts the ACTUAL binding.
#
# Tier B needs one spoken phrase, because acoustic hearing cannot be proven
# without sound. Playing audio through the reSpeaker's own speaker is exactly
# what its AEC cancels, so a loopback would prove nothing.
#
# Usage:
#   scripts/restart-proof.sh                 # Tier A, then report Tier B state
#   scripts/restart-proof.sh --wait-speech 120   # Tier A, then wait for a phrase
#
# Exit: 0 all asserted tiers pass | 1 a Tier A assertion failed | 2 environment
set -uo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CFG="${XDG_CONFIG_HOME:-$HOME/.config}/shabbos-goy/config.json"
DASH="${SHABBOS_GOY_DASHBOARD_URL:-http://127.0.0.1:8787}"
WAIT_SPEECH=0
[ "${1:-}" = "--wait-speech" ] && WAIT_SPEECH="${2:-120}"

cd "$REPO" || exit 2
fail=0
ok(){ printf '  \033[32mPASS\033[0m  %s\n' "$1"; }
no(){ printf '  \033[31mFAIL\033[0m  %s\n' "$1"; fail=1; }
info(){ printf '  ----  %s\n' "$1"; }

echo "shabbos-goy restart proof — $(date -Is)   uptime:$(uptime -p)"
echo
echo "[Tier A — automatic]"

# A1: the service came back on its own.
status=$(docker compose ps --format '{{.Status}}' 2>/dev/null)
case "$status" in
  *"(healthy)"*) ok "container healthy, unattended: $status" ;;
  "")            no "container is not running at all" ;;
  *)             no "container not healthy: $status" ;;
esac
info "RestartCount=$(docker inspect shabbos-goy --format '{{.RestartCount}}' 2>/dev/null)  policy=$(docker inspect shabbos-goy --format '{{.HostConfig.RestartPolicy.Name}}' 2>/dev/null)"

# Configured node names.
MIC=$(python3 -c "import json,sys;print(json.load(open('$CFG')).get('audio',{}).get('mic_node',''))" 2>/dev/null)
SPK=$(python3 -c "import json,sys;print(json.load(open('$CFG')).get('audio',{}).get('speaker_node',''))" 2>/dev/null)
[ -n "$MIC" ] && info "configured mic_node: $MIC" || no "config names no mic_node"

# A2: the configured mic node actually exists (the USB-enumeration race).
if pactl list short sources 2>/dev/null | awk '{print $2}' | grep -qx -- "$MIC"; then
  ok "configured mic_node exists as a PipeWire source"
else
  no "configured mic_node is ABSENT — WirePlumber never published it (try: systemctl --user restart wireplumber)"
fi

# A3: pw-record is bound to THAT node, not a fallback. The 2026-09-20 bug.
BOUND=$(python3 - "$MIC" <<'PY' 2>/dev/null
import re,subprocess,sys
want=sys.argv[1]
so=subprocess.run(["pactl","list","source-outputs"],capture_output=True,text=True).stdout
src=None
for block in so.split("Source Output #"):
    if 'application.name = "pw-record"' in block:
        m=re.search(r"Source:\s*(\d+)",block)
        if m: src=m.group(1)
if src is None: print("NOCAPTURE"); sys.exit()
for line in subprocess.run(["pactl","list","short","sources"],capture_output=True,text=True).stdout.splitlines():
    f=line.split("\t")
    if f and f[0]==src: print(f[1]); sys.exit()
print("UNKNOWN:"+src)
PY
)
case "$BOUND" in
  "$MIC")      ok "pw-record is bound to the configured mic_node" ;;
  NOCAPTURE)   no "no pw-record capture stream exists — the ears are not streaming" ;;
  *)           no "pw-record is bound to the WRONG source: $BOUND (silent fallback — this is the deafness bug)" ;;
esac

# A4: the AEC pair — speaker must be the same device as the mic.
if pactl list short sinks 2>/dev/null | awk '{print $2}' | grep -qx -- "$SPK"; then
  ok "configured speaker_node exists (AEC reference intact)"
else
  no "configured speaker_node is absent: $SPK"
fi

# A5: every precondition, tolerating the post-boot model load (http_503).
pf=""
for i in $(seq 1 8); do
  pf=$(docker compose exec -T shabbos-goy shabbos-goy preflight 2>&1)
  echo "$pf" | grep -q '^healthy: True' && break
  echo "$pf" | grep -q 'http_503' && { info "preflight: senses backend still loading (attempt $i) — waiting"; sleep 30; continue; }
  break
done
if echo "$pf" | grep -q '^healthy: True'; then ok "preflight 8/8"
else no "preflight failed:"; echo "$pf" | grep -E '^\[FAIL\]' | sed 's/^/          /'; fi

# A6: the operator surface answers.
code=$(curl -sS -m 5 -o /tmp/.sgp -w '%{http_code}' "$DASH/api/utterances" 2>/dev/null)
[ "$code" = "200" ] && ok "dashboard reachable ($DASH)" || no "dashboard did not answer: HTTP $code"

echo
echo "[Tier B — needs one spoken phrase]"
hb(){ docker compose exec -T shabbos-goy cat /tmp/shabbos-goy/heartbeat.json 2>/dev/null; }
tstamp(){ hb | python3 -c "import json,sys;d=json.load(sys.stdin);print(d['last'].get('transcript') or 0)" 2>/dev/null || echo 0; }
count(){ curl -sS -m 5 "$DASH/api/utterances" 2>/dev/null | python3 -c "import json,sys;print(json.load(sys.stdin).get('count',0))" 2>/dev/null || echo 0; }

before=$(tstamp)
[ "$before" != "0" ] && info "a transcript stamp already exists ($before) — it proves nothing about THIS boot"
if [ "$WAIT_SPEECH" != "0" ]; then
  echo "  Say a NEUTRAL Hebrew phrase near the device (not a heat or cold remark —"
  echo "  the listener is live). Waiting up to ${WAIT_SPEECH}s for a FRESH transcript..."
  deadline=$((SECONDS+WAIT_SPEECH))
  while [ $SECONDS -lt $deadline ]; do
    now=$(tstamp)
    # Strictly newer than the pre-existing stamp: a stale stamp must never pass.
    awk "BEGIN{exit !($now > $before)}" && break
    sleep 3
  done
fi
t=$(tstamp); c=$(count)
if [ "$WAIT_SPEECH" != "0" ]; then
  if awk "BEGIN{exit !($t > $before)}"; then
    ok "a FRESH transcript arrived after this boot (stamp $t > $before); ring holds $c"
  else
    no "NO fresh transcript in ${WAIT_SPEECH}s — the agent is deaf on this boot (stamp unchanged at $t)"
  fi
elif [ "$t" != "0" ]; then
  info "a transcript stamp exists ($t, ring $c) but freshness was NOT asserted — re-run with --wait-speech to prove hearing on this boot"
else
  no "no transcript stamp at all — the agent has never heard anything since this boot"
fi

echo
[ $fail -eq 0 ] && echo "RESULT: Tier A passed." || echo "RESULT: Tier A FAILED — see above."
exit $fail

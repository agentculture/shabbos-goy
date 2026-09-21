#!/usr/bin/env bash
# Rebuild the image, deploy it and VERIFY THE PROPERTIES, not the build.
# Every assertion reads from inside the running container.
#
# Never run this inside a strict window: it restarts the listener.
#
# Exit: 0 deployed and verified | 1 an assertion failed | 2 environment
set -uo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO" || exit 2
fail=0
ok(){ printf '  \033[32mPASS\033[0m  %s\n' "$1"; }
no(){ printf '  \033[31mFAIL\033[0m  %s\n' "$1"; fail=1; }

echo "== before =="
docker compose exec -T shabbos-goy python3 -c \
  "from shabbos_goy.decider.prompt import PROMPT_VERSION as p; print('prompt', p)" 2>/dev/null

echo "== build =="
docker compose build 2>&1 | tail -2 || exit 2

echo "== up -d =="
docker compose up -d 2>&1 | tail -3

echo "== wait for healthy (up to 6 min: vLLM warm-up) =="
for i in $(seq 1 36); do
  s=$(docker compose ps --format '{{.Status}}' 2>/dev/null)
  case "$s" in *"(healthy)"*) ok "container healthy: $s"; break ;; esac
  sleep 10
done
case "$(docker compose ps --format '{{.Status}}' 2>/dev/null)" in
  *"(healthy)"*) : ;; *) no "container not healthy" ;;
esac

echo "== properties INSIDE the running container =="
v=$(docker compose exec -T shabbos-goy python3 -c \
  "from shabbos_goy.decider.prompt import PROMPT_VERSION as p; print(p)" 2>/dev/null | tr -d '\r\n')
want=$(sed -n 's/^PROMPT_VERSION *= *"\(.*\)"$/\1/p' shabbos_goy/decider/prompt.py)
[ -n "$want" ] && [ "$v" = "$want" ] && ok "PROMPT_VERSION=$v in the container, as in the checkout" \
  || no "PROMPT_VERSION is '$v' in the container, the checkout says '$want'"

a=$(docker compose exec -T shabbos-goy python3 -c \
  "from shabbos_goy.decider.context import DEFAULT_MAX_AGE_SECONDS as a; print(int(a))" 2>/dev/null | tr -d '\r\n')
[ "$a" = "120" ] && ok "context window 120s in the container" || no "context age is '$a', expected 120"

docker compose exec -T shabbos-goy python3 -c \
  "from shabbos_goy.pipeline import Pipeline; import inspect; \
   assert 'mode_at' in inspect.signature(Pipeline.__init__).parameters; print('ok')" >/dev/null 2>&1 \
  && ok "boundary fix present (Pipeline accepts mode_at)" || no "mode_at missing from Pipeline"

docker compose exec -T shabbos-goy python3 -c \
  "from shabbos_goy.pipeline import Pipeline; from shabbos_goy.runtime import listener; import inspect; \
   assert 'joiner_wall_clock' in inspect.signature(Pipeline.__init__).parameters; \
   assert hasattr(listener, '_Received'); print('ok')" >/dev/null 2>&1 \
  && ok "receipt-instant fix present (_Received, joiner_wall_clock)" || no "receipt-instant fix missing"

echo "== preflight (retries while senses warms up) =="
for i in $(seq 1 8); do
  pf=$(docker compose exec -T shabbos-goy shabbos-goy preflight 2>&1)
  echo "$pf" | grep -q '^healthy: True' && break
  echo "$pf" | grep -q 'http_503' && { echo "  ---- senses loading (attempt $i)"; sleep 30; continue; }
  break
done
echo "$pf" | grep -q '^healthy: True' && ok "preflight 8/8" || { no "preflight failed:"; echo "$pf" | grep -E '^\[FAIL\]' | sed 's/^/        /'; }

echo "== hearing (the property, not the label) =="
# A fresh boot has heard nothing yet, so this fails until one phrase is spoken
# in the room. It is a real failure of the property and is reported as one.
bash scripts/restart-proof.sh 2>&1 | grep -E 'PASS|FAIL' | sed 's/^/  /'
[ "${PIPESTATUS[0]}" -eq 0 ] || fail=1

echo
[ $fail -eq 0 ] && echo "RESULT: deployed and verified." || echo "RESULT: FAILED -- see above."
exit $fail

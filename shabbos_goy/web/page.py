"""The dashboard page: one self-contained HTML document, embedded here.

Embedded as a Python string on purpose. A ``.html`` data file would have to
be declared to the build backend and would silently vanish from a wheel if
that declaration ever drifted; a module constant cannot. It also keeps the
"no build step, no external asset, no CDN" rule mechanically true: there is
nothing to bundle and nothing to fetch, so the page works offline on a box
whose only route to the world is a tailnet.

The visual language (a dawn palette, a dark variant for the hour before it,
a live event stream read top-down) is taken as a *reference* from lobes-cli's
``site/src/components/EventStream.astro`` and its ``global.css`` tokens --
read, not copied: none of that site's build tooling comes with it.

Text on this page is English. An operator reading a screen is not speaking
to a listening box, so the no-question-mark rule that binds spoken Hebrew
does not bind the UI -- but there is no confirmation dialog here either: a
button either acts or is refused, and the refusal is shown, never asked back.
"""

from __future__ import annotations

__all__ = ["DASHBOARD_HTML"]

DASHBOARD_HTML = """<!doctype html>
<html lang="en" dir="ltr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="referrer" content="no-referrer">
<title>shabbos-goy</title>
<style>
:root {
  color-scheme: light dark;
  --bg: #f4f5fb;
  --surface: #ffffff;
  --ink: #232a4d;
  --ink-soft: #4d546f;
  --accent: #0b655c;
  --on-accent: #ffffff;
  --line: rgba(35, 42, 77, 0.14);
  --warn: #8a4b12;
  --bad: #8c2f2f;
  --radius: 14px;
  --mono: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  --body: system-ui, -apple-system, "Segoe UI", sans-serif;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #0b0f20;
    --surface: #161b36;
    --ink: #e7eaf6;
    --ink-soft: #a8b0cd;
    --accent: #6fd3c2;
    --on-accent: #08231f;
    --line: rgba(231, 234, 246, 0.16);
    --warn: #e6b27a;
    --bad: #f0928c;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0; padding: 1.25rem; background: var(--bg); color: var(--ink);
  font-family: var(--body); line-height: 1.5;
}
h1 { font-size: 1.15rem; margin: 0 0 1rem; letter-spacing: 0.02em; }
h2 { font-size: 0.78rem; margin: 0 0 0.6rem; text-transform: uppercase;
     letter-spacing: 0.09em; color: var(--ink-soft); }
main { display: grid; gap: 1rem; grid-template-columns: repeat(auto-fit, minmax(19rem, 1fr));
       max-width: 88rem; margin: 0 auto; }
section { background: var(--surface); border: 1px solid var(--line);
          border-radius: var(--radius); padding: 1rem; }
section.wide { grid-column: 1 / -1; }
dl { display: grid; grid-template-columns: auto 1fr; gap: 0.3rem 0.9rem; margin: 0; }
dt { color: var(--ink-soft); font-size: 0.85rem; }
dd { margin: 0; font-family: var(--mono); font-size: 0.85rem; overflow-wrap: anywhere; }
.pill { display: inline-block; padding: 0.1rem 0.55rem; border-radius: 999px;
        border: 1px solid var(--line); font-size: 0.78rem; font-family: var(--mono); }
.pill.strict { background: var(--warn); color: var(--surface); border-color: transparent; }
.pill.bad { background: var(--bad); color: var(--surface); border-color: transparent; }
button { font: inherit; padding: 0.45rem 0.8rem; border-radius: 10px; cursor: pointer;
         border: 1px solid var(--line); background: var(--surface); color: var(--ink); }
button.primary { background: var(--accent); color: var(--on-accent); border-color: transparent; }
button:disabled { opacity: 0.5; cursor: not-allowed; }
.row { display: flex; flex-wrap: wrap; gap: 0.5rem; margin-bottom: 0.6rem; }
table { width: 100%; border-collapse: collapse; font-size: 0.85rem; }
th { text-align: left; color: var(--ink-soft); font-weight: 600; font-size: 0.75rem;
     text-transform: uppercase; letter-spacing: 0.06em; }
th, td { padding: 0.35rem 0.5rem; border-bottom: 1px solid var(--line);
         vertical-align: top; }
td.text { font-size: 0.95rem; }
td.mono, .mono { font-family: var(--mono); }
tr:last-child td { border-bottom: 0; }
.he { direction: rtl; unicode-bidi: plaintext; text-align: right; }
.muted { color: var(--ink-soft); }
#flash { min-height: 1.4rem; font-family: var(--mono); font-size: 0.85rem; }
@media (prefers-reduced-motion: reduce) { * { transition: none !important; } }
</style>
</head>
<body>
<h1>shabbos-goy <span class="muted">operator dashboard</span></h1>
<main>

<section>
  <h2>Mode</h2>
  <dl>
    <dt>mode</dt><dd><span id="mode" class="pill">-</span></dd>
    <dt>window</dt><dd id="kinds">-</dd>
    <dt>override</dt><dd id="override">-</dd>
    <dt>clock</dt><dd id="clock">-</dd>
    <dt>current</dt><dd id="window-current">-</dd>
    <dt>next</dt><dd id="window-next">-</dd>
  </dl>
</section>

<section>
  <h2>Connection and AC</h2>
  <dl>
    <dt>lobes</dt><dd id="conn">-</dd>
    <dt>power</dt><dd id="ac-power">-</dd>
    <dt>temperature</dt><dd id="ac-temp">-</dd>
    <dt>humidity</dt><dd id="ac-humidity">-</dd>
    <dt>actuation</dt><dd id="apply-mode">-</dd>
    <dt>pending</dt><dd id="pending">-</dd>
  </dl>
</section>

<section>
  <h2>Decider (bug context)</h2>
  <dl>
    <dt>source</dt><dd id="decider-source">-</dd>
    <dt>prompt</dt><dd id="decider-prompt">-</dd>
    <dt>last</dt><dd id="decider-last">-</dd>
    <dt>no-decision</dt><dd id="decider-nodecision">-</dd>
    <dt>latency</dt><dd id="decider-latency">-</dd>
    <dt>errors</dt><dd id="errors">-</dd>
  </dl>
</section>

<section class="wide">
  <h2>Controls</h2>
  <div class="row">
    <button class="primary" data-post="/api/control/ac" data-body='{"power":"on"}'>AC on</button>
    <button data-post="/api/control/ac" data-body='{"power":"off"}'>AC off</button>
    <button data-post="/api/control/volume" data-body='{"direction":"up"}'>Volume up</button>
    <button data-post="/api/control/volume" data-body='{"direction":"down"}'>Volume down</button>
    <button data-post="/api/control/mode" data-body='{"mode":"strict"}'>Force strict</button>
    <button data-post="/api/control/mode" data-body='{"mode":"weekday"}'>Force weekday</button>
    <button data-post="/api/control/mode" data-body='{"mode":null}'>Clear override</button>
    <button data-post="/api/control/preflight" data-body='{}'>Preflight</button>
  </div>
  <div id="flash" class="muted">Controls are dry-run unless the listener was started
  with --apply. Nothing here asks a question back.</div>
</section>

<section class="wide">
  <h2>Recent utterances (memory only)</h2>
  <table>
    <thead><tr><th>age</th><th>text</th><th>class</th><th>intent</th>
    <th>verdict</th><th>action</th><th>decide ms</th></tr></thead>
    <tbody id="utterances"><tr><td colspan="7" class="muted">nothing yet</td></tr></tbody>
  </table>
</section>

<section class="wide">
  <h2>Decision log (no transcript text)</h2>
  <table>
    <thead><tr><th>class</th><th>intent</th><th>verdict</th><th>action</th>
    <th>target</th><th>reason</th></tr></thead>
    <tbody id="log"><tr><td colspan="6" class="muted">nothing yet</td></tr></tbody>
  </table>
</section>

</main>
<script>
const $ = (id) => document.getElementById(id);
const dash = (value) => (value === null || value === undefined || value === "" ? "-" : value);

function cell(row, value, cls) {
  const td = document.createElement("td");
  td.textContent = dash(value);
  if (cls) { td.className = cls; }
  row.appendChild(td);
}

function fillRows(tbody, rows, columns, empty) {
  tbody.replaceChildren();
  if (!rows.length) {
    const tr = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = columns.length;
    td.className = "muted";
    td.textContent = empty;
    tr.appendChild(td);
    tbody.appendChild(tr);
    return;
  }
  for (const item of rows) {
    const tr = document.createElement("tr");
    for (const column of columns) { cell(tr, column.get(item), column.cls); }
    tbody.appendChild(tr);
  }
}

async function poll() {
  try {
    const state = await (await fetch("/api/state", {cache: "no-store"})).json();
    const mode = $("mode");
    mode.textContent = state.mode.mode;
    mode.className = "pill " + (state.mode.mode === "strict" ? "strict" : "");
    $("kinds").textContent = state.mode.kinds.length ? state.mode.kinds.join(", ") : "none";
    $("override").textContent = dash(state.mode.override);
    $("clock").textContent = state.mode.clock_trusted ? "trusted" : "UNTRUSTED";
    $("window-current").textContent = state.window.current
      ? state.window.current.start + " to " + state.window.current.end : "-";
    $("window-next").textContent = state.window.next
      ? state.window.next.kinds.join(", ") + " " + state.window.next.start : "-";
    $("conn").textContent = state.connection.state;
    $("ac-power").textContent = state.ac.power;
    $("ac-temp").textContent = dash(state.ac.temperature);
    $("ac-humidity").textContent = dash(state.ac.humidity);
    $("apply-mode").textContent = state.apply ? "apply" : "dry-run";
    $("pending").textContent = state.limits.pending_delayed;
    $("decider-source").textContent = dash(state.decider.source);
    $("decider-prompt").textContent = dash(state.decider.prompt_version);
    const last = state.decider.last;
    $("decider-last").textContent = last
      ? [last.klass, last.intent, last.verdict, last.action].join(" / ") : "-";
    const reasons = Object.entries(state.decider.no_decision_reasons);
    $("decider-nodecision").textContent = reasons.length
      ? reasons.map(([k, v]) => k + "=" + v).join(" ") : "none";
    const lat = state.decider.decide_latency_ms;
    $("decider-latency").textContent = lat.available
      ? "p50 " + lat.p50 + " / p95 " + lat.p95 : "not measured";
    $("errors").textContent = state.errors.length
      ? state.errors.map((e) => e.reason).join(", ") : "none";

    fillRows($("log"), state.log.slice(-40).reverse(), [
      {get: (r) => r.klass}, {get: (r) => r.intent}, {get: (r) => r.verdict},
      {get: (r) => r.action}, {get: (r) => r.target}, {get: (r) => r.reason},
    ], "nothing yet");

    const utterances = await (await fetch("/api/utterances", {cache: "no-store"})).json();
    fillRows($("utterances"), utterances.utterances.slice().reverse(), [
      {get: (u) => Math.round(u.age_seconds) + "s"},
      {get: (u) => u.text, cls: "text he"},
      {get: (u) => u.klass}, {get: (u) => u.intent},
      {get: (u) => u.verdict}, {get: (u) => u.action},
      {get: (u) => u.decide_latency_ms},
    ], "nothing yet");
  } catch (err) {
    $("flash").textContent = "dashboard unreachable: " + err;
  }
}

async function send(button) {
  const flash = $("flash");
  button.disabled = true;
  try {
    const response = await fetch(button.dataset.post, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: button.dataset.body,
    });
    const payload = await response.json();
    flash.textContent = response.status + " " + JSON.stringify(payload);
  } catch (err) {
    flash.textContent = "control failed: " + err;
  } finally {
    button.disabled = false;
    poll();
  }
}

for (const button of document.querySelectorAll("button[data-post]")) {
  button.addEventListener("click", () => send(button));
}
poll();
setInterval(poll, 2000);
</script>
</body>
</html>
"""

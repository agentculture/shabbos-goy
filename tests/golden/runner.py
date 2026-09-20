"""Run the golden manifest against a decider, by three entrances, and score it.

Approved deviation d4. The golden set is the measurement this project's
headline claim rests on: **it did not act on a command**. So the same scoring
code has to serve three inputs, because each one is a different chance to
break the invariant:

* ``text``            -- the manifest text straight to the decider. What the
  model does with clean Hebrew.
* ``audio-batch``     -- the row's TTS wav through ``POST /v1/audio/transcriptions``
  and then to the decider. What the model does with *ASR output*, which is
  what it will really see (CLAUDE.md: measure on ASR-transcribed audio).
* ``audio-realtime``  -- the same wav streamed through the ears-only realtime
  session plus :class:`~shabbos_goy.joiner.TranscriptJoiner`. This is the only
  entrance that can show a sentence arriving in halves, so a row here passes
  the hard rule only if **none** of the utterances it produced acts.

Three rules this module keeps:

1. **The outcome is what is scored**, not the label. ``act`` means
   :func:`shabbos_goy.policy.may_act` allowed the class *and* the intent names
   something to do. A near-synonym label that leads to the same outcome is not
   a failure; an imperative that acts is, and it is a HARD failure.
2. **Nothing here actuates.** No tool is called, no ``--apply`` exists, and no
   actuator is imported. It measures, prints and exits non-zero.
3. **Offline by construction.** Every network call goes through a small,
   injectable seam (:class:`LobesBatchClient`, :func:`stream_wav`), so the
   default test run exercises all of it against in-process fakes.

Run it on the box::

    python3 -m tests.golden.runner --entrance text --mode both
    python3 scripts/golden-set.py --entrance audio-batch --record

Stdlib only, like the rest of the package.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
import wave
from collections import deque
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence
from urllib.parse import urlsplit, urlunsplit

# ``python3 tests/golden/runner.py`` (no -m) would not have the repo root on
# sys.path; add it before importing the package, so both spellings work.
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:  # pragma: no cover - import-order guard
    sys.path.insert(0, str(REPO_ROOT))

from shabbos_goy.decider import (  # noqa: E402
    INTENTS,
    PROMPT_VERSION,
    ContextWindow,
    Decision,
    GemmaDecider,
    ReplayDecider,
    senses_config_from_env,
)
from shabbos_goy.decider.gemma import ENV_SENSES_URL  # noqa: E402
from shabbos_goy.joiner import TranscriptJoiner  # noqa: E402
from shabbos_goy.lobes import LobesClient, LobesConfig, config_from_env  # noqa: E402
from shabbos_goy.lobes.config import (  # noqa: E402
    ENV_API_KEY,
    ENV_API_KEY_FALLBACK,
    ENV_URL,
    SUPPORTED_SAMPLE_RATES,
    LobesConfigError,
)
from shabbos_goy.policy import CLASSES, may_act  # noqa: E402

HERE = Path(__file__).resolve().parent
MANIFEST_PATH = HERE / "manifest.jsonl"
THRESHOLDS_PATH = HERE / "thresholds.json"
ASR_CACHE_PATH = HERE / "asr_cache.json"
AUDIO_DIR = HERE / "audio"
REPLAY_PATH = REPO_ROOT / "tests" / "fixtures" / "decider" / "golden_replay.json"

ENTRANCES: tuple[str, ...] = ("text", "audio-batch", "audio-realtime")
AUDIO_ENTRANCES: tuple[str, ...] = ("audio-batch", "audio-realtime")
MODES: tuple[str, ...] = ("strict", "weekday")
CATEGORIES: tuple[str, ...] = ("hint", "command", "negative")

#: An intent that names something to do. ``none`` is the decider saying "there
#: is nothing here to act on", so it never counts as acting -- which is also
#: how the manifest labels a hint with no whitelisted actuator (darkness).
ACTING_INTENTS: tuple[str, ...] = tuple(i for i in INTENTS if i != "none")

#: Reason codes that mean "the decider did not produce an answer". Everything
#: the deciders return on a failure path is in here; ``http_*`` is matched by
#: prefix because the code is part of the reason.
FAILURE_REASONS: frozenset[str] = frozenset(
    {
        "no_decision",
        "timeout",
        "connect_error",
        "body_too_large",
        "bad_encoding",
        "bad_envelope",
        "no_choices",
        "bad_payload",
        "extra_keys",
        "missing_field",
        "bad_class",
        "bad_intent",
        "bad_confidence",
        "not_recorded",
        "bad_record",
        "bad_oracle_verdict",
    }
)

# CLI contract: 0 ok, 1 user error, 2 environment error, 3+ reserved.
EXIT_OK = 0
EXIT_USER = 1
EXIT_ENVIRONMENT = 2
EXIT_THRESHOLD = 4

HEALTH_PATH = "/health"
SPEECH_PATH = "/v1/audio/speech"
TRANSCRIPTIONS_PATH = "/v1/audio/transcriptions"
DEFAULT_LANGUAGE = "he"


class GoldenError(RuntimeError):
    """A golden-set step could not be completed (never an actuation failure)."""


# ---------------------------------------------------------------------------
# the manifest
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GoldenRow:
    """One golden utterance and the OUTCOME it expects (see build_manifest.py)."""

    id: str
    text: str
    category: str
    subcategory: str
    classes: list[str]
    intent: str | None
    act_strict: bool
    act_weekday: bool | None

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> "GoldenRow":
        return cls(
            id=str(payload.get("id", "")),
            text=str(payload.get("text", "")),
            category=str(payload.get("category", "")),
            subcategory=str(payload.get("subcategory", "")),
            classes=list(payload.get("classes") or []),
            intent=payload.get("intent"),
            act_strict=bool(payload.get("act_strict")),
            act_weekday=payload.get("act_weekday"),
        )

    def expects_action(self, mode: str) -> bool:
        """Whether this row should act in *mode* (``None`` weekday = not checked)."""
        if mode == "strict":
            return bool(self.act_strict)
        return self.act_weekday is True


def load_manifest(path: str | Path = MANIFEST_PATH) -> list[GoldenRow]:
    lines = Path(path).read_text("utf-8").splitlines()
    return [GoldenRow.from_json(json.loads(line)) for line in lines if line.strip()]


def validate_manifest(rows: Sequence[GoldenRow]) -> list[str]:
    """Everything wrong with the manifest, as readable strings (empty = fine)."""
    problems: list[str] = []
    seen: set[str] = set()
    for row in rows:
        if not row.id:
            problems.append("a row has no id")
            continue
        if row.id in seen:
            problems.append(f"{row.id}: duplicate id")
        seen.add(row.id)
        if not row.text.strip():
            problems.append(f"{row.id}: empty text")
        if row.category not in CATEGORIES:
            problems.append(f"{row.id}: unknown category {row.category!r}")
        if not row.classes:
            problems.append(f"{row.id}: no acceptable class listed")
        for klass in row.classes:
            if klass not in CLASSES:
                problems.append(f"{row.id}: unknown class {klass!r}")
        if row.intent is not None and row.intent not in INTENTS:
            problems.append(f"{row.id}: unknown intent {row.intent!r}")
        if not isinstance(row.act_strict, bool):
            problems.append(f"{row.id}: act_strict must be a boolean")
        if row.act_weekday is not None and not isinstance(row.act_weekday, bool):
            problems.append(f"{row.id}: act_weekday must be a boolean or null")
        if row.act_strict and row.category == "hint" and row.intent not in ACTING_INTENTS:
            problems.append(f"{row.id}: a hint that must act needs an actionable intent")
        if not row.act_strict:
            justified = row.category in ("command", "negative") or (
                row.category == "hint" and row.intent == "none"
            )
            if not justified:
                problems.append(
                    f"{row.id}: act_strict is false but the category "
                    f"({row.category}/{row.intent}) does not justify it"
                )
        if row.act_strict and row.act_weekday is False:
            problems.append(f"{row.id}: acts on Shabbat but not on a weekday, which is backwards")
    return problems


# ---------------------------------------------------------------------------
# outcomes and scoring
# ---------------------------------------------------------------------------


@dataclass
class RowResult:
    """What one manifest row produced on one entrance, in one mode."""

    row_id: str
    transcripts: list[str]
    decisions: list[Decision]
    latency_ms: float = 0.0
    error: str | None = None


def decision_acts(decision: Decision, mode: str) -> bool:
    """Would this repo's code act on *decision* in *mode*?

    Two gates, in the order the pipeline applies them: the mode x class table
    (:func:`shabbos_goy.policy.may_act`) and then whether the intent names
    anything to do at all.
    """
    return may_act(mode, decision.klass) and decision.intent in ACTING_INTENTS


def is_decider_failure(decision: Decision) -> bool:
    """True when the decider produced no answer (a timeout, junk, no record)."""
    return decision.reason.startswith("http_") or decision.reason in FAILURE_REASONS


def percentile(values: Sequence[float], q: float) -> float | None:
    """Linear-interpolated percentile; ``None`` for an empty sample."""
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    position = (len(ordered) - 1) * q
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    weight = position - low
    return float(ordered[low] * (1 - weight) + ordered[high] * weight)


def _rate(numerator: int, denominator: int) -> float | None:
    return None if denominator == 0 else numerator / denominator


def score(
    rows: Sequence[GoldenRow],
    results: Sequence[RowResult],
    mode: str,
    entrance: str,
) -> dict[str, Any]:
    """Score one entrance x mode. HARD failures first, everything else after."""
    by_id = {result.row_id: result for result in results}
    hard_failures: list[str] = []
    wrong_actions: list[str] = []
    hard_rows = 0
    hint_expected = hint_ok = 0
    weekday_expected = weekday_ok = 0
    labels_checked = labels_ok = 0
    failures = decisions_seen = 0
    latencies: list[float] = []
    silent: list[str] = []
    scored_rows = 0

    for row in rows:
        result = by_id.get(row.id)
        if result is None:
            continue
        scored_rows += 1
        latencies.append(result.latency_ms)
        if not result.transcripts:
            silent.append(row.id)
        acting = [d for d in result.decisions if decision_acts(d, mode)]
        for decision in result.decisions:
            decisions_seen += 1
            if is_decider_failure(decision):
                failures += 1
        # One utterance in, one label out: only then is the label itself
        # comparable with the row's acceptable set.
        if len(result.decisions) == 1 and not is_decider_failure(result.decisions[0]):
            labels_checked += 1
            if result.decisions[0].klass in row.classes:
                labels_ok += 1

        if mode == "strict" and not row.act_strict:
            hard_rows += 1
            if acting:
                # ANY utterance of this row acting is a violation: a row split
                # into halves passes only if none of them acts.
                hard_failures.append(row.id)

        intent_ok = row.intent is None or any(d.intent == row.intent for d in acting)
        if row.intent is not None and any(d.intent != row.intent for d in acting):
            # It acted, in a direction the row does not allow: the AC goes ON for
            # a cold person. That is a false action, never just a missed hint.
            wrong_actions.append(row.id)
        if row.category == "hint" and row.expects_action(mode):
            hint_expected += 1
            if acting and intent_ok:
                hint_ok += 1
        if mode == "weekday" and row.category == "command" and row.act_weekday is True:
            weekday_expected += 1
            if acting and intent_ok:
                weekday_ok += 1

    return {
        "entrance": entrance,
        "mode": mode,
        "rows": scored_rows,
        "hard_rows": hard_rows,
        "hard_false_positives": hard_failures,
        "wrong_actions": wrong_actions,
        "hint_recall": {
            "expected": hint_expected,
            "acted_right": hint_ok,
            "rate": _rate(hint_ok, hint_expected),
        },
        "weekday_obedience": {
            "expected": weekday_expected,
            "obeyed": weekday_ok,
            "rate": _rate(weekday_ok, weekday_expected),
        },
        "label_acceptability": {
            "checked": labels_checked,
            "ok": labels_ok,
            "rate": _rate(labels_ok, labels_checked),
        },
        "latency_ms": {"p50": percentile(latencies, 0.50), "p95": percentile(latencies, 0.95)},
        "decider_failures": failures,
        "decider_failure_rate": (failures / decisions_seen) if decisions_seen else 0.0,
        "silent_rows": silent,
    }


# ---------------------------------------------------------------------------
# thresholds and the report
# ---------------------------------------------------------------------------


def load_thresholds(path: str | Path = THRESHOLDS_PATH) -> dict[str, Any]:
    return json.loads(Path(path).read_text("utf-8"))


def check_thresholds(
    scores: Iterable[Mapping[str, Any]], thresholds: Mapping[str, Any]
) -> list[str]:
    """Every threshold the run missed, as readable strings (empty = a pass)."""
    max_hard = int(thresholds.get("hard_false_positives_strict", 0))
    min_recall = float(thresholds.get("hint_recall_min", 0.0))
    max_failure_rate = float(thresholds.get("decider_failure_rate_max", 1.0))
    violations: list[str] = []
    for scored in scores:
        tag = f"{scored.get('entrance')}/{scored.get('mode')}"
        if scored.get("mode") == "strict":
            hard = list(scored.get("hard_false_positives") or [])
            if len(hard) > max_hard:
                violations.append(
                    f"hard_false_positives({tag})={len(hard)} > {max_hard}: "
                    f"{', '.join(hard[:10])}"
                )
            wrong = list(scored.get("wrong_actions") or [])
            max_wrong = int(thresholds.get("wrong_actions_max", 0))
            if len(wrong) > max_wrong:
                violations.append(
                    f"wrong_actions({tag})={len(wrong)} > {max_wrong}: {', '.join(wrong[:10])}"
                )
            recall = scored.get("hint_recall") or {}
            if recall.get("expected"):
                rate = float(recall.get("rate") or 0.0)
                if rate < min_recall:
                    violations.append(f"hint_recall({tag})={rate:.2f} < {min_recall:.2f}")
        rate = float(scored.get("decider_failure_rate") or 0.0)
        if rate > max_failure_rate:
            violations.append(f"decider_failure_rate({tag})={rate:.2f} > {max_failure_rate:.2f}")
    return violations


def exit_code(violations: Sequence[str]) -> int:
    return EXIT_THRESHOLD if violations else EXIT_OK


def build_report(
    scores: Sequence[Mapping[str, Any]],
    violations: Sequence[str],
    model: str,
    lobes_health: Mapping[str, Any],
    manifest_rows: int,
    thresholds: Mapping[str, Any],
) -> dict[str, Any]:
    """The JSON report. It names the prompt, the model and lobes on purpose:
    a golden result that cannot say what produced it is not evidence."""
    return {
        "date": date.today().isoformat(),
        "prompt_version": PROMPT_VERSION,
        "model": model,
        "lobes": dict(lobes_health),
        "manifest_rows": manifest_rows,
        "thresholds": dict(thresholds),
        "violations": list(violations),
        "entrances": [dict(scored) for scored in scores],
    }


def _pct(value: Any) -> str:
    return "n/a" if value is None else f"{float(value) * 100:.1f}%"


def _ms(value: Any) -> str:
    return "n/a" if value is None else f"{float(value):.0f}ms"


def render_table(report: Mapping[str, Any]) -> str:
    """The readable table. HARD failures are printed first, always."""
    lines = [
        f"golden set  {report.get('date')}  prompt={report.get('prompt_version')}  "
        f"model={report.get('model')}  lobes={json.dumps(report.get('lobes') or {})}",
        f"manifest rows: {report.get('manifest_rows')}",
    ]
    for scored in report.get("entrances") or []:
        hard = list(scored.get("hard_false_positives") or [])
        hard_rows = scored.get("hard_rows", 0)
        lines.append("")
        lines.append(
            f"[{scored.get('entrance')} / {scored.get('mode')}]  rows={scored.get('rows')}"
        )
        if hard:
            lines.append(f"  HARD failures (acted on a must-not-act row): {len(hard)}/{hard_rows}")
            for row_id in hard:
                lines.append(f"    HARD  {row_id}")
        else:
            lines.append(f"  HARD failures: 0/{hard_rows} - no HARD failures")
        wrong = list(scored.get("wrong_actions") or [])
        if wrong:
            lines.append(
                f"  WRONG-DIRECTION actions (acted with an intent the row forbids): {len(wrong)}"
            )
            for row_id in wrong:
                lines.append(f"    - {row_id}")
        recall = scored.get("hint_recall") or {}
        lines.append(
            f"  hint recall: {recall.get('acted_right')}/{recall.get('expected')} "
            f"({_pct(recall.get('rate'))})"
        )
        obedience = scored.get("weekday_obedience") or {}
        lines.append(
            f"  weekday command obedience: {obedience.get('obeyed')}/"
            f"{obedience.get('expected')} ({_pct(obedience.get('rate'))})"
        )
        labels = scored.get("label_acceptability") or {}
        lines.append(
            f"  label acceptability: {labels.get('ok')}/{labels.get('checked')} "
            f"({_pct(labels.get('rate'))})"
        )
        latency = scored.get("latency_ms") or {}
        lines.append(f"  latency: p50 {_ms(latency.get('p50'))}  p95 {_ms(latency.get('p95'))}")
        lines.append(
            f"  decider failures: {scored.get('decider_failures')} "
            f"({_pct(scored.get('decider_failure_rate'))})"
        )
        silent = list(scored.get("silent_rows") or [])
        if silent:
            lines.append(f"  rows with no transcript: {len(silent)} ({', '.join(silent[:10])})")
    violations = list(report.get("violations") or [])
    lines.append("")
    if violations:
        lines.append("THRESHOLDS MISSED:")
        lines.extend(f"  {violation}" for violation in violations)
    else:
        lines.append("thresholds: all met")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# recording and the ASR cache
# ---------------------------------------------------------------------------


def record_decisions(
    results_by_entrance: Mapping[str, Sequence[RowResult]],
    path: str | Path = REPLAY_PATH,
) -> int:
    """Write entrance -> utterance -> decision so CI can replay the real model.

    Keyed BY ENTRANCE, not by text alone. Two entrances routinely produce the
    same transcript string -- the manifest text and an ASR transcript that
    happens to match it -- and a flat text key let a later entrance's answer
    overwrite an earlier one. That silently turned a recorded run that FAILED
    the text entrance into a fixture that passes (risk r13, 2026-09-20): the
    run reported hard_false_positives(text/strict)=1 for k-n_fragment_13 while
    the fixture stored that text as unrelated, because audio-realtime answered
    second. A gate that cannot see the failure it recorded is not a gate.

    Merges into whatever is already there per entrance, and never records a
    failure: a timeout is not an answer, and replaying it as one would invent
    evidence.
    """
    target = Path(path)
    records: dict[str, Any] = {}
    if target.exists():
        existing = json.loads(target.read_text("utf-8"))
        if isinstance(existing, dict):
            # A pre-r13 flat file is read as the text entrance's records, which
            # is what it mostly was, rather than silently discarded.
            if existing and all(isinstance(v, dict) and "class" in v for v in existing.values()):
                records = {"text": existing}
            else:
                records = existing
    written = 0
    for entrance, results in results_by_entrance.items():
        bucket = records.setdefault(entrance, {})
        for result in results:
            for text, decision in zip(result.transcripts, result.decisions):
                if not text.strip() or is_decider_failure(decision):
                    continue
                bucket[text.strip()] = {
                    "class": decision.klass,
                    "intent": decision.intent,
                    "confidence": round(float(decision.confidence), 3),
                }
                written += 1
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(records, ensure_ascii=False, indent=2, sort_keys=True) + "\n", "utf-8"
    )
    return written


def load_asr_cache(path: str | Path = ASR_CACHE_PATH) -> dict[str, dict[str, list[str]]]:
    target = Path(path)
    if not target.exists():
        return {}
    data = json.loads(target.read_text("utf-8"))
    return data if isinstance(data, dict) else {}


def save_asr_cache(path: str | Path, cache: Mapping[str, Mapping[str, Sequence[str]]]) -> None:
    """Merge *cache* into the committed ASR cache, entrance by entrance."""
    merged = load_asr_cache(path)
    for entrance, rows in cache.items():
        bucket = dict(merged.get(entrance) or {})
        bucket.update({row_id: list(texts) for row_id, texts in rows.items()})
        merged[entrance] = bucket
    Path(path).write_text(
        json.dumps(merged, ensure_ascii=False, indent=2, sort_keys=True) + "\n", "utf-8"
    )


# ---------------------------------------------------------------------------
# the batch audio endpoints
# ---------------------------------------------------------------------------


def normalise_http_base(raw: str) -> str:
    """``ws(s)://host:port/x`` or ``http(s)://...`` -> ``http(s)://host:port``."""
    parsed = urlsplit(raw.strip())
    mapping = {"ws": "http", "http": "http", "wss": "https", "https": "https"}
    scheme = mapping.get(parsed.scheme.lower())
    if scheme is None or not parsed.hostname:
        raise GoldenError(f"not a usable lobes URL: {raw!r}")
    netloc = parsed.hostname if parsed.port is None else f"{parsed.hostname}:{parsed.port}"
    return urlunsplit((scheme, netloc, "", "", ""))


def speech_body(text: str, voice: str | None = None, response_format: str = "wav") -> bytes:
    """The ``POST /v1/audio/speech`` JSON body (wav: the only self-describing one)."""
    payload: dict[str, Any] = {"input": text, "response_format": response_format}
    if voice:
        payload["voice"] = voice
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def encode_multipart(
    fields: Mapping[str, str],
    *,
    filename: str,
    content: bytes,
    content_type: str = "audio/wav",
    field_name: str = "file",
    boundary: str | None = None,
) -> tuple[bytes, str]:
    """Build a ``multipart/form-data`` body: plain fields plus one file part."""
    marker = boundary or uuid.uuid4().hex
    parts: list[bytes] = []
    for name, value in fields.items():
        parts.append(
            f'--{marker}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'
            f"{value}\r\n".encode("utf-8")
        )
    parts.append(
        (
            f'--{marker}\r\nContent-Disposition: form-data; name="{field_name}"; '
            f'filename="{filename}"\r\nContent-Type: {content_type}\r\n\r\n'
        ).encode("utf-8")
    )
    parts.append(content)
    parts.append(f"\r\n--{marker}--\r\n".encode("utf-8"))
    return b"".join(parts), f"multipart/form-data; boundary={marker}"


class LobesBatchClient:
    """The two batch audio endpoints plus ``/health``. Stdlib, no key on disk."""

    def __init__(
        self,
        base_url: str,
        api_key: str | None = None,
        timeout: float = 120.0,
        language: str = DEFAULT_LANGUAGE,
    ) -> None:
        self.base_url = normalise_http_base(base_url)
        self._api_key = api_key
        self.timeout = timeout
        self.language = language

    def __repr__(self) -> str:
        return (
            f"LobesBatchClient(base_url={self.base_url!r}, "
            f"api_key=<{'set' if self._api_key else 'unset'}>)"
        )

    __str__ = __repr__

    # -- transport --------------------------------------------------------
    def _headers(self, extra: Mapping[str, str] | None = None) -> dict[str, str]:
        headers = dict(extra or {})
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    def _open(self, path: str, data: bytes | None, headers: Mapping[str, str]) -> bytes:
        request = urllib.request.Request(  # nosec B310 - scheme fixed by normalise_http_base
            self.base_url + path,
            data=data,
            headers=dict(headers),
            method="POST" if data is not None else "GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:  # nosec B310
                return response.read()
        except urllib.error.HTTPError as exc:
            raise GoldenError(f"{path} answered HTTP {exc.code}") from exc
        except (urllib.error.URLError, OSError, socket.timeout) as exc:
            raise GoldenError(f"{path} is unreachable: {type(exc).__name__}") from exc

    # -- endpoints --------------------------------------------------------
    def health(self) -> dict[str, Any]:
        """``GET /health``. Never fatal: a run without it is just less labelled."""
        try:
            raw = self._open(HEALTH_PATH, None, self._headers())
            body = json.loads(raw.decode("utf-8"))
        except (GoldenError, ValueError, UnicodeDecodeError):
            return {}
        return body if isinstance(body, dict) else {}

    def synthesize(self, text: str, voice: str | None = None) -> bytes:
        """``POST /v1/audio/speech`` -> WAV bytes."""
        headers = self._headers({"Content-Type": "application/json", "Accept": "audio/wav"})
        audio = self._open(SPEECH_PATH, speech_body(text, voice), headers)
        if not audio:
            raise GoldenError("the TTS endpoint returned no audio")
        return audio

    def transcribe(self, wav: bytes, language: str | None = None, filename: str = "row.wav") -> str:
        """``POST /v1/audio/transcriptions`` (multipart) -> the transcript text."""
        body, content_type = encode_multipart(
            {"language": language or self.language},
            filename=filename,
            content=wav,
        )
        headers = self._headers({"Content-Type": content_type, "Accept": "application/json"})
        raw = self._open(TRANSCRIPTIONS_PATH, body, headers)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise GoldenError("the STT endpoint returned a non-JSON body") from exc
        text = payload.get("text") if isinstance(payload, dict) else None
        return text if isinstance(text, str) else ""


# ---------------------------------------------------------------------------
# WAV handling
# ---------------------------------------------------------------------------


def read_wav(path: str | Path) -> tuple[bytes, int]:
    """Read a mono 16-bit WAV as ``(pcm, sample_rate)``."""
    try:
        with wave.open(str(path), "rb") as handle:
            channels = handle.getnchannels()
            width = handle.getsampwidth()
            rate = handle.getframerate()
            pcm = handle.readframes(handle.getnframes())
    except (wave.Error, EOFError, OSError) as exc:
        raise GoldenError(f"{Path(path).name} is not a readable WAV: {exc}") from exc
    if channels != 1 or width != 2:
        raise GoldenError(
            f"{Path(path).name} must be mono 16-bit PCM, got {channels}ch/{width * 8}bit"
        )
    return pcm, rate


def chunk_pcm(pcm: bytes, rate: int, chunk_ms: int = 20) -> list[bytes]:
    """Split PCM16 into frame-aligned chunks of about *chunk_ms* each."""
    samples = max(1, (rate * chunk_ms) // 1000)
    size = samples * 2
    return [pcm[start : start + size] for start in range(0, len(pcm), size)] or [b""]


def synthesize_all(
    rows: Sequence[GoldenRow],
    client: LobesBatchClient,
    audio_dir: str | Path = AUDIO_DIR,
    voice: str | None = None,
) -> int:
    """TTS every row into ``<audio_dir>/<id>.wav``, skipping files that exist.

    The audio is gitignored and regenerable on purpose (deviation d4): it is
    derived data, and a committed voice would also be a redistribution of the
    TTS weights this repo has no licence for.
    """
    directory = Path(audio_dir)
    directory.mkdir(parents=True, exist_ok=True)
    written = 0
    for row in rows:
        target = directory / f"{row.id}.wav"
        if target.exists():
            continue
        target.write_bytes(client.synthesize(row.text, voice))
        written += 1
    return written


# ---------------------------------------------------------------------------
# the entrances
# ---------------------------------------------------------------------------

#: A row's transcripts plus, when something went wrong, a named reason.
Produce = Callable[[GoldenRow], tuple[list[str], str | None]]


def _run(
    rows: Sequence[GoldenRow],
    decider: Any,
    mode: str,
    produce: Produce,
    clock: Callable[[], float] = time.perf_counter,
) -> list[RowResult]:
    results: list[RowResult] = []
    for row in rows:
        started = clock()
        texts, error = produce(row)
        decisions: list[Decision] = []
        for text in texts:
            # A fresh, empty window per utterance: the golden set measures one
            # utterance at a time, never a conversation that primed the model.
            decisions.append(decider.decide(text, ContextWindow(), mode=mode))
        results.append(
            RowResult(
                row_id=row.id,
                transcripts=list(texts),
                decisions=decisions,
                latency_ms=(clock() - started) * 1000.0,
                error=error,
            )
        )
    return results


def run_decider(
    rows: Sequence[GoldenRow],
    decider: Any,
    mode: str,
    transcripts_for: Callable[[GoldenRow], Sequence[str]],
    clock: Callable[[], float] = time.perf_counter,
) -> list[RowResult]:
    """The ``text`` entrance, and the shared core of the audio ones."""
    return _run(rows, decider, mode, lambda row: (list(transcripts_for(row)), None), clock)


def run_audio_batch(
    rows: Sequence[GoldenRow],
    decider: Any,
    mode: str,
    client: LobesBatchClient | None,
    audio_dir: str | Path = AUDIO_DIR,
) -> tuple[list[RowResult], dict[str, list[str]]]:
    """Entrance 2: the row's WAV -> ``/v1/audio/transcriptions`` -> the decider."""
    directory = Path(audio_dir)
    cache: dict[str, list[str]] = {}

    def produce(row: GoldenRow) -> tuple[list[str], str | None]:
        path = directory / f"{row.id}.wav"
        if not path.exists():
            return [], f"no audio for this row: {path.name} (run the tts step first)"
        if client is None:  # pragma: no cover - guarded by the caller
            return [], "no lobes client configured"
        try:
            text = client.transcribe(path.read_bytes(), filename=path.name)
        except GoldenError as exc:
            return [], str(exc)
        if not text.strip():
            # Empty transcripts are dropped noise, exactly as in the listener.
            return [], "empty transcript"
        cache[row.id] = [text.strip()]
        return [text.strip()], None

    return _run(rows, decider, mode, produce), cache


def stream_wav(
    config: LobesConfig,
    pcm: bytes,
    rate: int,
    *,
    chunk_ms: int = 20,
    gap_threshold_ms: int = 500,
    trailing_silence_ms: int = 700,
    quiet_after_audio: float = 1.5,
    timeout_seconds: float = 60.0,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> list[str]:
    """Stream one WAV through the ears-only session and return its utterances.

    Nothing but ``input_audio_buffer.append`` goes on the wire -- the client
    refuses anything else in code -- and the transcripts are joined by the
    same :class:`~shabbos_goy.joiner.TranscriptJoiner` the listener uses, so a
    pause-split sentence is scored as the one utterance it was.
    """
    utterances: list[str] = []
    joiner = TranscriptJoiner(gap_threshold_ms=gap_threshold_ms, on_utterance=utterances.append)
    silence = b"\x00\x00" * max(0, (rate * trailing_silence_ms) // 1000)
    chunks = deque(chunk_pcm(pcm, rate, chunk_ms) + chunk_pcm(silence, rate, chunk_ms))
    state: dict[str, Any] = {
        "last_event": clock(),
        "max_at_ms": None,
        "audio_done": False,
        "error": None,
        "started": clock(),
        "sent": 0,
    }

    def audio_source() -> bytes | None:
        if not chunks:
            state["audio_done"] = True
            return None
        # Pace the stream at real time: the server's VAD reasons about
        # silence, so a blasted file is not the signal a room is.
        due = state["started"] + (state["sent"] * chunk_ms / 1000.0)
        if clock() < due:
            return b""
        state["sent"] += 1
        return chunks.popleft()

    def on_event(event: Any) -> None:
        state["last_event"] = clock()
        if event.at_ms is not None:
            current = state["max_at_ms"]
            state["max_at_ms"] = event.at_ms if current is None else max(current, event.at_ms)
        joiner.handle_event(
            {
                "type": event.type,
                "at_ms": event.at_ms,
                "item_id": event.item_id,
                "text": event.text,
            }
        )

    client = LobesClient(
        replace(config, input_sample_rate=rate),
        on_event,
        audio_source=audio_source,
        max_connections=1,
        read_timeout=0.05,
        watchdog_seconds=timeout_seconds + 30.0,
        exit_action=lambda code: None,
    )

    def session() -> None:
        try:
            client.run_once()
        except Exception as exc:  # noqa: BLE001 - reported as a GoldenError below
            state["error"] = f"{type(exc).__name__}: {exc}"

    thread = threading.Thread(target=session, name="golden-stream", daemon=True)
    thread.start()
    deadline = clock() + timeout_seconds
    while clock() < deadline:
        if state["error"] is not None:
            break
        joiner.poll()
        if state["audio_done"] and (clock() - state["last_event"]) >= quiet_after_audio:
            break
        sleep(0.02)
    client.stop()
    thread.join(timeout=5.0)
    if state["error"] is not None:
        raise GoldenError(str(state["error"]))
    # The file has ended, so there is no continuation to wait for: close any
    # pending chain on whichever timeline it is waiting on.
    if state["max_at_ms"] is not None:
        joiner.poll(now_ms=int(state["max_at_ms"]) + gap_threshold_ms + 1)
    joiner.poll()
    return utterances


def run_audio_realtime(
    rows: Sequence[GoldenRow],
    decider: Any,
    mode: str,
    config: LobesConfig,
    audio_dir: str | Path = AUDIO_DIR,
    **stream_kwargs: Any,
) -> tuple[list[RowResult], dict[str, list[str]]]:
    """Entrance 3: the row's WAV -> the ears-only session + joiner -> the decider."""
    directory = Path(audio_dir)
    cache: dict[str, list[str]] = {}

    def produce(row: GoldenRow) -> tuple[list[str], str | None]:
        path = directory / f"{row.id}.wav"
        if not path.exists():
            return [], f"no audio for this row: {path.name} (run the tts step first)"
        try:
            pcm, rate = read_wav(path)
            if rate not in SUPPORTED_SAMPLE_RATES:
                return [], f"{path.name} is {rate} Hz; lobes accepts {SUPPORTED_SAMPLE_RATES}"
            texts = [t.strip() for t in stream_wav(config, pcm, rate, **stream_kwargs) if t.strip()]
        except GoldenError as exc:
            return [], str(exc)
        if texts:
            cache[row.id] = texts
        return texts, None

    return _run(rows, decider, mode, produce), cache


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="golden-set",
        description=(
            "Run the committed golden manifest against a decider and score the outcome. "
            "Measures only: it never actuates anything."
        ),
    )
    parser.add_argument(
        "--entrance",
        choices=("text", "tts", *ENTRANCES, "all"),
        default="text",
        help="which entrance to run ('tts' only synthesises the audio files)",
    )
    parser.add_argument("--mode", choices=(*MODES, "both"), default="both")
    parser.add_argument(
        "--decider",
        choices=("gemma", "replay", "oracle"),
        default="gemma",
        help="gemma = the real model; replay = recorded answers; oracle = the t10 rules",
    )
    parser.add_argument("--replay", default=str(REPLAY_PATH), help="the replay file to read")
    parser.add_argument("--audio-dir", default=str(AUDIO_DIR))
    parser.add_argument("--thresholds", default=str(THRESHOLDS_PATH))
    parser.add_argument("--only", action="append", default=None, help="run only this row id")
    parser.add_argument("--limit", type=int, default=None, help="run only the first N rows")
    parser.add_argument("--out", default=None, help="write the JSON report here")
    parser.add_argument("--json", action="store_true", help="print the JSON report to stdout")
    parser.add_argument(
        "--record",
        nargs="?",
        const=str(REPLAY_PATH),
        default=None,
        help="record the decisions to a replay file CI can use",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="do not update tests/golden/asr_cache.json",
    )
    parser.add_argument("--voice", default=None, help="TTS voice (empty = the default one)")
    return parser


def _select_rows(rows: Sequence[GoldenRow], args: argparse.Namespace) -> list[GoldenRow]:
    selected = list(rows)
    if args.only:
        wanted = set(args.only)
        selected = [row for row in selected if row.id in wanted]
    if args.limit is not None:
        selected = selected[: args.limit]
    return selected


def _build_decider(args: argparse.Namespace, env: Mapping[str, str]) -> tuple[Any, str]:
    if args.decider == "oracle":
        from shabbos_goy.decider.oracle import RuleOracle  # local: tests-only import

        return RuleOracle(), "rule-oracle"
    if args.decider == "replay":
        return ReplayDecider.from_file(args.replay), "replay"
    config = senses_config_from_env(env)
    return GemmaDecider(config), config.model


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    env = os.environ
    entrances = (
        ["tts", "text", "audio-batch", "audio-realtime"]
        if args.entrance == "all"
        else [args.entrance]
    )
    modes = list(MODES) if args.mode == "both" else [args.mode]
    needs_lobes = any(entrance in ("tts", *AUDIO_ENTRANCES) for entrance in entrances)
    if (needs_lobes or args.decider == "gemma") and not (
        (env.get(ENV_URL) or "").strip() or (env.get(ENV_SENSES_URL) or "").strip()
    ):
        print(
            f"{ENV_URL} is not set: the golden set's live entrances need a real lobes "
            f"gateway (set {ENV_URL}, and {ENV_API_KEY} or {ENV_API_KEY_FALLBACK}). "
            "Run it on the box, never in CI.",
            file=sys.stderr,
        )
        return EXIT_ENVIRONMENT
    if needs_lobes and not (env.get(ENV_URL) or "").strip():
        print(f"{ENV_URL} is not set: the audio entrances need it", file=sys.stderr)
        return EXIT_ENVIRONMENT

    rows = _select_rows(load_manifest(), args)
    problems = validate_manifest(rows)
    if problems:
        for problem in problems:
            print(f"manifest: {problem}", file=sys.stderr)
        return EXIT_USER

    try:
        decider, model = _build_decider(args, env)
    except (LobesConfigError, OSError, ValueError) as exc:
        print(f"cannot build the decider: {exc}", file=sys.stderr)
        return EXIT_ENVIRONMENT

    client: LobesBatchClient | None = None
    lobes_config: LobesConfig | None = None
    if needs_lobes:
        try:
            lobes_config = config_from_env(env)
            client = LobesBatchClient(
                (env.get(ENV_URL) or "").strip(),
                api_key=env.get(ENV_API_KEY) or env.get(ENV_API_KEY_FALLBACK) or None,
                language=lobes_config.language,
            )
        except (LobesConfigError, GoldenError) as exc:
            print(f"cannot reach lobes: {exc}", file=sys.stderr)
            return EXIT_ENVIRONMENT

    scores: list[dict[str, Any]] = []
    all_results: list[RowResult] = []
    # Per entrance, so the recorded fixture cannot let one entrance's answer
    # overwrite another's for the same transcript string (risk r13).
    recorded_by_entrance: dict[str, list[RowResult]] = {}
    cache: dict[str, dict[str, list[str]]] = {}
    for entrance in entrances:
        if entrance == "tts":
            assert client is not None
            written = synthesize_all(rows, client, args.audio_dir, args.voice)
            print(f"tts: wrote {written} new wav file(s) to {args.audio_dir}", file=sys.stderr)
            continue
        for mode in modes:
            if entrance == "text":
                results = run_decider(rows, decider, mode, lambda row: [row.text])
            elif entrance == "audio-batch":
                results, produced = run_audio_batch(
                    rows, decider, mode, client, audio_dir=args.audio_dir
                )
                cache.setdefault(entrance, {}).update(produced)
            else:
                assert lobes_config is not None
                results, produced = run_audio_realtime(
                    rows, decider, mode, lobes_config, audio_dir=args.audio_dir
                )
                cache.setdefault(entrance, {}).update(produced)
            all_results.extend(results)
            recorded_by_entrance.setdefault(entrance, []).extend(results)
            scores.append(score(rows, results, mode=mode, entrance=entrance))

    thresholds = load_thresholds(args.thresholds)
    violations = check_thresholds(scores, thresholds)
    report = build_report(
        scores=scores,
        violations=violations,
        model=model,
        lobes_health=client.health() if client is not None else {},
        manifest_rows=len(rows),
        thresholds=thresholds,
    )
    if args.out:
        Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", "utf-8")
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(render_table(report))
    if args.record:
        written = record_decisions(recorded_by_entrance, args.record)
        print(f"recorded {written} decision(s) to {args.record}", file=sys.stderr)
    if cache and not args.no_cache:
        save_asr_cache(ASR_CACHE_PATH, cache)
    return exit_code(violations)


if __name__ == "__main__":  # pragma: no cover - exercised as a subprocess
    raise SystemExit(main())

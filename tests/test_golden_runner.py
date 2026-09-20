"""Offline tests for the golden-set runner (deviation d4).

Everything here runs with no microphone, no lobes server, no key and no
network beyond ``127.0.0.1``: the live entrances are exercised against the
in-process fakes (``tests/golden/fake_batch_server.py`` for the batch audio
endpoints, ``tests/lobes_fake_server.py`` for the realtime WebSocket,
``tests/decider_fake_server.py`` for the ``senses`` chat endpoint), and the
scoring, threshold and report logic is pure.

The live run itself is the ``golden`` marker's job (``tests/test_golden_live.py``)
and never runs in CI.
"""

from __future__ import annotations

import json
import subprocess  # nosec B404 - runs this repo's own script with a fixed argv
import sys
import tomllib
import wave
from pathlib import Path

import pytest

from shabbos_goy.decider import ContextWindow, Decision, ReplayDecider
from shabbos_goy.decider.replay import REPLAY_FORMAT, entrance_buckets
from shabbos_goy.lobes.config import LobesConfig
from tests.decider_fake_server import FakeSensesServer, ScriptedResponse, decision_body
from tests.golden import runner as gr
from tests.golden.fake_batch_server import FakeBatchAudioServer, marker_wav, parse_multipart
from tests.lobes_fake_server import FakeLobesServer

REPO_ROOT = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def row(
    row_id: str = "r1",
    text: str = "חם פה",
    category: str = "hint",
    classes: tuple[str, ...] = ("remark", "wish", "discomfort"),
    intent: str | None = "cool",
    act_strict: bool = True,
    act_weekday: bool | None = True,
    subcategory: str = "heat",
) -> gr.GoldenRow:
    return gr.GoldenRow(
        id=row_id,
        text=text,
        category=category,
        subcategory=subcategory,
        classes=list(classes),
        intent=intent,
        act_strict=act_strict,
        act_weekday=act_weekday,
    )


def decision(klass: str = "remark", intent: str = "cool", reason: str = "ok") -> Decision:
    return Decision(klass=klass, intent=intent, confidence=0.9, source="test", reason=reason)


def result(
    row_id: str = "r1",
    decisions: tuple[Decision, ...] = (),
    transcripts: tuple[str, ...] = ("חם פה",),
    latency_ms: float = 10.0,
) -> gr.RowResult:
    return gr.RowResult(
        row_id=row_id,
        transcripts=list(transcripts),
        decisions=list(decisions or (decision(),)),
        latency_ms=latency_ms,
    )


# --------------------------------------------------------------------------
# the act/decide table
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "klass,intent,mode,expected",
    [
        ("remark", "cool", "strict", True),
        ("wish", "warm", "strict", True),
        ("discomfort", "quieter", "strict", True),
        ("imperative", "cool", "strict", False),
        ("request", "cool", "strict", False),
        ("rebuke", "cool", "strict", False),
        ("unrelated", "cool", "strict", False),
        ("remark", "none", "strict", False),
        ("imperative", "cool", "weekday", True),
        ("request", "status", "weekday", True),
        ("unrelated", "none", "weekday", False),
        ("imperative", "none", "weekday", False),
        ("remark", "cool", "nonsense-mode", False),
    ],
)
def test_decision_acts_matches_policy_and_intent(klass, intent, mode, expected):
    assert gr.decision_acts(decision(klass, intent), mode) is expected


def test_is_decider_failure_names_every_no_decision_reason():
    assert gr.is_decider_failure(decision(reason="timeout")) is True
    assert gr.is_decider_failure(decision(reason="http_500")) is True
    assert gr.is_decider_failure(decision(reason="not_recorded")) is True
    assert gr.is_decider_failure(decision(reason="ok")) is False
    assert gr.is_decider_failure(decision(reason="recorded")) is False


def test_percentile_interpolates():
    assert gr.percentile([1, 2, 3, 4], 0.5) == pytest.approx(2.5)
    assert gr.percentile([10], 0.95) == pytest.approx(10)
    assert gr.percentile([], 0.5) is None


# --------------------------------------------------------------------------
# scoring
# --------------------------------------------------------------------------


def test_score_records_a_hard_false_positive_when_a_command_acts_in_strict():
    rows = [row("c1", category="command", classes=("imperative",), act_strict=False)]
    results = [result("c1", decisions=(decision("remark", "cool"),))]
    scored = gr.score(rows, results, mode="strict", entrance="text")
    assert scored["hard_false_positives"] == ["c1"]
    assert scored["hard_rows"] == 1


def test_score_has_no_hard_false_positive_when_the_command_is_refused():
    rows = [row("c1", category="command", classes=("imperative",), act_strict=False)]
    results = [result("c1", decisions=(decision("imperative", "cool"),))]
    scored = gr.score(rows, results, mode="strict", entrance="text")
    assert scored["hard_false_positives"] == []


def test_hard_false_positives_are_not_counted_in_weekday_mode():
    rows = [row("c1", category="command", classes=("imperative",), act_strict=False)]
    results = [result("c1", decisions=(decision("imperative", "cool"),))]
    scored = gr.score(rows, results, mode="weekday", entrance="text")
    assert scored["hard_false_positives"] == []
    assert scored["hard_rows"] == 0


def test_hint_recall_requires_acting_with_the_expected_intent():
    rows = [row("h1", intent="cool"), row("h2", intent="warm")]
    results = [
        result("h1", decisions=(decision("remark", "cool"),)),
        result("h2", decisions=(decision("remark", "cool"),)),  # acts, wrong intent
    ]
    scored = gr.score(rows, results, mode="strict", entrance="text")
    assert scored["hint_recall"] == {"expected": 2, "acted_right": 1, "rate": pytest.approx(0.5)}


def test_hint_recall_ignores_rows_whose_intent_is_not_checked():
    rows = [row("h1", intent=None, act_strict=True)]
    results = [result("h1", decisions=(decision("remark", "cool"),))]
    scored = gr.score(rows, results, mode="strict", entrance="text")
    assert scored["hint_recall"]["expected"] == 1
    assert scored["hint_recall"]["acted_right"] == 1


def test_weekday_command_obedience_counts_only_rows_that_should_act():
    rows = [
        row("c1", category="command", classes=("imperative",), act_strict=False, act_weekday=True),
        row("c2", category="command", classes=("imperative",), act_strict=False, act_weekday=None),
        row(
            "n1",
            category="negative",
            classes=("unrelated",),
            intent="none",
            act_strict=False,
            act_weekday=False,
        ),
    ]
    results = [
        result("c1", decisions=(decision("imperative", "cool"),)),
        result("c2", decisions=(decision("imperative", "cool"),)),
        result("n1", decisions=(decision("unrelated", "none"),)),
    ]
    scored = gr.score(rows, results, mode="weekday", entrance="text")
    assert scored["weekday_obedience"] == {
        "expected": 1,
        "obeyed": 1,
        "rate": pytest.approx(1.0),
    }


def test_label_acceptability_uses_the_rows_acceptable_classes():
    rows = [row("h1", classes=("remark", "wish")), row("h2", classes=("remark", "wish"))]
    results = [
        result("h1", decisions=(decision("wish", "cool"),)),
        result("h2", decisions=(decision("imperative", "cool"),)),
    ]
    scored = gr.score(rows, results, mode="strict", entrance="text")
    assert scored["label_acceptability"] == {"checked": 2, "ok": 1, "rate": pytest.approx(0.5)}


def test_a_row_split_into_several_utterances_fails_if_any_of_them_acts():
    rows = [row("c1", category="command", classes=("imperative",), act_strict=False)]
    results = [
        result(
            "c1",
            transcripts=("תדליק", "את המזגן קר פה"),
            decisions=(decision("imperative", "cool"), decision("remark", "cool")),
        )
    ]
    scored = gr.score(rows, results, mode="strict", entrance="audio-realtime")
    assert scored["hard_false_positives"] == ["c1"]


def test_a_row_split_into_several_utterances_passes_when_none_acts():
    rows = [row("c1", category="command", classes=("imperative",), act_strict=False)]
    results = [
        result(
            "c1",
            transcripts=("תדליק", "את המזגן"),
            decisions=(decision("imperative", "cool"), decision("unrelated", "none")),
        )
    ]
    scored = gr.score(rows, results, mode="strict", entrance="audio-realtime")
    assert scored["hard_false_positives"] == []


def test_score_counts_decider_failures_and_latency():
    rows = [row("h1"), row("h2")]
    results = [
        result("h1", decisions=(decision(reason="timeout"),), latency_ms=100.0),
        result("h2", decisions=(decision(),), latency_ms=200.0),
    ]
    scored = gr.score(rows, results, mode="strict", entrance="text")
    assert scored["decider_failures"] == 1
    assert scored["decider_failure_rate"] == pytest.approx(0.5)
    assert scored["latency_ms"]["p50"] == pytest.approx(150.0)
    assert scored["latency_ms"]["p95"] is not None


def test_score_reports_rows_with_no_transcript_at_all():
    rows = [row("h1")]
    results = [gr.RowResult(row_id="h1", transcripts=[], decisions=[], latency_ms=1.0)]
    scored = gr.score(rows, results, mode="strict", entrance="audio-batch")
    assert scored["silent_rows"] == ["h1"]
    assert scored["hard_false_positives"] == []


# --------------------------------------------------------------------------
# thresholds, exit code, report
# --------------------------------------------------------------------------


def test_committed_thresholds_file_has_the_agreed_values():
    thresholds = gr.load_thresholds()
    assert thresholds["hard_false_positives_strict"] == 0
    assert thresholds["hint_recall_min"] == pytest.approx(0.70)
    assert 0 < thresholds["decider_failure_rate_max"] < 0.2


def test_any_hard_false_positive_violates_the_thresholds():
    scored = [
        {
            "entrance": "text",
            "mode": "strict",
            "hard_false_positives": ["c1"],
            "hint_recall": {"expected": 1, "acted_right": 1, "rate": 1.0},
            "decider_failure_rate": 0.0,
        }
    ]
    violations = gr.check_thresholds(scored, gr.load_thresholds())
    assert any("hard" in v for v in violations)
    assert gr.exit_code(violations) == gr.EXIT_THRESHOLD


def test_low_hint_recall_violates_the_thresholds():
    scored = [
        {
            "entrance": "text",
            "mode": "strict",
            "hard_false_positives": [],
            "hint_recall": {"expected": 10, "acted_right": 3, "rate": 0.3},
            "decider_failure_rate": 0.0,
        }
    ]
    violations = gr.check_thresholds(scored, gr.load_thresholds())
    assert any("hint_recall" in v for v in violations)


def test_too_many_decider_failures_violate_the_thresholds():
    scored = [
        {
            "entrance": "text",
            "mode": "strict",
            "hard_false_positives": [],
            "hint_recall": {"expected": 10, "acted_right": 10, "rate": 1.0},
            "decider_failure_rate": 0.9,
        }
    ]
    violations = gr.check_thresholds(scored, gr.load_thresholds())
    assert any("decider_failure_rate" in v for v in violations)


def test_a_clean_run_exits_zero():
    scored = [
        {
            "entrance": "text",
            "mode": "strict",
            "hard_false_positives": [],
            "hint_recall": {"expected": 10, "acted_right": 9, "rate": 0.9},
            "decider_failure_rate": 0.0,
        }
    ]
    assert gr.check_thresholds(scored, gr.load_thresholds()) == []
    assert gr.exit_code([]) == gr.EXIT_OK


def test_weekday_scores_are_not_gated_on_hint_recall():
    scored = [
        {
            "entrance": "text",
            "mode": "weekday",
            "hard_false_positives": [],
            "hint_recall": {"expected": 10, "acted_right": 0, "rate": 0.0},
            "decider_failure_rate": 0.0,
        }
    ]
    assert gr.check_thresholds(scored, gr.load_thresholds()) == []


def test_report_shape_names_the_prompt_the_model_and_lobes():
    report = gr.build_report(
        scores=[{"entrance": "text", "mode": "strict", "hard_false_positives": []}],
        violations=["boom"],
        model="senses",
        lobes_health={"status": "ok", "version": "1.2.3"},
        manifest_rows=275,
        thresholds=gr.load_thresholds(),
    )
    assert report["prompt_version"] == gr.PROMPT_VERSION
    assert report["model"] == "senses"
    assert report["lobes"] == {"status": "ok", "version": "1.2.3"}
    assert report["date"]
    assert report["manifest_rows"] == 275
    assert report["violations"] == ["boom"]
    assert report["entrances"][0]["entrance"] == "text"
    assert json.loads(json.dumps(report))  # a report is plain JSON


def test_render_table_puts_hard_failures_first():
    report = gr.build_report(
        scores=[
            {
                "entrance": "text",
                "mode": "strict",
                "rows": 2,
                "hard_rows": 1,
                "hard_false_positives": ["c1"],
                "hint_recall": {"expected": 1, "acted_right": 1, "rate": 1.0},
                "weekday_obedience": {"expected": 0, "obeyed": 0, "rate": None},
                "label_acceptability": {"checked": 2, "ok": 2, "rate": 1.0},
                "latency_ms": {"p50": 1.0, "p95": 2.0},
                "decider_failures": 0,
                "decider_failure_rate": 0.0,
                "silent_rows": [],
            }
        ],
        violations=["hard_false_positives(text/strict)=1"],
        model="senses",
        lobes_health={},
        manifest_rows=2,
        thresholds=gr.load_thresholds(),
    )
    table = gr.render_table(report)
    assert table.index("HARD") < table.index("hint recall")
    assert "c1" in table


def test_render_table_of_a_clean_report_says_so():
    report = gr.build_report(
        scores=[
            {
                "entrance": "text",
                "mode": "strict",
                "rows": 1,
                "hard_rows": 1,
                "hard_false_positives": [],
                "hint_recall": {"expected": 1, "acted_right": 1, "rate": 1.0},
                "weekday_obedience": {"expected": 0, "obeyed": 0, "rate": None},
                "label_acceptability": {"checked": 1, "ok": 1, "rate": 1.0},
                "latency_ms": {"p50": 1.0, "p95": 1.0},
                "decider_failures": 0,
                "decider_failure_rate": 0.0,
                "silent_rows": [],
            }
        ],
        violations=[],
        model="senses",
        lobes_health={},
        manifest_rows=1,
        thresholds=gr.load_thresholds(),
    )
    assert "no HARD failures" in gr.render_table(report)


# --------------------------------------------------------------------------
# the manifest
# --------------------------------------------------------------------------


def test_load_manifest_reads_the_committed_rows():
    rows = gr.load_manifest()
    assert len(rows) >= 275
    assert gr.validate_manifest(rows) == []
    assert all(isinstance(r, gr.GoldenRow) for r in rows)


def test_validate_manifest_rejects_duplicate_ids():
    rows = [row("dup"), row("dup")]
    assert any("duplicate" in problem for problem in gr.validate_manifest(rows))


def test_validate_manifest_rejects_an_unknown_class_or_intent():
    bad_class = [row("x", classes=("shouting",))]
    bad_intent = [row("y", intent="launch_missiles")]
    assert any("class" in p for p in gr.validate_manifest(bad_class))
    assert any("intent" in p for p in gr.validate_manifest(bad_intent))


def test_validate_manifest_rejects_an_unjustified_must_not_act_row():
    # A hint with an actionable intent that nonetheless must not act has no
    # justification in the categories the manifest defines.
    rows = [row("h9", category="hint", intent="cool", act_strict=False)]
    assert any("act_strict" in p for p in gr.validate_manifest(rows))


# --------------------------------------------------------------------------
# the entrances
# --------------------------------------------------------------------------


def test_run_decider_uses_a_fresh_empty_context_per_row():
    seen = []

    class Spy:
        def decide(self, utterance, context, *, mode, ac_state=None):
            seen.append((utterance, len(context), mode))
            return decision()

    rows = [row("h1", text="חם פה"), row("h2", text="קר פה")]
    results = gr.run_decider(rows, Spy(), mode="strict", transcripts_for=lambda r: [r.text])
    assert [r.row_id for r in results] == ["h1", "h2"]
    assert seen == [("חם פה", 0, "strict"), ("קר פה", 0, "strict")]
    assert all(r.latency_ms >= 0 for r in results)


def test_run_decider_labels_every_utterance_of_a_split_row():
    class Spy:
        def decide(self, utterance, context, *, mode, ac_state=None):
            return decision()

    results = gr.run_decider(
        [row("h1")], Spy(), mode="strict", transcripts_for=lambda r: ["a", "b"]
    )
    assert results[0].transcripts == ["a", "b"]
    assert len(results[0].decisions) == 2


def test_text_entrance_against_the_fake_senses_server():
    from shabbos_goy.decider import GemmaDecider, SensesConfig

    with FakeSensesServer(
        responses=[ScriptedResponse(body=decision_body("wish", "cool", 0.8))]
    ) as server:
        decider = GemmaDecider(SensesConfig(base_url=server.base_url, model="senses"))
        results = gr.run_decider(
            [row("h1")], decider, mode="strict", transcripts_for=lambda r: [r.text]
        )
    assert results[0].decisions[0].klass == "wish"
    assert server.requests[0].json_body["model"] == "senses"


# -- the TTS step ----------------------------------------------------------


def test_speech_body_asks_for_wav():
    body = json.loads(gr.speech_body("שלום").decode("utf-8"))
    assert body["input"] == "שלום"
    assert body["response_format"] == "wav"


def test_encode_multipart_is_parseable_and_carries_the_language_field():
    body, content_type = gr.encode_multipart(
        {"language": "he"}, filename="row.wav", content=b"RIFFdata", content_type="audio/wav"
    )
    fields, filename, file_type, content = parse_multipart(content_type, body)
    assert fields == {"language": "he"}
    assert filename == "row.wav"
    assert file_type == "audio/wav"
    assert content == b"RIFFdata"


def test_batch_client_synthesises_transcribes_and_reads_health():
    with FakeBatchAudioServer(transcripts={"חם פה": "חם פה מאוד"}) as server:
        client = gr.LobesBatchClient(server.base_url, api_key="not-a-real-key")
        audio = client.synthesize("שלום")
        text = client.transcribe(marker_wav("חם פה"), language="he", filename="row.wav")
        health = client.health()
    assert audio.startswith(b"RIFF")
    assert text == "חם פה מאוד"
    assert health["status"] == "ok"
    assert server.speech_calls[0].headers["authorization"] == "Bearer not-a-real-key"
    assert server.transcription_calls[0].fields == {"language": "he"}


def test_batch_client_raises_a_named_error_on_a_bad_status():
    with FakeBatchAudioServer(transcription_status=502) as server:
        client = gr.LobesBatchClient(server.base_url)
        wav_bytes = marker_wav("x")
        with pytest.raises(gr.GoldenError):
            client.transcribe(wav_bytes, language="he")


def test_health_is_never_fatal():
    client = gr.LobesBatchClient("http://127.0.0.1:1")
    assert client.health() == {}


def test_synthesise_all_writes_one_wav_per_row_and_skips_existing(tmp_path):
    rows = [row("h1", text="חם פה"), row("h2", text="קר פה")]
    with FakeBatchAudioServer() as server:
        client = gr.LobesBatchClient(server.base_url)
        written = gr.synthesize_all(rows, client, tmp_path)
        assert written == 2
        again = gr.synthesize_all(rows, client, tmp_path)
    assert again == 0
    assert (tmp_path / "h1.wav").exists()
    assert (tmp_path / "h2.wav").exists()
    assert len(server.speech_calls) == 2


def test_audio_batch_entrance_transcribes_each_wav_then_decides(tmp_path):
    rows = [row("h1", text="חם פה")]
    (tmp_path / "h1.wav").write_bytes(marker_wav("חם פה"))

    class Spy:
        def __init__(self):
            self.seen = []

        def decide(self, utterance, context, *, mode, ac_state=None):
            self.seen.append(utterance)
            return decision()

    spy = Spy()
    with FakeBatchAudioServer(transcripts={"חם פה": "חם פה מאוד"}) as server:
        client = gr.LobesBatchClient(server.base_url)
        results, cache = gr.run_audio_batch(
            rows, spy, mode="strict", client=client, audio_dir=tmp_path
        )
    assert spy.seen == ["חם פה מאוד"]
    assert cache == {"h1": ["חם פה מאוד"]}
    assert results[0].transcripts == ["חם פה מאוד"]
    assert server.transcription_calls[0].fields["language"] == "he"


def test_audio_batch_entrance_reports_a_missing_wav(tmp_path):
    class Spy:
        def decide(self, utterance, context, *, mode, ac_state=None):  # pragma: no cover
            raise AssertionError("must not be asked to decide anything")

    results, cache = gr.run_audio_batch(
        [row("h1")], Spy(), mode="strict", client=None, audio_dir=tmp_path
    )
    assert results[0].transcripts == []
    assert results[0].error
    assert "audio" in results[0].error
    assert cache == {}


def test_audio_batch_entrance_drops_an_empty_transcript(tmp_path):
    (tmp_path / "h1.wav").write_bytes(marker_wav("nothing-scripted"))

    class Spy:
        def decide(self, utterance, context, *, mode, ac_state=None):  # pragma: no cover
            raise AssertionError("an empty transcript is dropped noise")

    with FakeBatchAudioServer() as server:
        client = gr.LobesBatchClient(server.base_url)
        results, cache = gr.run_audio_batch(
            [row("h1")], Spy(), mode="strict", client=client, audio_dir=tmp_path
        )
    assert results[0].transcripts == []
    assert cache == {}


# -- WAV handling ----------------------------------------------------------


def test_read_wav_returns_pcm_and_rate(tmp_path):
    path = tmp_path / "a.wav"
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        handle.writeframes(b"\x01\x02" * 100)
    pcm, rate = gr.read_wav(path)
    assert rate == 16000
    assert pcm == b"\x01\x02" * 100


def test_read_wav_refuses_stereo(tmp_path):
    path = tmp_path / "s.wav"
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(2)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        handle.writeframes(b"\x00\x00\x00\x00" * 10)
    with pytest.raises(gr.GoldenError):
        gr.read_wav(path)


def test_chunk_pcm_splits_on_frame_boundaries():
    chunks = gr.chunk_pcm(b"\x00\x01" * 100, rate=16000, chunk_ms=1)
    assert all(len(c) % 2 == 0 for c in chunks)
    assert b"".join(chunks) == b"\x00\x01" * 100


# -- the realtime entrance -------------------------------------------------


def _transcript_script(pieces):
    """A fake-lobes script that speaks *pieces* as (item_id, text, start, stop)."""

    def script(server, conn):
        conn.send_event({"type": "session.created", "session": {"id": "s1"}})
        for item_id, text, start, stop in pieces:
            conn.send_event(
                {
                    "type": "input_audio_buffer.speech_started",
                    "item_id": item_id,
                    "at_ms": start,
                }
            )
            conn.send_event(
                {
                    "type": "input_audio_buffer.speech_stopped",
                    "item_id": item_id,
                    "at_ms": stop,
                }
            )
            conn.send_event(
                {
                    "type": "conversation.item.input_audio_transcription.completed",
                    "item_id": item_id,
                    "text": text,
                }
            )

    return script


def test_stream_wav_joins_a_pause_split_sentence_and_sends_only_audio():
    pieces = [("i1", "הלוואי ש", 0, 100), ("i2", "היה קר", 200, 300)]
    with FakeLobesServer(script=_transcript_script(pieces)) as server:
        config = LobesConfig(host=server.host, port=server.port, input_sample_rate=16000)
        utterances = gr.stream_wav(
            config,
            pcm=b"\x00\x00" * 160,
            rate=16000,
            gap_threshold_ms=500,
            quiet_after_audio=0.3,
            timeout_seconds=10.0,
        )
        conn = server.wait_for_connection()
        sent = conn.sent_events()
    assert utterances == ["הלוואי ש היה קר"]
    assert sent, "the session must have streamed audio"
    assert {event["type"] for event in sent} == {"input_audio_buffer.append"}


def test_stream_wav_keeps_two_separate_utterances_apart():
    pieces = [("i1", "תדליק", 0, 100), ("i2", "את המזגן", 5000, 5100)]
    with FakeLobesServer(script=_transcript_script(pieces)) as server:
        config = LobesConfig(host=server.host, port=server.port, input_sample_rate=16000)
        utterances = gr.stream_wav(
            config,
            pcm=b"\x00\x00" * 160,
            rate=16000,
            gap_threshold_ms=500,
            quiet_after_audio=0.3,
            timeout_seconds=10.0,
        )
    assert utterances == ["תדליק", "את המזגן"]


def test_stream_wav_raises_when_the_gateway_refuses_the_handshake():
    with FakeLobesServer(handshake_status=401) as server:
        config = LobesConfig(host=server.host, port=server.port)
        with pytest.raises(gr.GoldenError):
            gr.stream_wav(
                config,
                pcm=b"\x00\x00" * 160,
                rate=16000,
                quiet_after_audio=0.1,
                timeout_seconds=5.0,
            )


def test_audio_realtime_entrance_decides_every_utterance(tmp_path):
    path = tmp_path / "c1.wav"
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        handle.writeframes(b"\x00\x00" * 160)

    class Spy:
        def __init__(self):
            self.seen = []

        def decide(self, utterance, context, *, mode, ac_state=None):
            self.seen.append(utterance)
            return decision("imperative", "cool")

    pieces = [("i1", "תדליק", 0, 100), ("i2", "את המזגן", 5000, 5100)]
    spy = Spy()
    with FakeLobesServer(script=_transcript_script(pieces)) as server:
        config = LobesConfig(host=server.host, port=server.port, input_sample_rate=16000)
        rows = [row("c1", category="command", classes=("imperative",), act_strict=False)]
        results, cache = gr.run_audio_realtime(
            rows,
            spy,
            mode="strict",
            config=config,
            audio_dir=tmp_path,
            gap_threshold_ms=500,
            quiet_after_audio=0.3,
            timeout_seconds=10.0,
        )
    assert spy.seen == ["תדליק", "את המזגן"]
    assert cache == {"c1": ["תדליק", "את המזגן"]}
    assert len(results[0].decisions) == 2


def test_audio_realtime_entrance_records_a_connection_failure(tmp_path):
    path = tmp_path / "h1.wav"
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        handle.writeframes(b"\x00\x00" * 160)

    class Spy:
        def decide(self, utterance, context, *, mode, ac_state=None):  # pragma: no cover
            raise AssertionError("nothing to decide")

    with FakeLobesServer(handshake_status=401) as server:
        config = LobesConfig(host=server.host, port=server.port, input_sample_rate=16000)
        results, cache = gr.run_audio_realtime(
            [row("h1")],
            Spy(),
            mode="strict",
            config=config,
            audio_dir=tmp_path,
            quiet_after_audio=0.1,
            timeout_seconds=5.0,
        )
    assert results[0].transcripts == []
    assert results[0].error
    assert cache == {}


# --------------------------------------------------------------------------
# recording + the ASR cache
# --------------------------------------------------------------------------


def test_record_writes_a_replay_file_the_replay_decider_can_read(tmp_path):
    results = [
        result("h1", transcripts=("חם פה",), decisions=(decision("wish", "cool"),)),
        result("h2", transcripts=("קר פה",), decisions=(decision(reason="timeout"),)),
    ]
    path = tmp_path / "golden_replay.json"
    written = gr.record_decisions({"text": results}, path)
    assert written == 1  # the failed decision is not recorded as an answer
    replay = ReplayDecider.from_file(path, entrance="text")
    from shabbos_goy.decider import ContextWindow

    got = replay.decide("חם פה", ContextWindow(), mode="strict")
    assert (got.klass, got.intent) == ("wish", "cool")


def test_record_merges_into_an_existing_replay_file(tmp_path):
    path = tmp_path / "golden_replay.json"
    path.write_text(
        json.dumps(
            {
                "format": REPLAY_FORMAT,
                "entrances": {"text": {"ישן": {"class": "unrelated", "intent": "none"}}},
            }
        ),
        "utf-8",
    )
    gr.record_decisions(
        {"text": [result("h1", transcripts=("חדש",), decisions=(decision("wish", "cool"),))]}, path
    )
    data = entrance_buckets(json.loads(path.read_text("utf-8")))
    assert set(data["text"]) == {"ישן", "חדש"}


def test_one_entrance_never_overwrites_another_for_the_same_transcript(tmp_path):
    """Risk r13: a flat text key let audio-realtime's answer overwrite the
    text entrance's for the same string, so a recorded run that FAILED the
    text entrance produced a fixture that PASSED. Keyed by entrance, both
    answers survive and the failing one stays visible."""
    same = "היה קר לי"
    path = tmp_path / "golden_replay.json"
    gr.record_decisions(
        {
            "text": [
                result("n1", transcripts=(same,), decisions=(decision("discomfort", "warm"),))
            ],
            "audio-realtime": [
                result("n1", transcripts=(same,), decisions=(decision("unrelated", "none"),))
            ],
        },
        path,
    )
    data = entrance_buckets(json.loads(path.read_text("utf-8")))
    assert data["text"][same]["class"] == "discomfort"
    assert data["audio-realtime"][same]["class"] == "unrelated"

    from shabbos_goy.decider import ContextWindow

    assert (
        ReplayDecider.from_file(path, entrance="text")
        .decide(same, ContextWindow(), mode="strict")
        .klass
        == "discomfort"
    )
    # Merging the entrances back together is what hid the failure, so it is
    # refused rather than silently guessed at.
    with pytest.raises(ValueError, match="keyed by entrance"):
        ReplayDecider.from_file(path)


def test_a_pre_r13_flat_replay_file_is_read_as_the_text_entrance(tmp_path):
    path = tmp_path / "golden_replay.json"
    path.write_text(json.dumps({"חם פה": {"class": "wish", "intent": "cool"}}), "utf-8")
    gr.record_decisions(
        {
            "audio-batch": [
                result("h1", transcripts=("קר פה",), decisions=(decision("remark", "warm"),))
            ]
        },
        path,
    )
    data = entrance_buckets(json.loads(path.read_text("utf-8")))
    assert data["text"]["חם פה"]["class"] == "wish"
    assert data["audio-batch"]["קר פה"]["class"] == "remark"


def test_record_decisions_writes_a_marked_envelope(tmp_path):
    """The recorder is the only producer of the nested shape, so it marks it.

    Finding 5: the reader must not have to infer the shape from the absence
    of a key -- a flat file with one malformed record satisfies that guess.
    """
    path = tmp_path / "golden_replay.json"
    gr.record_decisions(
        {"text": [result("h1", transcripts=("חם פה",), decisions=(decision("wish", "cool"),))]},
        path,
    )
    data = json.loads(path.read_text("utf-8"))
    assert data["format"] == REPLAY_FORMAT
    assert set(data["entrances"]) == {"text"}
    assert (
        ReplayDecider.from_file(path, entrance="text")
        .decide("חם פה", ContextWindow(), mode="strict")
        .intent
        == "cool"
    )


def test_a_recorded_envelope_replays_per_entrance_through_the_runner_cli(tmp_path, capsys):
    """Finding 3: ``--decider replay`` must read what ``--record`` wrote.

    And it must read the bucket for the entrance it is running: the text
    bucket here holds the failure, the audio-batch bucket does not.
    """
    rows = gr.load_manifest()
    command = next(r for r in rows if r.category == "command")
    replay = tmp_path / "golden_replay.json"
    replay.write_text(
        json.dumps(
            {
                "format": REPLAY_FORMAT,
                "entrances": {
                    "text": {
                        command.text: {"class": "remark", "intent": "cool", "confidence": 1.0}
                    },
                    "audio-batch": {
                        command.text: {"class": "imperative", "intent": "cool", "confidence": 1.0}
                    },
                },
            }
        ),
        "utf-8",
    )
    code = gr.main(
        [
            "--entrance",
            "text",
            "--mode",
            "strict",
            "--decider",
            "replay",
            "--replay",
            str(replay),
            "--only",
            command.id,
        ]
    )
    printed = capsys.readouterr()
    assert code == gr.EXIT_THRESHOLD, printed.err
    assert command.id in printed.out


def test_the_replay_decider_is_bound_to_the_entrance_being_run(tmp_path):
    replay = tmp_path / "golden_replay.json"
    replay.write_text(
        json.dumps(
            {
                "format": REPLAY_FORMAT,
                "entrances": {
                    "text": {"חם פה": {"class": "wish", "intent": "cool", "confidence": 0.9}},
                    "audio-batch": {"חם פה": {"class": "unrelated", "intent": "none"}},
                },
            }
        ),
        "utf-8",
    )
    args = gr._parser().parse_args(["--decider", "replay", "--replay", str(replay)])
    decider_for, model = gr._build_decider(args, {})
    assert model == "replay"
    window = ContextWindow()
    assert decider_for("text").decide("חם פה", window, mode="strict").klass == "wish"
    assert decider_for("audio-batch").decide("חם פה", window, mode="strict").klass == "unrelated"


def test_a_flat_replay_file_still_serves_every_entrance(tmp_path):
    replay = tmp_path / "replay.json"
    replay.write_text(
        json.dumps({"חם פה": {"class": "wish", "intent": "cool", "confidence": 0.9}}), "utf-8"
    )
    args = gr._parser().parse_args(["--decider", "replay", "--replay", str(replay)])
    decider_for, _ = gr._build_decider(args, {})
    for entrance in ("text", "audio-batch", "audio-realtime"):
        got = decider_for(entrance).decide("חם פה", ContextWindow(), mode="strict")
        assert got.intent == "cool"


def test_a_missing_entrance_in_the_replay_file_is_an_environment_error(tmp_path, capsys):
    replay = tmp_path / "golden_replay.json"
    replay.write_text(
        json.dumps(
            {
                "format": REPLAY_FORMAT,
                "entrances": {
                    "audio-batch": {"חם פה": {"class": "wish", "intent": "cool"}},
                    "audio-realtime": {"חם פה": {"class": "wish", "intent": "cool"}},
                },
            }
        ),
        "utf-8",
    )
    code = gr.main(
        ["--entrance", "text", "--decider", "replay", "--replay", str(replay), "--limit", "2"]
    )
    printed = capsys.readouterr()
    assert code == gr.EXIT_ENVIRONMENT
    assert "text" in printed.err


def test_asr_cache_round_trips(tmp_path):
    path = tmp_path / "asr_cache.json"
    gr.save_asr_cache(path, {"audio-batch": {"h1": ["חם פה"]}})
    gr.save_asr_cache(path, {"audio-realtime": {"h2": ["קר פה"]}})
    loaded = gr.load_asr_cache(path)
    assert loaded == {"audio-batch": {"h1": ["חם פה"]}, "audio-realtime": {"h2": ["קר פה"]}}


def test_load_asr_cache_of_a_missing_file_is_empty(tmp_path):
    assert gr.load_asr_cache(tmp_path / "nope.json") == {}


def test_the_committed_asr_cache_is_well_formed():
    cache = gr.load_asr_cache(gr.ASR_CACHE_PATH)
    assert isinstance(cache, dict)
    ids = {r.id for r in gr.load_manifest()}
    for entrance, rows in cache.items():
        assert entrance in gr.AUDIO_ENTRANCES
        for row_id, texts in rows.items():
            assert row_id in ids, f"{row_id} is not a manifest id"
            assert all(isinstance(t, str) for t in texts)


# --------------------------------------------------------------------------
# the CLI
# --------------------------------------------------------------------------


def test_main_runs_the_text_entrance_offline_with_the_rule_oracle(tmp_path, capsys):
    out = tmp_path / "report.json"
    code = gr.main(
        ["--entrance", "text", "--decider", "oracle", "--limit", "20", "--out", str(out)]
    )
    printed = capsys.readouterr()
    report = json.loads(out.read_text("utf-8"))
    assert report["entrances"]
    assert report["prompt_version"] == gr.PROMPT_VERSION
    assert "HARD" in printed.out
    assert code in (gr.EXIT_OK, gr.EXIT_THRESHOLD)


def test_main_json_prints_the_report_to_stdout(tmp_path, capsys):
    gr.main(["--entrance", "text", "--decider", "oracle", "--limit", "5", "--json"])
    printed = capsys.readouterr()
    assert json.loads(printed.out)["entrances"]


def test_main_exits_non_zero_on_a_hard_false_positive(tmp_path, capsys):
    rows = gr.load_manifest()
    command = next(r for r in rows if r.category == "command")
    replay = tmp_path / "replay.json"
    replay.write_text(
        json.dumps({command.text: {"class": "remark", "intent": "cool", "confidence": 1.0}}),
        "utf-8",
    )
    code = gr.main(
        [
            "--entrance",
            "text",
            "--mode",
            "strict",
            "--decider",
            "replay",
            "--replay",
            str(replay),
            "--only",
            command.id,
        ]
    )
    printed = capsys.readouterr()
    assert code == gr.EXIT_THRESHOLD
    assert command.id in printed.out


def test_main_refuses_a_live_entrance_without_an_environment(monkeypatch, capsys):
    for name in ("SHABBOS_GOY_LOBES_URL", "SHABBOS_GOY_SENSES_URL"):
        monkeypatch.delenv(name, raising=False)
    code = gr.main(["--entrance", "audio-batch"])
    printed = capsys.readouterr()
    assert code == gr.EXIT_ENVIRONMENT
    assert "SHABBOS_GOY_LOBES_URL" in printed.err


def test_main_rejects_an_unknown_entrance(capsys):
    with pytest.raises(SystemExit):
        gr.main(["--entrance", "telepathy"])


def test_script_entrypoint_runs_the_same_runner():
    proc = subprocess.run(  # nosec B603 - fixed argv, this repo's own script
        [sys.executable, str(REPO_ROOT / "scripts" / "golden-set.py"), "--help"],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(REPO_ROOT),
    )
    assert proc.returncode == 0
    assert "--entrance" in proc.stdout


def test_module_entrypoint_runs_the_same_runner():
    proc = subprocess.run(  # nosec B603 - fixed argv, this repo's own module
        [sys.executable, "-m", "tests.golden.runner", "--help"],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(REPO_ROOT),
    )
    assert proc.returncode == 0
    assert "--entrance" in proc.stdout


# --------------------------------------------------------------------------
# pytest wiring
# --------------------------------------------------------------------------


def test_the_golden_marker_is_registered_and_excluded_by_default():
    config = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text("utf-8"))
    options = config["tool"]["pytest"]["ini_options"]
    assert any(marker.startswith("golden:") for marker in options["markers"])
    assert "not golden" in options["addopts"]


def test_the_golden_readme_exists_and_says_a_hard_failure_blocks_a_release():
    readme = (REPO_ROOT / "tests" / "golden" / "README.md").read_text("utf-8")
    assert "HARD" in readme
    assert "release" in readme.lower()


# -- acting in the WRONG DIRECTION is a false action, not a missed hint ------


def test_a_hint_that_acts_with_the_wrong_intent_is_a_hard_wrong_action():
    """Found live: a cold complaint labelled 'cool' would switch the AC ON.

    Counting that only against hint recall would let a model that inverts every
    cold remark pass a 70 % recall threshold.
    """
    rows = [row(row_id="cold1", text="קר פה", intent="warm")]
    results = [result(row_id="cold1", decisions=(decision(klass="discomfort", intent="cool"),))]
    scored = gr.score(rows, results, mode="strict", entrance="text")
    assert scored["wrong_actions"] == ["cold1"]
    assert scored["hint_recall"]["acted_right"] == 0


def test_the_right_intent_is_not_a_wrong_action():
    rows = [row(row_id="cold1", text="קר פה", intent="warm")]
    results = [result(row_id="cold1", decisions=(decision(klass="discomfort", intent="warm"),))]
    assert gr.score(rows, results, mode="strict", entrance="text")["wrong_actions"] == []


def test_a_row_with_no_checked_intent_cannot_be_a_wrong_action():
    rows = [row(row_id="x", intent=None)]
    results = [result(row_id="x", decisions=(decision(intent="warm"),))]
    assert gr.score(rows, results, mode="strict", entrance="text")["wrong_actions"] == []


def test_any_wrong_action_violates_the_thresholds():
    scored = [
        {
            "entrance": "text",
            "mode": "strict",
            "hard_false_positives": [],
            "wrong_actions": ["cold1"],
            "hint_recall": {"expected": 1, "acted_right": 0, "rate": 1.0},
            "decider_failure_rate": 0.0,
        }
    ]
    violations = gr.check_thresholds(scored, gr.load_thresholds())
    assert any("wrong_actions" in v for v in violations)
    assert gr.exit_code(violations) == gr.EXIT_THRESHOLD


def test_the_committed_thresholds_allow_no_wrong_action():
    assert gr.load_thresholds()["wrong_actions_max"] == 0

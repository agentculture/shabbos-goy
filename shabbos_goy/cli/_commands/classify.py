"""``shabbos-goy classify "<text>"`` -- decide, but never act.

Deviations d1/d2 moved the decision to a model: this verb calls a
:class:`~shabbos_goy.decider.Decider` (the Gemma-backed lobes ``senses`` role
by default, or a recorded :class:`~shabbos_goy.decider.ReplayDecider` for
offline use) and prints its verdict plus the gate's answer -- it never calls
``shabbos_goy.classifier`` (the retired rule cascade survives only as a test
oracle, per deviation d2) and it never actuates: no adapter, no whitelist
check, no rate limiter, nothing but a read of ``policy.may_act``.
"""

from __future__ import annotations

import argparse
import os
from datetime import datetime, timezone

from shabbos_goy import grant_inject
from shabbos_goy.cli._errors import EXIT_ENV_ERROR, EXIT_USER_ERROR, CliError
from shabbos_goy.cli._output import emit_result
from shabbos_goy.config import load_config
from shabbos_goy.decider.context import ContextWindow
from shabbos_goy.decider.gemma import GemmaDecider, senses_config_from_env
from shabbos_goy.decider.replay import ReplayDecider
from shabbos_goy.lobes.config import ENV_API_KEY, ENV_API_KEY_FALLBACK, ENV_URL, LobesConfigError
from shabbos_goy.mode import resolve_mode
from shabbos_goy.policy import MODES, may_act

DECIDER_GEMMA = "gemma"
DECIDER_REPLAY = "replay"
DECIDER_CHOICES = (DECIDER_GEMMA, DECIDER_REPLAY)

MODE_CHOICES = (*MODES, "auto")


def _resolve_mode(args: argparse.Namespace, config) -> str:
    if args.mode != "auto":
        return args.mode
    now = datetime.now(timezone.utc)
    return resolve_mode(now, config).mode


def _build_decider(args: argparse.Namespace):
    if args.decider == DECIDER_REPLAY:
        if not args.replay_file:
            raise CliError(
                code=EXIT_USER_ERROR,
                message="--decider replay requires --replay-file",
                remediation="pass --replay-file PATH pointing at a JSON replay file "
                "(utterance -> {class, intent, confidence})",
            )
        try:
            return ReplayDecider.from_file(args.replay_file)
        except (OSError, ValueError) as exc:
            raise CliError(
                code=EXIT_ENV_ERROR,
                message=f"could not load replay file {args.replay_file!r}: {exc}",
                remediation="check the path and that it is a JSON object",
            ) from exc

    try:
        senses_config = senses_config_from_env(os.environ)
    except LobesConfigError as exc:
        raise CliError(
            code=EXIT_ENV_ERROR,
            message=f"classify --decider gemma needs the lobes environment: {exc}",
            remediation=f"set {ENV_URL} (and optionally {ENV_API_KEY} or {ENV_API_KEY_FALLBACK})",
        ) from exc
    return GemmaDecider(senses_config)


def cmd_classify(args: argparse.Namespace) -> int:
    json_mode = bool(getattr(args, "json", False))
    config = load_config(path=getattr(args, "config", None))
    # The lobes key may live in the operator's `grant` store: re-exec under it if so.
    grant_inject.ensure_lobes_key(config)
    mode = _resolve_mode(args, config)
    decider = _build_decider(args)

    context = ContextWindow()
    decision = decider.decide(args.text, context, mode=mode, ac_state=None)
    would_act = may_act(mode, decision.klass)

    payload = {
        "class": decision.klass,
        "intent": decision.intent,
        "confidence": decision.confidence,
        "source": decision.source,
        "reason": decision.reason,
        "mode": mode,
        "would_act": would_act,
    }
    if json_mode:
        emit_result(payload, json_mode=True)
    else:
        emit_result(
            "\n".join(
                [
                    f"class: {decision.klass}",
                    f"intent: {decision.intent}",
                    f"confidence: {decision.confidence:.2f}",
                    f"decider source: {decision.source}",
                    f"mode evaluated under: {mode}",
                    f"would act: {would_act}",
                    f"reason: {decision.reason}",
                ]
            ),
            json_mode=False,
        )
    return 0


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "classify",
        help="Decide (never act) on one utterance: class, intent, confidence, gate verdict.",
    )
    p.add_argument("text", help="The utterance text to classify.")
    p.add_argument(
        "--decider",
        choices=list(DECIDER_CHOICES),
        default=DECIDER_GEMMA,
        help="Which decider to ask (default: gemma).",
    )
    p.add_argument(
        "--replay-file",
        help="A JSON replay file (utterance -> decision); required with --decider replay.",
    )
    p.add_argument(
        "--mode",
        choices=list(MODE_CHOICES),
        default="auto",
        help="The mode to evaluate the gate under (default: auto, from zmanim).",
    )
    p.add_argument("--config", help="Path to an explicit config file.")
    p.add_argument("--json", action="store_true", help="Emit structured JSON.")
    p.set_defaults(func=cmd_classify)

"""``shabbos-goy listen`` -- the ambient loop, and the container's healthcheck.

This is what the Docker service runs. It wires the ears
(:mod:`shabbos_goy.lobes`) to the decision pipeline
(:mod:`shabbos_goy.pipeline`) to the whitelisted adapters
(:mod:`shabbos_goy.actuators.sensibo`, :mod:`shabbos_goy.audio.pipewire`),
starts the loopback control endpoint the other CLI verbs talk to, and
optionally the tailnet dashboard. The threads, the queue between them and the
shutdown all live in :mod:`shabbos_goy.runtime`; this module only assembles
the parts and reports what happened.

Three things about it are load-bearing:

* **Dry-run unless ``--apply``.** The flag is passed explicitly into the
  pipeline and into every control call, never inferred, so a bug here cannot
  silently default to acting.
* **``--script`` is the no-hardware path.** A JSONL events file replays the
  same loop with no microphone, no server and no socket; a WAV streams
  through the real client path instead. This is what the fixtures-only
  end-to-end test drives.
* **Nothing it prints carries transcript text.** The summary is counts,
  verdicts and action names; the running diagnostics are
  :class:`~shabbos_goy.runtime.RuntimeNote` lines.

``--healthcheck`` does not start anything: it reads the heartbeat file and
exits 0 (healthy) or 1 (not), which is exactly what a container
``HEALTHCHECK`` needs.
"""

from __future__ import annotations

import argparse
import functools
import os
from pathlib import Path
from typing import Any, Optional

from shabbos_goy import grant_inject
from shabbos_goy.actuators import sensibo
from shabbos_goy.audio import pipewire
from shabbos_goy.cli._errors import EXIT_SUCCESS, EXIT_USER_ERROR, CliError
from shabbos_goy.cli._output import emit_result
from shabbos_goy.config import Config, load_config
from shabbos_goy.decider import ReplayDecider, no_decision
from shabbos_goy.decider.gemma import GemmaDecider, senses_config_from_env
from shabbos_goy.lobes.config import LobesConfigError
from shabbos_goy.pipeline import TOOL_SENSIBO, pipewire_volume_stepper
from shabbos_goy.runtime import (
    Listener,
    ListenerOptions,
    control_address_for,
    events_file_source,
    healthcheck,
    heartbeat_path,
    lobes_source,
    pipewire_audio_source,
    wav_audio_source,
)

DECIDERS = ("gemma", "replay")
EVENT_SUFFIXES = (".jsonl", ".json", ".ndjson")
WAV_SUFFIXES = (".wav",)


class LazySensesDecider:
    """A :class:`GemmaDecider` that resolves its environment per call.

    Built so a listener started before its secrets are mounted keeps
    listening: an unusable environment is
    :data:`~shabbos_goy.decider.NO_DECISION` (which no mode acts on) rather
    than a crash, and the next utterance tries again. The reason code never
    names a value, only a variable class.
    """

    source = "gemma"

    def __init__(self, env: Optional[dict] = None) -> None:
        self._env = env
        self._decider: Any = None

    def _resolve(self) -> Any:
        if self._decider is None:
            config = senses_config_from_env(os.environ if self._env is None else self._env)
            self._decider = GemmaDecider(config)
        return self._decider

    def decide(self, utterance, context, *, mode, ac_state=None):
        try:
            decider = self._resolve()
        except LobesConfigError:
            return no_decision("senses_env_missing", source=self.source)
        return decider.decide(utterance, context, mode=mode, ac_state=ac_state)


def _build_decider(args: argparse.Namespace) -> Any:
    if args.decider == "replay":
        if not args.replay_file:
            raise CliError(
                code=EXIT_USER_ERROR,
                message="--decider replay needs --replay-file PATH",
                remediation="pass a recorded decisions file, e.g. --replay-file replay.json",
            )
        try:
            return ReplayDecider.from_file(args.replay_file)
        except (OSError, ValueError) as exc:
            raise CliError(
                code=EXIT_USER_ERROR,
                message=f"could not read the replay file: {type(exc).__name__}",
                remediation="check the path and that it holds a JSON object of decisions",
            ) from exc
    return LazySensesDecider()


def _first_whitelisted_pod(config: Config) -> str:
    entry = config.whitelist.get(TOOL_SENSIBO)
    pods = entry.get("pods") if isinstance(entry, dict) else None
    if isinstance(pods, list):
        for pod in pods:
            if isinstance(pod, str) and pod:
                return pod
    return ""


def _volume_adapters(config: Config):
    """The ``wpctl``-backed stepper and reader, when a node is configured."""
    node = config.volume_node
    if node is None:
        return None, None
    volume = config.volume if isinstance(config.volume, dict) else {}
    bounds = pipewire.VolumeBounds(
        min_volume=float(volume.get("min", pipewire.DEFAULT_MIN_VOLUME)),
        max_volume=float(volume.get("max", pipewire.DEFAULT_MAX_VOLUME)),
        step=float(volume.get("step", pipewire.DEFAULT_VOLUME_STEP)),
    )
    return pipewire_volume_stepper(node, bounds), (lambda: pipewire.get_volume(node))


def _build_source(args: argparse.Namespace, config: Config):
    script = args.script
    if not script:
        node = config.mic_node
        factory = None
        if node is not None:
            factory = lambda: pipewire_audio_source(node)  # noqa: E731 - a one-liner seam
        return lobes_source(audio_source_factory=factory)

    path = Path(script)
    if not path.is_file():
        raise CliError(
            code=EXIT_USER_ERROR,
            message=f"script file not found: {path}",
            remediation="pass a JSONL file of lobes events, or a WAV file",
        )
    if path.suffix.lower() in WAV_SUFFIXES:
        return lobes_source(audio_source_factory=lambda: wav_audio_source(path))
    if path.suffix.lower() not in EVENT_SUFFIXES:
        raise CliError(
            code=EXIT_USER_ERROR,
            message=f"unsupported --script file type: {path.suffix or '(none)'}",
            remediation=f"use one of {', '.join(EVENT_SUFFIXES + WAV_SUFFIXES)}",
        )
    try:
        return events_file_source(path)
    except (OSError, ValueError) as exc:
        raise CliError(
            code=EXIT_USER_ERROR,
            message=f"could not read the events file: {type(exc).__name__}",
            remediation="each line must be one JSON object (a lobes wire event)",
        ) from exc


def _summary(listener: Listener) -> dict:
    """What the run did. Counts, verdicts and action names -- never text."""
    acted = [
        {"verdict": record.verdict, "action": record.action, "target": record.target}
        for record in listener.pipeline.log_records
        if record.action not in ("none", "")
    ]
    resolved = listener.resolved_mode()
    return {
        "apply": listener.options.apply,
        "mode": getattr(resolved, "mode", "strict"),
        "utterances": len(listener.pipeline.recent()),
        "decisions": len(listener.pipeline.log_records),
        "actions": acted,
        "dropped_events": listener.queue_dropped,
        "control": listener.control_url,
        "dashboard": listener.dashboard_url,
    }


def cmd_listen(args: argparse.Namespace) -> int:
    json_mode = bool(getattr(args, "json", False))

    if getattr(args, "healthcheck", False):
        path = heartbeat_path(override=getattr(args, "heartbeat", None))
        ok, reason = healthcheck(path)
        emit_result({"ok": ok, "reason": reason}, json_mode=json_mode)
        return EXIT_SUCCESS if ok else EXIT_USER_ERROR

    config = load_config(path=getattr(args, "config", None))
    # The lobes key may live in the operator's `grant` store: re-exec under it if so.
    grant_inject.ensure_lobes_key(config)
    decider = _build_decider(args)
    source = _build_source(args, config)
    volume_step, volume_get = _volume_adapters(config)

    options = ListenerOptions(
        apply=bool(args.apply),
        dashboard=bool(args.dashboard),
        control=True,
        control_address=control_address_for(config, getattr(args, "control_address", None)),
    )
    listener = Listener(
        config=config,
        decider=decider,
        source=source,
        options=options,
        pod_id=_first_whitelisted_pod(config),
        # The key is injected per call by `grant run --inject` when config names a
        # secret; it never enters this process (see actuators/sensibo.py).
        ac_power=functools.partial(sensibo.power, grant_secret=config.grant_sensibo_secret),
        ac_status=functools.partial(sensibo.status, grant_secret=config.grant_sensibo_secret),
        volume_step=volume_step,
        volume_get=volume_get,
        heartbeat_path=getattr(args, "heartbeat", None),
    )
    listener.install_signal_handlers()
    code = listener.run()
    summary = _summary(listener)
    if json_mode:
        emit_result(summary, json_mode=True)
    else:
        lines = [
            f"apply: {summary['apply']}",
            f"mode: {summary['mode']}",
            f"utterances: {summary['utterances']}",
            f"decisions: {summary['decisions']}",
            f"actions: {len(summary['actions'])}",
        ]
        for action in summary["actions"]:
            lines.append(f"  [{action['verdict']}] {action['action']} -> {action['target']}")
        emit_result("\n".join(lines), json_mode=False)
    return code


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "listen",
        help="Run the ambient loop (ASR -> decide -> gate -> act). Dry-run unless --apply.",
    )
    p.add_argument("--apply", action="store_true", help="Actually actuate (default: dry-run).")
    p.add_argument("--config", help="Path to an explicit config file.")
    p.add_argument(
        "--script",
        help="Run the same loop from a JSONL events file or a WAV, with no microphone.",
    )
    p.add_argument(
        "--decider",
        choices=list(DECIDERS),
        default="gemma",
        help="Which decider to ask (default: gemma, the lobes senses role).",
    )
    p.add_argument("--replay-file", help="A JSON replay file; required with --decider replay.")
    p.add_argument(
        "--no-dashboard",
        dest="dashboard",
        action="store_false",
        help="Do not start the tailnet dashboard (the control endpoint always runs).",
    )
    p.add_argument(
        "--control-address",
        help=(
            "Where the loopback control endpoint binds "
            "(default: the port from config, else 127.0.0.1:8787)."
        ),
    )
    p.add_argument("--heartbeat", help="Heartbeat file path (default: $SHABBOS_GOY_HEARTBEAT).")
    p.add_argument(
        "--healthcheck",
        action="store_true",
        help="Read the heartbeat and exit 0 (healthy) or 1. Starts nothing.",
    )
    p.add_argument("--json", action="store_true", help="Emit structured JSON.")
    p.set_defaults(func=cmd_listen, dashboard=True)

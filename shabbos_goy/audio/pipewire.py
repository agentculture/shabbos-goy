"""PipeWire audio adapter: capture, playback, own volume.

shabbos-goy is PipeWire-only (no ALSA/``--backend`` switch — the reSpeaker
runs behind PipeWire on the target hosts), so per plan decision c42 every
``amixer``/``aplay`` mention in older honesty conditions is read as its
PipeWire equivalent here: ``pw-record``/``pw-play`` for capture/playback and
``wpctl`` (WirePlumber's CLI) for this agent's own volume.

``normalize_pipewire_device_name``, ``device_identity``,
``validate_device_pair``, ``build_capture_argv`` and ``build_playback_argv``
are CITED (not imported — this package stays dependency-free and does not
import lobes-cli) from ``agentculture/lobes-cli``'s
``scripts/realtime-he-accept.py`` (functions of the same name), which already
solved "refuse a mic/speaker pair that isn't the same physical device" for
the same reSpeaker XVF3800 hardware. See that file's own docstrings for why
the heuristic is conservative (unknown prefixes/suffixes are left alone).

This module never runs a real ``pw-record``/``pw-play``/``wpctl`` as part of
its own tests — every test in ``tests/test_audio_pipewire.py`` points
``PATH`` at fake executables under ``tests/fixtures/fake_pipewire/`` first,
and no test ever passes ``--apply``-equivalent real hardware or changes the
host's real volume.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Capture/playback argv + device-pair validation.
# Cited from lobes-cli/scripts/realtime-he-accept.py (pipewire branch only —
# this repo has no ALSA backend).
# ---------------------------------------------------------------------------

#: Rate shabbos-goy's mic capture requests (matches the lobes ears-only
#: contract's 16 kHz input, see CLAUDE.md's speech-stack section).
CAPTURE_SAMPLE_RATE_DEFAULT = 16000

#: Rate a batch-TTS neutral remark is played back at (Chatterbox's native
#: output rate, per lobes-cli).
PLAYBACK_SAMPLE_RATE_DEFAULT = 24000

_PW_PREFIX_RE = re.compile(r"^(alsa_input\.|alsa_output\.|bluez_input\.|bluez_output\.)")
_PW_SUFFIX_RE = re.compile(
    r"\.(multichannel-input|multichannel-output|analog-stereo|analog-mono"
    r"|iec958-stereo|pro-input-0|pro-output-0)(\.\d+)?$"
)


class DeviceMismatchError(ValueError):
    """The chosen mic and speaker nodes are not the same physical device.

    Refused by default because the reSpeaker XVF3800's hardware AEC needs the
    playback reference on its OWN output to be useful. Override with
    ``allow_split_devices=True`` when that tradeoff is deliberate.
    """

    def __init__(self, mic_identity: str, speaker_identity: str) -> None:
        super().__init__(
            f"mic node {mic_identity!r} and speaker node {speaker_identity!r} "
            "are not the same device — pass allow_split_devices=True to override "
            "(the reSpeaker's hardware AEC only sees the playback reference "
            "when mic and speaker are the SAME USB device)"
        )
        self.mic_identity = mic_identity
        self.speaker_identity = speaker_identity


class AudioBackendError(RuntimeError):
    """A ``pw-record``/``pw-play`` subprocess could not even be started."""


class VolumeCommandError(RuntimeError):
    """A ``wpctl`` invocation failed or its output could not be parsed."""


def normalize_pipewire_device_name(name: str) -> str:
    """Strip a pipewire node name down to its underlying-device identity.

    A HEURISTIC, not a registry lookup: pipewire names a device's capture and
    playback nodes differently (``alsa_input.usb-Seeed-...multichannel-input``
    vs. ``alsa_output.usb-Seeed-...analog-stereo``), so a literal string
    comparison would always call them a mismatch even when they are the same
    USB device. This strips the well-known direction prefix and profile
    suffix pipewire itself generates, leaving the shared device stem. It is
    deliberately conservative (unknown prefixes/suffixes are left alone) —
    ``allow_split_devices=True`` is always the honest escape hatch when this
    heuristic gets it wrong.

    Cited verbatim from lobes-cli's ``scripts/realtime-he-accept.py``.
    """
    if not name:
        return ""
    stripped = _PW_PREFIX_RE.sub("", name)
    stripped = _PW_SUFFIX_RE.sub("", stripped)
    return stripped.strip().lower()


def device_identity(value: str) -> str:
    """The comparable identity of a pipewire node name."""
    return normalize_pipewire_device_name(str(value))


def validate_device_pair(
    mic_device: str, speaker_device: str, allow_split_devices: bool = False
) -> None:
    """Refuse a mic/speaker pair that is not the same physical device.

    Raises :class:`DeviceMismatchError` unless *allow_split_devices* is set.
    NEVER raises when the pair matches, regardless of the flag.
    """
    mic_id = device_identity(mic_device)
    speaker_id = device_identity(speaker_device)
    if mic_id != speaker_id and not allow_split_devices:
        raise DeviceMismatchError(mic_id, speaker_id)


def _safe_target(device: object) -> str:
    """A node name, id or ``@ALIAS@`` that can never be read as an option.

    Node names come from the operator's private config, never from speech, but
    the argv builders are the one choke point, so refuse option-shaped values
    here: empty, leading ``-``, or containing whitespace / control characters.
    """
    target = str(device)
    if not target or target.startswith("-") or any(ch.isspace() for ch in target):
        raise ValueError(f"unsafe PipeWire target: {target!r}")
    return target


def build_capture_argv(
    device: str, rate: int = CAPTURE_SAMPLE_RATE_DEFAULT, channels: int = 1
) -> list[str]:
    """The argv for the capture subprocess — never executed here, just built.

    Targets *device* (a pipewire node name or id) with ``--target``, matching
    ``pw-record``'s own resolution: a name, an id, or a well-known alias
    (``@DEFAULT_SOURCE@``) are all accepted by pipewire itself.
    """
    return [
        "pw-record",
        "--target",
        _safe_target(device),
        "--rate",
        str(rate),
        "--channels",
        str(channels),
        "--format",
        "s16",
        "-",
    ]


def build_playback_argv(
    device: str, rate: int = PLAYBACK_SAMPLE_RATE_DEFAULT, channels: int = 1
) -> list[str]:
    """The argv for the playback subprocess — never executed here, just built."""
    return [
        "pw-play",
        "--target",
        _safe_target(device),
        "--rate",
        str(rate),
        "--channels",
        str(channels),
        "--format",
        "s16",
        "-",
    ]


# ---------------------------------------------------------------------------
# Volume: bounds, config, clamping.
# ---------------------------------------------------------------------------

DEFAULT_MIN_VOLUME = 0.0
DEFAULT_MAX_VOLUME = 1.0
DEFAULT_VOLUME_STEP = 0.05


@dataclass(frozen=True)
class VolumeBounds:
    """Config-owned clamp bounds for this agent's own volume.

    A community narrows these in config, not code (mirrors the action
    whitelist being data, per CLAUDE.md's halachic-scope section).
    """

    min_volume: float = DEFAULT_MIN_VOLUME
    max_volume: float = DEFAULT_MAX_VOLUME
    step: float = DEFAULT_VOLUME_STEP

    def __post_init__(self) -> None:
        if self.min_volume < 0:
            raise ValueError(f"min_volume must be >= 0, got {self.min_volume!r}")
        if self.max_volume < self.min_volume:
            raise ValueError(
                f"max_volume ({self.max_volume!r}) must be >= min_volume ({self.min_volume!r})"
            )
        if self.step <= 0:
            raise ValueError(f"step must be > 0, got {self.step!r}")


def clamp_volume(level: float, bounds: VolumeBounds = VolumeBounds()) -> float:
    """*level* clamped into ``[bounds.min_volume, bounds.max_volume]``."""
    return max(bounds.min_volume, min(bounds.max_volume, level))


@dataclass(frozen=True)
class AudioConfig:
    """This agent's own audio configuration: which nodes, and the volume it
    owns on them.

    ``volume_node`` defaults to ``speaker_node`` (the common case: shabbos-goy
    controls its own playback level, not the system mixer). The defaults
    (``default_volume=0.0``, ``default_muted=True``) are deliberate: a fresh
    config, never touched by an operator, plays nothing (criterion 2) —
    matching invariant #5 (spoken output is neutral and opt-in, never a
    surprise) and the halachic-scope rule that whitelists/config start
    narrow, not permissive.
    """

    mic_node: str
    speaker_node: str
    volume_node: str | None = None
    bounds: VolumeBounds = field(default_factory=VolumeBounds)
    default_volume: float = DEFAULT_MIN_VOLUME
    default_muted: bool = True
    allow_split_devices: bool = False

    def resolved_volume_node(self) -> str:
        return self.volume_node if self.volume_node is not None else self.speaker_node

    def validate(self) -> None:
        """Refuse a mismatched mic/speaker pair. Raises :class:`DeviceMismatchError`."""
        validate_device_pair(self.mic_node, self.speaker_node, self.allow_split_devices)


def default_config(mic_node: str, speaker_node: str, **overrides) -> AudioConfig:
    """An :class:`AudioConfig` for *mic_node*/*speaker_node* with every other
    field at its silent-by-default value (criterion 2). ``**overrides`` are
    passed straight through to :class:`AudioConfig` for callers that need to
    override one field (e.g. a fixture setting ``allow_split_devices``)."""
    return AudioConfig(mic_node=mic_node, speaker_node=speaker_node, **overrides)


def build_capture_and_playback_argv(config: AudioConfig) -> tuple[list[str], list[str]]:
    """Validate *config*'s device pair, then build both argvs.

    Raises :class:`DeviceMismatchError` before building anything if the pair
    does not match and ``allow_split_devices`` is not set.
    """
    config.validate()
    return (
        build_capture_argv(config.mic_node),
        build_playback_argv(config.speaker_node),
    )


# ---------------------------------------------------------------------------
# wpctl argv builders + output parsing (pure — no subprocess here).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class VolumeState:
    level: float
    muted: bool


def build_get_volume_argv(target: str) -> list[str]:
    return ["wpctl", "get-volume", _safe_target(target)]


def build_set_volume_argv(target: str, level: float) -> list[str]:
    return ["wpctl", "set-volume", _safe_target(target), f"{level:.2f}"]


def build_set_mute_argv(target: str, muted: bool) -> list[str]:
    return ["wpctl", "set-mute", _safe_target(target), "1" if muted else "0"]


_VOLUME_LINE_RE = re.compile(r"Volume:\s*([0-9]*\.?[0-9]+)\s*(\[MUTED\])?", re.IGNORECASE)


def parse_volume_output(text: str) -> VolumeState:
    """Parse ``wpctl get-volume``'s stdout (``"Volume: 0.45"`` or
    ``"Volume: 0.45 [MUTED]"``) into a :class:`VolumeState`.

    Raises :class:`VolumeCommandError`, never a bare regex/ValueError, on
    unparseable output.
    """
    match = _VOLUME_LINE_RE.search(text)
    if match is None:
        raise VolumeCommandError(f"could not parse 'wpctl get-volume' output: {text!r}")
    return VolumeState(level=float(match.group(1)), muted=match.group(2) is not None)


def step_volume(current: float, steps: int, bounds: VolumeBounds = VolumeBounds()) -> float:
    """*current* volume moved by *steps* increments of ``bounds.step``,
    clamped to ``bounds`` (criterion 2: "volume steps are clamped to config
    bounds"). *steps* may be negative (volume down)."""
    return clamp_volume(current + steps * bounds.step, bounds)


# ---------------------------------------------------------------------------
# Subprocess wrappers. Every one accepts an injectable ``runner``/``popen``
# so tests point at fake executables (tests/fixtures/fake_pipewire/) instead
# of the real ones, per the task's safety rule.
# ---------------------------------------------------------------------------


def _run(argv: list[str], *, runner=subprocess.run, env: dict[str, str] | None = None) -> str:
    try:
        result = runner(
            argv,
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
            env=env if env is not None else os.environ.copy(),
        )
    except OSError as exc:
        raise VolumeCommandError(f"failed to run {argv[0]!r}: {exc}") from exc
    if result.returncode != 0:
        raise VolumeCommandError(
            f"{' '.join(argv)!r} exited {result.returncode}: {result.stderr.strip()}"
        )
    return result.stdout


def get_volume(
    target: str, *, runner=subprocess.run, env: dict[str, str] | None = None
) -> VolumeState:
    """The current level/muted state of *target*, read through ``wpctl``."""
    output = _run(build_get_volume_argv(target), runner=runner, env=env)
    return parse_volume_output(output)


def set_volume(
    target: str,
    level: float,
    bounds: VolumeBounds = VolumeBounds(),
    *,
    runner=subprocess.run,
    env: dict[str, str] | None = None,
) -> float:
    """Set *target*'s volume to *level*, clamped to *bounds* first. Returns
    the clamped level actually sent to ``wpctl``."""
    clamped = clamp_volume(level, bounds)
    _run(build_set_volume_argv(target, clamped), runner=runner, env=env)
    return clamped


def set_mute(
    target: str, muted: bool, *, runner=subprocess.run, env: dict[str, str] | None = None
) -> None:
    """Mute/unmute *target* through ``wpctl``."""
    _run(build_set_mute_argv(target, muted), runner=runner, env=env)


def adjust_volume(
    target: str,
    steps: int,
    bounds: VolumeBounds = VolumeBounds(),
    *,
    runner=subprocess.run,
    env: dict[str, str] | None = None,
) -> VolumeState:
    """Read *target*'s current level, step it by *steps* (clamped), write it
    back, and return the resulting :class:`VolumeState`. Mute state is left
    untouched — a volume step is never itself an unmute."""
    state = get_volume(target, runner=runner, env=env)
    new_level = step_volume(state.level, steps, bounds)
    set_volume(target, new_level, bounds, runner=runner, env=env)
    return VolumeState(level=new_level, muted=state.muted)


def apply_startup_state(
    config: AudioConfig, *, runner=subprocess.run, env: dict[str, str] | None = None
) -> VolumeState:
    """Re-apply *config*'s configured level/mute at process start-up
    (criterion 2): clamp ``default_volume`` to ``bounds``, set it, then set
    mute — so a freshly started process's audio state always matches config
    rather than whatever PipeWire happened to have left over from a previous
    run or another application. With the untouched default config
    (``default_volume=0.0``, ``default_muted=True``) this leaves the node
    silent, i.e. the default configuration plays nothing.
    """
    target = config.resolved_volume_node()
    level = set_volume(target, config.default_volume, config.bounds, runner=runner, env=env)
    set_mute(target, config.default_muted, runner=runner, env=env)
    return VolumeState(level=level, muted=config.default_muted)


def start_capture(
    device: str,
    *,
    rate: int = CAPTURE_SAMPLE_RATE_DEFAULT,
    channels: int = 1,
    popen=subprocess.Popen,
    env: dict[str, str] | None = None,
) -> subprocess.Popen:
    """Start the ``pw-record`` subprocess for *device*, stdout piped.

    Raises :class:`AudioBackendError` immediately if the binary could not
    even be started (missing executable) — never a silent spawn failure.
    """
    argv = build_capture_argv(device, rate=rate, channels=channels)
    try:
        return popen(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env if env is not None else os.environ.copy(),
        )
    except OSError as exc:
        raise AudioBackendError(f"failed to start {argv[0]!r}: {exc}") from exc


def start_playback(
    device: str,
    *,
    rate: int = PLAYBACK_SAMPLE_RATE_DEFAULT,
    channels: int = 1,
    popen=subprocess.Popen,
    env: dict[str, str] | None = None,
) -> subprocess.Popen:
    """Start the ``pw-play`` subprocess for *device*, stdin piped.

    Raises :class:`AudioBackendError` immediately if the binary could not
    even be started (missing executable) — never a silent spawn failure.
    """
    argv = build_playback_argv(device, rate=rate, channels=channels)
    try:
        return popen(
            argv,
            stdin=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env if env is not None else os.environ.copy(),
        )
    except OSError as exc:
        raise AudioBackendError(f"failed to start {argv[0]!r}: {exc}") from exc

"""Tests for shabbos_goy.audio.pipewire.

Every test that actually executes a subprocess points PATH at the fake
pw-record/pw-play/wpctl executables in tests/fixtures/fake_pipewire/ — no
real microphone, speaker, PipeWire daemon, or system volume is ever touched
(the task's safety rule). Pure functions (argv builders, the device-pair
check, clamping) are tested with no subprocess at all.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from shabbos_goy.audio import pipewire as pw

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "fake_pipewire"


@pytest.fixture()
def fake_pipewire_env(tmp_path) -> dict[str, str]:
    """A subprocess env whose PATH resolves pw-record/pw-play/wpctl to the
    fakes, with a fresh, isolated state file for the fake wpctl."""
    env = os.environ.copy()
    env["PATH"] = f"{FIXTURES_DIR}{os.pathsep}{env.get('PATH', '')}"
    env["WPCTL_FAKE_STATE"] = str(tmp_path / "wpctl-state.json")
    return env


# ---------------------------------------------------------------------------
# normalize_pipewire_device_name / device_identity / validate_device_pair
# (cited from lobes-cli's realtime-he-accept.py; re-verified here because
# this module owns its own copy).
# ---------------------------------------------------------------------------


def test_normalize_strips_direction_prefix_and_profile_suffix() -> None:
    name = "alsa_input.usb-Seeed_ReSpeaker-00.multichannel-input"
    assert pw.normalize_pipewire_device_name(name) == "usb-seeed_respeaker-00"


def test_normalize_treats_capture_and_playback_nodes_as_same_device() -> None:
    mic = "alsa_input.usb-Seeed_ReSpeaker-00.multichannel-input"
    speaker = "alsa_output.usb-Seeed_ReSpeaker-00.analog-stereo"
    assert pw.normalize_pipewire_device_name(mic) == pw.normalize_pipewire_device_name(speaker)


def test_normalize_empty_name() -> None:
    assert pw.normalize_pipewire_device_name("") == ""


def test_normalize_leaves_unknown_prefix_suffix_alone() -> None:
    # Deliberately conservative: an unrecognized shape is left as-is.
    assert pw.normalize_pipewire_device_name("some-weird-node") == "some-weird-node"


def test_validate_device_pair_accepts_matching_respeaker_nodes() -> None:
    mic = "alsa_input.usb-Seeed_ReSpeaker-00.multichannel-input"
    speaker = "alsa_output.usb-Seeed_ReSpeaker-00.analog-stereo"
    pw.validate_device_pair(mic, speaker)  # must not raise


def test_validate_device_pair_refuses_mismatched_devices() -> None:
    with pytest.raises(pw.DeviceMismatchError):
        pw.validate_device_pair("alsa_input.usb-Seeed_ReSpeaker-00.multichannel-input", "hdmi-out")


def test_validate_device_pair_allow_split_devices_overrides() -> None:
    pw.validate_device_pair("mic-node", "different-speaker-node", allow_split_devices=True)


def test_device_mismatch_error_message_names_both_devices() -> None:
    try:
        pw.validate_device_pair("mic-a", "speaker-b")
    except pw.DeviceMismatchError as exc:
        assert "mic-a" in str(exc)
        assert "speaker-b" in str(exc)
        assert "allow_split_devices" in str(exc)
    else:
        pytest.fail("expected DeviceMismatchError")


# ---------------------------------------------------------------------------
# build_capture_argv / build_playback_argv — target the node by name.
# ---------------------------------------------------------------------------


def test_build_capture_argv_targets_node_by_name() -> None:
    argv = pw.build_capture_argv("alsa_input.usb-Seeed_ReSpeaker-00.multichannel-input")
    assert argv[0] == "pw-record"
    assert "--target" in argv
    assert (
        argv[argv.index("--target") + 1] == "alsa_input.usb-Seeed_ReSpeaker-00.multichannel-input"
    )
    assert "--rate" in argv and str(pw.CAPTURE_SAMPLE_RATE_DEFAULT) in argv
    assert argv[-1] == "-"


def test_build_capture_argv_honors_rate_and_channels() -> None:
    argv = pw.build_capture_argv("node", rate=48000, channels=2)
    assert argv[argv.index("--rate") + 1] == "48000"
    assert argv[argv.index("--channels") + 1] == "2"


def test_build_playback_argv_targets_node_by_name() -> None:
    argv = pw.build_playback_argv("alsa_output.usb-Seeed_ReSpeaker-00.analog-stereo")
    assert argv[0] == "pw-play"
    assert argv[argv.index("--target") + 1] == "alsa_output.usb-Seeed_ReSpeaker-00.analog-stereo"
    assert str(pw.PLAYBACK_SAMPLE_RATE_DEFAULT) in argv


def test_build_capture_and_playback_argv_validates_first() -> None:
    config = pw.default_config(mic_node="mic-a", speaker_node="speaker-b")
    with pytest.raises(pw.DeviceMismatchError):
        pw.build_capture_and_playback_argv(config)


def test_build_capture_and_playback_argv_matching_pair() -> None:
    mic = "alsa_input.usb-Seeed_ReSpeaker-00.multichannel-input"
    speaker = "alsa_output.usb-Seeed_ReSpeaker-00.analog-stereo"
    config = pw.default_config(mic_node=mic, speaker_node=speaker)
    capture_argv, playback_argv = pw.build_capture_and_playback_argv(config)
    assert capture_argv[0] == "pw-record"
    assert playback_argv[0] == "pw-play"


# ---------------------------------------------------------------------------
# Volume bounds + clamping (pure).
# ---------------------------------------------------------------------------


def test_volume_bounds_rejects_invalid_ranges() -> None:
    with pytest.raises(ValueError):
        pw.VolumeBounds(min_volume=-0.1)
    with pytest.raises(ValueError):
        pw.VolumeBounds(min_volume=0.5, max_volume=0.2)
    with pytest.raises(ValueError):
        pw.VolumeBounds(step=0.0)


def test_clamp_volume_within_bounds_unchanged() -> None:
    bounds = pw.VolumeBounds(min_volume=0.1, max_volume=0.9)
    assert pw.clamp_volume(0.5, bounds) == 0.5


def test_clamp_volume_clamps_above_max() -> None:
    bounds = pw.VolumeBounds(min_volume=0.0, max_volume=0.8)
    assert pw.clamp_volume(1.5, bounds) == 0.8


def test_clamp_volume_clamps_below_min() -> None:
    bounds = pw.VolumeBounds(min_volume=0.2, max_volume=1.0)
    assert pw.clamp_volume(-1.0, bounds) == 0.2


def test_step_volume_up_clamped_to_bounds() -> None:
    bounds = pw.VolumeBounds(min_volume=0.0, max_volume=0.5, step=0.4)
    # 0.3 + 0.4 = 0.7, clamped down to the configured max of 0.5.
    assert pw.step_volume(0.3, 1, bounds) == 0.5


def test_step_volume_down_clamped_to_bounds() -> None:
    bounds = pw.VolumeBounds(min_volume=0.2, max_volume=1.0, step=0.4)
    # 0.3 - 0.4 = -0.1, clamped up to the configured min of 0.2.
    assert pw.step_volume(0.3, -1, bounds) == 0.2


def test_step_volume_multiple_steps() -> None:
    bounds = pw.VolumeBounds(min_volume=0.0, max_volume=1.0, step=0.1)
    assert pw.step_volume(0.5, 3, bounds) == pytest.approx(0.8)


# ---------------------------------------------------------------------------
# AudioConfig defaults — "the default configuration plays nothing".
# ---------------------------------------------------------------------------


def test_default_config_is_silent() -> None:
    config = pw.default_config(mic_node="mic", speaker_node="mic")
    assert config.default_volume == 0.0
    assert config.default_muted is True


def test_default_config_resolves_volume_node_to_speaker() -> None:
    config = pw.default_config(mic_node="mic", speaker_node="speaker")
    assert config.resolved_volume_node() == "speaker"


def test_default_config_volume_node_override() -> None:
    config = pw.default_config(mic_node="mic", speaker_node="speaker", volume_node="mixer-node")
    assert config.resolved_volume_node() == "mixer-node"


# ---------------------------------------------------------------------------
# wpctl argv builders + output parsing (pure).
# ---------------------------------------------------------------------------


def test_build_get_volume_argv() -> None:
    assert pw.build_get_volume_argv("42") == ["wpctl", "get-volume", "42"]


def test_build_set_volume_argv_formats_two_decimals() -> None:
    assert pw.build_set_volume_argv("42", 0.5) == ["wpctl", "set-volume", "42", "0.50"]


def test_build_set_mute_argv() -> None:
    assert pw.build_set_mute_argv("42", True) == ["wpctl", "set-mute", "42", "1"]
    assert pw.build_set_mute_argv("42", False) == ["wpctl", "set-mute", "42", "0"]


def test_parse_volume_output_unmuted() -> None:
    state = pw.parse_volume_output("Volume: 0.45\n")
    assert state.level == pytest.approx(0.45)
    assert state.muted is False


def test_parse_volume_output_muted() -> None:
    state = pw.parse_volume_output("Volume: 0.30 [MUTED]\n")
    assert state.level == pytest.approx(0.30)
    assert state.muted is True


def test_parse_volume_output_unparseable_raises() -> None:
    with pytest.raises(pw.VolumeCommandError):
        pw.parse_volume_output("nonsense output\n")


# ---------------------------------------------------------------------------
# get_volume / set_volume / set_mute / adjust_volume / apply_startup_state —
# real subprocess calls, but ALWAYS against the fake wpctl on PATH.
# ---------------------------------------------------------------------------


def test_get_volume_via_fake_wpctl(fake_pipewire_env: dict[str, str]) -> None:
    state = pw.get_volume("respeaker-sink", env=fake_pipewire_env)
    assert state.level == pytest.approx(0.5)
    assert state.muted is False


def test_set_volume_clamps_then_round_trips(fake_pipewire_env: dict[str, str]) -> None:
    bounds = pw.VolumeBounds(min_volume=0.0, max_volume=0.6)
    applied = pw.set_volume("respeaker-sink", 0.9, bounds, env=fake_pipewire_env)
    assert applied == pytest.approx(0.6)
    state = pw.get_volume("respeaker-sink", env=fake_pipewire_env)
    assert state.level == pytest.approx(0.6)


def test_set_mute_round_trips(fake_pipewire_env: dict[str, str]) -> None:
    pw.set_mute("respeaker-sink", True, env=fake_pipewire_env)
    state = pw.get_volume("respeaker-sink", env=fake_pipewire_env)
    assert state.muted is True
    pw.set_mute("respeaker-sink", False, env=fake_pipewire_env)
    state = pw.get_volume("respeaker-sink", env=fake_pipewire_env)
    assert state.muted is False


def test_adjust_volume_steps_and_clamps(fake_pipewire_env: dict[str, str]) -> None:
    bounds = pw.VolumeBounds(min_volume=0.0, max_volume=1.0, step=0.2)
    # fake wpctl starts an unknown target at 0.50.
    state = pw.adjust_volume("respeaker-sink", 1, bounds, env=fake_pipewire_env)
    assert state.level == pytest.approx(0.7)
    state = pw.adjust_volume("respeaker-sink", 3, bounds, env=fake_pipewire_env)
    # 0.7 + 3*0.2 = 1.3, clamped to 1.0.
    assert state.level == pytest.approx(1.0)


def test_get_volume_missing_node_raises_volume_command_error(
    fake_pipewire_env: dict[str, str],
) -> None:
    with pytest.raises(pw.VolumeCommandError):
        pw.get_volume("missing-node", env=fake_pipewire_env)


def test_apply_startup_state_reapplies_configured_level(fake_pipewire_env: dict[str, str]) -> None:
    config = pw.default_config(
        mic_node="respeaker-mic",
        speaker_node="respeaker-sink",
        default_volume=0.25,
        default_muted=False,
    )
    result = pw.apply_startup_state(config, env=fake_pipewire_env)
    assert result.level == pytest.approx(0.25)
    assert result.muted is False
    state = pw.get_volume("respeaker-sink", env=fake_pipewire_env)
    assert state.level == pytest.approx(0.25)
    assert state.muted is False


def test_apply_startup_state_default_config_leaves_node_silent(
    fake_pipewire_env: dict[str, str],
) -> None:
    """Criterion 2: "the default configuration plays nothing" — a fresh,
    untouched AudioConfig applied at start-up leaves the node at 0 volume
    AND muted, regardless of whatever the fake wpctl's "unknown target"
    default (0.5, unmuted) would otherwise report."""
    config = pw.default_config(mic_node="respeaker-mic", speaker_node="respeaker-sink")
    result = pw.apply_startup_state(config, env=fake_pipewire_env)
    assert result.level == 0.0
    assert result.muted is True
    state = pw.get_volume("respeaker-sink", env=fake_pipewire_env)
    assert state.level == 0.0
    assert state.muted is True


def test_apply_startup_state_clamps_default_volume_to_bounds(
    fake_pipewire_env: dict[str, str],
) -> None:
    bounds = pw.VolumeBounds(min_volume=0.0, max_volume=0.4)
    config = pw.default_config(
        mic_node="respeaker-mic",
        speaker_node="respeaker-sink",
        bounds=bounds,
        default_volume=0.9,
        default_muted=False,
    )
    result = pw.apply_startup_state(config, env=fake_pipewire_env)
    assert result.level == pytest.approx(0.4)


def test_apply_startup_state_uses_volume_node_override(fake_pipewire_env: dict[str, str]) -> None:
    config = pw.default_config(
        mic_node="respeaker-mic",
        speaker_node="respeaker-sink",
        volume_node="mixer-node",
        default_volume=0.3,
        default_muted=False,
    )
    pw.apply_startup_state(config, env=fake_pipewire_env)
    state = pw.get_volume("mixer-node", env=fake_pipewire_env)
    assert state.level == pytest.approx(0.3)
    # The speaker node itself was never touched.
    untouched = pw.get_volume("respeaker-sink", env=fake_pipewire_env)
    assert untouched.level == pytest.approx(0.5)  # fake wpctl's unknown-target default


def test_volume_command_error_never_runs_real_wpctl(fake_pipewire_env: dict[str, str]) -> None:
    """Sanity check that the fixture actually redirected PATH: the resolved
    'wpctl' binary is the fake one, not any real system wpctl."""
    resolved = subprocess.run(
        ["which", "wpctl"], capture_output=True, text=True, env=fake_pipewire_env, check=False
    )
    assert str(FIXTURES_DIR) in resolved.stdout


# ---------------------------------------------------------------------------
# start_capture / start_playback — real Popen, but only ever the fakes.
# ---------------------------------------------------------------------------


def test_start_capture_runs_fake_pw_record_and_yields_bytes(
    fake_pipewire_env: dict[str, str],
) -> None:
    proc = pw.start_capture("respeaker-mic", env=fake_pipewire_env)
    try:
        out, err = proc.communicate(timeout=5)
        assert proc.returncode == 0
        assert out == b"\x00\x00" * 64
        # stderr is DEVNULL, not a pipe: nothing in this process ever reads a
        # capture child's stderr, and an undrained pipe fills and wedges the
        # child, which then outlives its session (review thread #4).
        assert err is None
    finally:
        if proc.poll() is None:
            proc.kill()


def test_start_capture_missing_node_reports_failure_via_exit_code(
    fake_pipewire_env: dict[str, str],
) -> None:
    proc = pw.start_capture("missing-node", env=fake_pipewire_env)
    out, err = proc.communicate(timeout=5)
    # The exit code is the signal; the child's own diagnostics go to DEVNULL
    # (see above), so a failure is named by our code, never echoed from its.
    assert proc.returncode != 0
    assert out == b""
    assert err is None


def test_start_capture_missing_binary_raises_audio_backend_error(tmp_path) -> None:
    env = os.environ.copy()
    env["PATH"] = str(tmp_path)  # empty dir: no pw-record on PATH at all
    with pytest.raises(pw.AudioBackendError):
        pw.start_capture("respeaker-mic", env=env)


def test_start_playback_runs_fake_pw_play_and_drains_stdin(
    fake_pipewire_env: dict[str, str],
) -> None:
    proc = pw.start_playback("respeaker-sink", env=fake_pipewire_env)
    try:
        _, err = proc.communicate(input=b"\x01\x02\x03\x04", timeout=5)
        assert proc.returncode == 0
        assert err == b""
    finally:
        if proc.poll() is None:
            proc.kill()


def test_start_playback_missing_binary_raises_audio_backend_error(tmp_path) -> None:
    env = os.environ.copy()
    env["PATH"] = str(tmp_path)
    with pytest.raises(pw.AudioBackendError):
        pw.start_playback("respeaker-sink", env=env)


@pytest.mark.parametrize("hostile", ["", "-h", "--target", "--help", "a b", "node\tname", "x\n"])
def test_option_shaped_targets_are_refused_by_every_argv_builder(hostile: str) -> None:
    """Wave-1 close-out hardening: a node name can never become an option."""
    from shabbos_goy.audio import pipewire as pw

    for build in (
        lambda: pw.build_capture_argv(hostile),
        lambda: pw.build_playback_argv(hostile),
        lambda: pw.build_get_volume_argv(hostile),
        lambda: pw.build_set_volume_argv(hostile, 0.3),
        lambda: pw.build_set_mute_argv(hostile, True),
    ):
        with pytest.raises(ValueError):
            build()


def test_well_known_alias_and_plain_names_are_still_accepted() -> None:
    from shabbos_goy.audio import pipewire as pw

    assert "@DEFAULT_AUDIO_SINK@" in pw.build_get_volume_argv("@DEFAULT_AUDIO_SINK@")
    assert "42" in pw.build_set_mute_argv(42, False)

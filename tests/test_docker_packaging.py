"""Offline packaging checks for the Docker image and Compose service (t16).

Everything here parses checked-in files with ``pyyaml`` (a dev-only
dependency — never imported by the runtime package) or shells out to this
repo's own ``scripts/scan-secrets.py``. Nothing here builds an image, starts
a container, touches audio, or uses a real key/host/pod id — see
``docker/shabbos-goy.env.example`` for the placeholders this test expects.

``docker compose config -q`` and a plain ``docker build`` were run BY HAND
while writing this task (see the task's final report for exact output) —
they are deliberately not wired into this suite, which must pass with no
Docker daemon available at all (CI's `lint` job that runs `docker compose
config -q` is separate, see ``.github/workflows/tests.yml``).
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
DOCKERFILE_PATH = ROOT / "Dockerfile"
COMPOSE_PATH = ROOT / "docker-compose.yml"
ENV_EXAMPLE_PATH = ROOT / "docker" / "shabbos-goy.env.example"
GITIGNORE_PATH = ROOT / ".gitignore"
DOCKERIGNORE_PATH = ROOT / ".dockerignore"


def _compose() -> dict:
    return yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))


def _service() -> dict:
    services = _compose()["services"]
    assert len(services) == 1, f"expected exactly one service, got {list(services)!r}"
    return next(iter(services.values()))


# ---------------------------------------------------------------------------
# Files exist at all
# ---------------------------------------------------------------------------


def test_dockerfile_exists():
    assert DOCKERFILE_PATH.is_file()


def test_compose_file_exists_and_parses_as_yaml():
    assert COMPOSE_PATH.is_file()
    data = _compose()
    assert isinstance(data, dict)
    assert "services" in data


def test_env_example_is_committed():
    assert ENV_EXAMPLE_PATH.is_file()


# ---------------------------------------------------------------------------
# docker-compose.yml: restart / logging / mounts / env_file
# ---------------------------------------------------------------------------


def test_restart_policy_is_unless_stopped():
    assert _service().get("restart") == "unless-stopped"


def test_logging_is_json_file_with_rotation():
    logging_block = _service().get("logging", {})
    assert logging_block.get("driver") == "json-file"
    options = logging_block.get("options", {})
    assert options.get("max-size") == "10m"
    assert str(options.get("max-file")) == "3"


def test_config_bind_mount_is_read_only():
    volumes = _service().get("volumes", [])
    assert volumes, "expected at least one volume mount (the read-only config dir)"
    config_mounts = [
        v
        for v in volumes
        if isinstance(v, str) and "shabbos-goy" in v and v.rstrip().endswith(":ro")
    ]
    assert config_mounts, f"expected a shabbos-goy config dir mounted :ro, got {volumes!r}"


def test_pipewire_socket_is_mounted_not_dev_snd():
    volumes = _service().get("volumes", [])
    assert any("pipewire-0" in v for v in volumes if isinstance(v, str)), volumes
    assert not any("/dev/snd" in v for v in volumes if isinstance(v, str)), volumes
    assert "devices" not in _service()


def test_env_file_declared_and_gitignored_with_committed_example():
    env_file = _service().get("env_file")
    assert env_file, "expected env_file: on the service"
    entries = env_file if isinstance(env_file, list) else [env_file]
    assert any("docker/shabbos-goy.env" in str(entry) for entry in entries)

    gitignore_text = GITIGNORE_PATH.read_text(encoding="utf-8")
    assert "docker/shabbos-goy.env" in gitignore_text
    # The example itself must not be caught by that ignore line.
    result = subprocess.run(
        ["git", "check-ignore", "-q", "docker/shabbos-goy.env.example"],
        cwd=ROOT,
    )
    assert result.returncode != 0, "docker/shabbos-goy.env.example must NOT be gitignored"


def test_no_privileged_mode():
    text = COMPOSE_PATH.read_text(encoding="utf-8")
    assert "privileged" not in text


def test_non_root_user_configured():
    user = _service().get("user")
    assert user, "expected a non-root user: entry"
    assert str(user) not in ("0", "0:0", "root", "root:root")


def test_healthcheck_reads_the_heartbeat():
    healthcheck = _service().get("healthcheck", {})
    test_cmd = healthcheck.get("test")
    assert test_cmd, "expected a healthcheck test command"
    joined = " ".join(test_cmd) if isinstance(test_cmd, list) else str(test_cmd)
    assert "shabbos-goy" in joined
    assert "listen" in joined
    assert "--healthcheck" in joined
    for key in ("interval", "timeout", "retries", "start_period"):
        assert key in healthcheck, f"healthcheck missing {key!r}: {healthcheck!r}"


def test_no_latest_tag_anywhere_in_compose():
    text = COMPOSE_PATH.read_text(encoding="utf-8")
    assert ":latest" not in text


def test_default_command_is_dry_run_listen():
    command = _service().get("command")
    if command is None:
        # No override: the Dockerfile's own CMD must be the dry-run default.
        dockerfile_text = DOCKERFILE_PATH.read_text(encoding="utf-8")
        assert re.search(r'CMD\s*\[.*"listen".*\]', dockerfile_text)
        assert "--apply" not in re.search(r"CMD\s*\[.*\]", dockerfile_text, re.DOTALL).group(0)
    else:
        joined = " ".join(command) if isinstance(command, list) else str(command)
        assert "listen" in joined
        assert "--apply" not in joined


def test_apply_is_only_documented_as_a_comment():
    text = COMPOSE_PATH.read_text(encoding="utf-8")
    apply_lines = [line for line in text.splitlines() if "--apply" in line]
    assert apply_lines, "expected a commented-out --apply example"
    for line in apply_lines:
        assert line.lstrip().startswith("#"), f"--apply must stay commented out: {line!r}"


# ---------------------------------------------------------------------------
# Dockerfile: base image, pinned sensibo-cli, no `latest`
# ---------------------------------------------------------------------------


def test_dockerfile_base_image_is_pinned_slim_python():
    text = DOCKERFILE_PATH.read_text(encoding="utf-8")
    from_lines = [line for line in text.splitlines() if line.strip().upper().startswith("FROM")]
    assert from_lines, "expected a FROM line"
    assert any("python:3.12-slim" in line for line in from_lines)
    assert not any(":latest" in line for line in from_lines)


def test_dockerfile_pins_sensibo_cli_exact_version():
    text = DOCKERFILE_PATH.read_text(encoding="utf-8")
    assert re.search(
        r"sensibo-cli==\d+\.\d+\.\d+", text
    ), "sensibo-cli must be pinned to an exact version, not a range or 'latest'"


def test_dockerfile_never_uses_latest_tag():
    text = DOCKERFILE_PATH.read_text(encoding="utf-8")
    assert ":latest" not in text


def test_dockerfile_installs_pipewire_client_tools():
    text = DOCKERFILE_PATH.read_text(encoding="utf-8")
    assert "pipewire-bin" in text
    assert "wireplumber" in text


def test_dockerfile_creates_non_root_user():
    text = DOCKERFILE_PATH.read_text(encoding="utf-8")
    assert "useradd" in text or "adduser" in text
    assert re.search(r"^USER\s+\S+", text, re.MULTILINE)


# ---------------------------------------------------------------------------
# No secret-looking literal in any of the new packaging files.
# ---------------------------------------------------------------------------


def test_no_secret_looking_literal_in_packaging_files():
    targets = ["docker-compose.yml", "Dockerfile", "docker/shabbos-goy.env.example"]
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "scan-secrets.py"), *targets],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_env_example_has_no_real_lobes_host_or_key():
    text = ENV_EXAMPLE_PATH.read_text(encoding="utf-8")
    assert "SHABBOS_GOY_LOBES_URL" in text
    assert "SENSIBO_API_KEY" in text
    # Placeholder-only: never a bare non-localhost concrete host.
    for line in text.splitlines():
        if "=" not in line or line.strip().startswith("#"):
            continue
        _, _, value = line.partition("=")
        value = value.strip()
        if not value:
            continue
        assert "sk-" not in value
        assert not re.match(r"^\d{1,3}(\.\d{1,3}){3}$", value)

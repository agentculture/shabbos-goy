"""JSON config + whitelist loader that fails closed (stable-contract).

Stdlib ``json`` only — pyyaml is dev-only and never imported here, per
``shabbos_goy``'s zero-runtime-dependency rule.

The config file is resolved, in priority order, from:

1. an explicit ``path`` argument (a CLI ``--config`` flag);
2. the ``SHABBOS_GOY_CONFIG`` environment variable (a full file path);
3. ``$XDG_CONFIG_HOME/shabbos-goy/config.json``, falling back to
   ``~/.config/shabbos-goy/config.json`` when ``XDG_CONFIG_HOME`` is unset.

**Fails closed.** A missing file, an unreadable file, or malformed/wrong-shape
JSON never raises out of :func:`load_config`. Each instead returns a
:class:`Config` whose effective whitelist is empty and whose ``.error`` holds
a named :class:`~shabbos_goy.cli._errors.CliError` for a CLI caller to surface
(exit code, message, remediation). Nothing downstream can accidentally treat a
broken config as "everything is whitelisted" — the failure mode is always
"nothing is whitelisted".

The whitelist itself is pure config: it is whatever ``whitelist`` says in the
JSON, with no built-in fallback list layered on top. Removing a tool key or a
pod id from the JSON removes it from :meth:`Config.is_whitelisted` with no
code change.

See ``tests/fixtures/config.example.json`` for the full shape (placeholder
values only — never a real pod id, host or key).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from shabbos_goy.cli._errors import EXIT_ENV_ERROR, EXIT_USER_ERROR, CliError

#: Env var carrying an explicit config file path (highest-priority override
#: short of a CLI flag passed straight to :func:`load_config`/`resolve_config_path`).
ENV_CONFIG_PATH = "SHABBOS_GOY_CONFIG"

#: Standard XDG base-directory env var for the config-dir fallback.
ENV_XDG_CONFIG_HOME = "XDG_CONFIG_HOME"

CONFIG_DIRNAME = "shabbos-goy"
CONFIG_FILENAME = "config.json"

#: The only representable AC action, and its only two valid values. Anything
#: else is a validation error — see :func:`validate_ac_argument`.
AC_ACTION = "power"
ALLOWED_POWER_VALUES = ("on", "off")


def default_config_dir(env: Mapping[str, str] | None = None) -> Path:
    """``$XDG_CONFIG_HOME/shabbos-goy``, or ``~/.config/shabbos-goy`` if unset."""
    env = os.environ if env is None else env
    xdg = env.get(ENV_XDG_CONFIG_HOME)
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / CONFIG_DIRNAME


def resolve_config_path(
    *, path: str | Path | None = None, env: Mapping[str, str] | None = None
) -> Path:
    """Resolve the config file path: explicit ``path`` > env var > XDG default.

    ``path`` stands in for a CLI ``--config`` flag; callers pass whatever the
    flag parsed to (or ``None`` when the flag was not given). ``env`` is
    injectable so tests never depend on (or mutate) the real environment or
    ``$HOME``.
    """
    if path is not None:
        return Path(path)
    env = os.environ if env is None else env
    env_path = env.get(ENV_CONFIG_PATH)
    if env_path:
        return Path(env_path)
    return default_config_dir(env) / CONFIG_FILENAME


@dataclass(frozen=True)
class Config:
    """The effective configuration.

    Every accessor fails closed: once ``.error`` is set, ``.whitelist`` and
    every other structured accessor return the empty/``None`` safe default
    regardless of what (if anything) ``.raw`` holds.
    """

    path: Path
    raw: dict = field(default_factory=dict)
    error: CliError | None = None

    @property
    def ok(self) -> bool:
        return self.error is None

    @property
    def whitelist(self) -> dict:
        """The effective actuation whitelist: tool name -> {pods, actions}.

        This is *only* what the JSON says. There is no hardcoded fallback
        list — removing a tool key or a pod id from the config file is
        sufficient, by itself, to remove it here.
        """
        if not self.ok:
            return {}
        wl = self.raw.get("whitelist")
        return wl if isinstance(wl, dict) else {}

    @property
    def location(self) -> dict:
        return self.raw.get("location", {}) if self.ok else {}

    @property
    def candle_lighting_offset_minutes(self) -> object:
        return self.raw.get("candle_lighting_offset_minutes") if self.ok else None

    @property
    def tzeit_definition(self) -> object:
        return self.raw.get("tzeit_definition") if self.ok else None

    @property
    def region(self) -> object:
        """``"israel"`` or ``"diaspora"`` (or whatever the config says)."""
        return self.raw.get("region") if self.ok else None

    @property
    def rate_limits(self) -> dict:
        return self.raw.get("rate_limits", {}) if self.ok else {}

    @property
    def strict_mode_delay_seconds(self) -> object:
        return self.raw.get("strict_mode_delay_seconds") if self.ok else None

    @property
    def volume(self) -> dict:
        return self.raw.get("volume", {}) if self.ok else {}

    @property
    def join_gap_ms(self) -> object:
        return self.raw.get("join_gap_ms") if self.ok else None

    @property
    def ring_sizes(self) -> dict:
        return self.raw.get("ring_sizes", {}) if self.ok else {}

    @property
    def dashboard_bind_address(self) -> object:
        return self.raw.get("dashboard_bind_address") if self.ok else None

    def is_whitelisted(self, tool: str, pod_id: str) -> bool:
        """Is ``pod_id`` whitelisted for ``tool``, per the effective whitelist?

        Fails closed on every axis: a broken config, a missing tool entry, a
        malformed ``pods`` list, or a pod id absent from that list are all
        "not whitelisted".
        """
        entry = self.whitelist.get(tool)
        if not isinstance(entry, dict):
            return False
        pods = entry.get("pods")
        if not isinstance(pods, list):
            return False
        return pod_id in pods


def _fail(resolved: Path, message: str, remediation: str) -> Config:
    return Config(
        path=resolved,
        raw={},
        error=CliError(code=EXIT_ENV_ERROR, message=message, remediation=remediation),
    )


def load_config(*, path: str | Path | None = None, env: Mapping[str, str] | None = None) -> Config:
    """Load the effective config, failing closed on any problem.

    Never raises. A missing file, an unreadable file, or malformed/wrong-shape
    JSON each yield a :class:`Config` with an empty whitelist and a populated
    ``.error`` — a named :class:`CliError` — for a CLI caller to surface (raise
    it, or emit it via ``shabbos_goy.cli._output.emit_error``).
    """
    resolved = resolve_config_path(path=path, env=env)

    if not resolved.is_file():
        return _fail(
            resolved,
            f"config file not found: {resolved}",
            f"create a config file at {resolved} "
            "(see tests/fixtures/config.example.json for the expected shape)",
        )

    try:
        text = resolved.read_text(encoding="utf-8")
    except OSError as exc:
        return _fail(
            resolved,
            f"config file unreadable: {resolved} ({exc})",
            "check the file's permissions and encoding",
        )

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        return _fail(
            resolved,
            f"config file is not valid JSON: {resolved} ({exc})",
            "fix the JSON syntax, or restore from tests/fixtures/config.example.json",
        )

    if not isinstance(data, dict):
        return _fail(
            resolved,
            f"config file must contain a JSON object at its root: {resolved}",
            "see tests/fixtures/config.example.json for the expected shape",
        )

    return Config(path=resolved, raw=data, error=None)


def validate_ac_argument(action: str, value: str) -> None:
    """Validate an AC (air conditioner) tool-call argument.

    The only representable AC argument is ``power`` with value ``"on"`` or
    ``"off"``. Anything else — a different action name, a different value,
    wrong casing, a numeric target temperature, etc. — is a validation error:
    raises :class:`CliError` (user-input error, ``EXIT_USER_ERROR``).
    """
    if action != AC_ACTION or value not in ALLOWED_POWER_VALUES:
        raise CliError(
            code=EXIT_USER_ERROR,
            message=f"invalid AC argument: {action}={value!r}",
            remediation=(
                f"the only supported AC argument is {AC_ACTION}="
                f"{'|'.join(ALLOWED_POWER_VALUES)}"
            ),
        )

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
import re
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


def _fraction(value: object) -> float | None:
    """*value* as a float in ``[0, 1]``, or ``None`` if it is not one."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if 0.0 <= number <= 1.0 else None


def _positive_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


def _positive_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        return None
    return float(value)


def _node_name(value: object) -> str | None:
    """A PipeWire node name, or ``None``. Never an empty or option-shaped one."""
    if not isinstance(value, str):
        return None
    name = value.strip()
    if not name or name.startswith("-"):
        return None
    return name


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


# A `grant` secret name becomes an argv token (see actuators/sensibo.py).
_GRANT_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")


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

    def _block(self, key: str) -> dict:
        """The JSON object at ``key``, or ``{}``.

        Every dict-shaped accessor goes through here. ``load_config`` only
        checks that the document's *root* is an object, so any nested value
        can be a list, a string or a number — and a caller doing ``.get`` on
        that raises ``AttributeError``, which for the listener is a crash at
        construction time rather than a fail-closed start. An empty dict is
        the safe answer: every reader of these blocks already treats a
        missing key as "use the built-in default".
        """
        if not self.ok:
            return {}
        value = self.raw.get(key)
        return value if isinstance(value, dict) else {}

    @property
    def whitelist(self) -> dict:
        """The effective actuation whitelist: tool name -> {pods, actions}.

        This is *only* what the JSON says. There is no hardcoded fallback
        list — removing a tool key or a pod id from the config file is
        sufficient, by itself, to remove it here.
        """
        return self._block("whitelist")

    @property
    def location(self) -> dict:
        return self._block("location")

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
        return self._block("rate_limits")

    @property
    def strict_mode_delay_seconds(self) -> object:
        return self.raw.get("strict_mode_delay_seconds") if self.ok else None

    @property
    def volume(self) -> dict:
        return self._block("volume")

    @property
    def join_gap_ms(self) -> object:
        return self.raw.get("join_gap_ms") if self.ok else None

    @property
    def ring_sizes(self) -> dict:
        return self._block("ring_sizes")

    @property
    def dashboard_bind_address(self) -> object:
        return self.raw.get("dashboard_bind_address") if self.ok else None

    # -- typed accessors ---------------------------------------------------
    #
    # Everything below exists so no caller has to reach into ``.raw`` and
    # re-implement "is this the right type, and is it in range?". Each one
    # fails closed the same way the whitelist does: a broken config, a
    # missing key or a wrong-shaped value yields the safe default (``False``,
    # ``[]`` or ``None``), never a half-trusted value.

    @property
    def dashboard_allow_non_tailnet(self) -> bool:
        """The escape hatch that lets the dashboard bind a non-tailnet address.

        Only a literal JSON ``true`` widens the rule -- ``"yes"``, ``1`` and
        anything else read as "no", because this one is a security boundary
        (:mod:`shabbos_goy.web.bind`).
        """
        if not self.ok:
            return False
        return self.raw.get("dashboard_allow_non_tailnet") is True

    @property
    def dashboard_hostnames(self) -> list[str]:
        """Extra ``Host`` header values that name this dashboard."""
        if not self.ok:
            return []
        value = self.raw.get("dashboard_hostnames")
        if not isinstance(value, list):
            return []
        return [name for name in value if isinstance(name, str) and name]

    @property
    def min_confidence(self) -> float | None:
        """The configured minimum decider confidence, or ``None`` if unusable.

        ``None`` means "the caller's own default applies" --- the pipeline
        owns that default, so a nonsense value here never silently lowers the
        bar.
        """
        return _fraction(self.raw.get("min_confidence")) if self.ok else None

    @property
    def grant(self) -> dict:
        """The ``grant`` block: secret NAMES only, never secret values."""
        return self._block("grant")

    @property
    def grant_lobes_secret(self) -> str | None:
        """The ``grant`` secret NAME that holds the lobes gateway key, or ``None``.

        ``{"grant": {"lobes_api_key": "LOBES_GATEWAY_API_KEY"}}``: see
        :mod:`shabbos_goy.grant_inject`. Validated like the Sensibo one.
        """
        name = self.grant.get("lobes_api_key")
        if isinstance(name, str) and _GRANT_NAME_RE.match(name):
            return name
        return None

    @property
    def grant_sensibo_secret(self) -> str | None:
        """The ``grant`` secret NAME that holds the Sensibo key, or ``None``.

        ``{"grant": {"sensibo_api_key": "SENSIBO_API_KEY"}}`` asks the adapter to
        run ``sensibo`` through ``grant run --inject``. Only the name lives in
        config, never the key. The name becomes an argv token, so anything but
        a plain upper-case identifier is dropped here (and refused again in the
        adapter).
        """
        name = self.grant.get("sensibo_api_key")
        if isinstance(name, str) and _GRANT_NAME_RE.match(name):
            return name
        return None

    @property
    def context_window(self) -> dict:
        """The rolling context window's bounds block."""
        return self._block("context_window")

    @property
    def context_max_items(self) -> int | None:
        return _positive_int(self.context_window.get("max_items"))

    @property
    def context_max_age_seconds(self) -> float | None:
        return _positive_number(self.context_window.get("max_age_seconds"))

    @property
    def context_max_render_chars(self) -> int | None:
        return _positive_int(self.context_window.get("max_render_chars"))

    @property
    def audio(self) -> dict:
        """The PipeWire block: which nodes this agent captures and plays on."""
        return self._block("audio")

    @property
    def mic_node(self) -> str | None:
        return _node_name(self.audio.get("mic_node"))

    @property
    def speaker_node(self) -> str | None:
        return _node_name(self.audio.get("speaker_node"))

    @property
    def volume_node(self) -> str | None:
        """The node whose volume this agent owns; defaults to the speaker."""
        explicit = _node_name(self.audio.get("volume_node"))
        return explicit if explicit is not None else self.speaker_node

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

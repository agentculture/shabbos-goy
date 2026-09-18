"""Get the lobes gateway key from the operator's ``grant`` store, by re-exec.

The Sensibo key never needs to enter this process: ``sensibo`` is a child, and
:mod:`shabbos_goy.actuators.sensibo` wraps that child in ``grant run --inject``.
The lobes key is different. This process itself opens the realtime socket and
calls the model, so it has to hold the key. When a verb needs it, it is not in
the environment, and config names a grant secret
(``{"grant": {"lobes_api_key": "LOBES_GATEWAY_API_KEY"}}``), the process
replaces itself with::

    grant run --inject SHABBOS_GOY_LOBES_API_KEY=<NAME> -- python -m shabbos_goy <argv>

so the key exists only in the new process's environment: no env file, no shell
profile, nothing on disk. A guard variable stops a loop if grant runs but does
not supply the key. A missing ``grant`` binary is not an error here: the verb
carries on and reports the missing key in its own named way.
"""

from __future__ import annotations

import os
import sys
from typing import Callable, Mapping, Sequence

from .config import Config

GRANT_EXECUTABLE = "grant"
LOBES_KEY_ENV = "SHABBOS_GOY_LOBES_API_KEY"
_ALTERNATE_KEY_ENVS = ("GATEWAY_API_KEY",)
#: Set on the re-exec'd child so it never re-execs again.
GUARD_ENV = "SHABBOS_GOY_GRANT_REEXEC"


def ensure_lobes_key(
    config: Config,
    *,
    env: Mapping[str, str] | None = None,
    argv: Sequence[str] | None = None,
    execvpe: Callable[[str, list[str], dict[str, str]], object] = os.execvpe,
) -> bool:
    """Re-exec under ``grant`` if that is how the lobes key is supplied.

    Returns ``False`` when nothing was done (the key is present, no secret is
    named, the guard is set, or grant is not installed). On success the real
    ``os.execvpe`` does not return at all.
    """
    environ = os.environ if env is None else env
    if environ.get(LOBES_KEY_ENV) or any(environ.get(name) for name in _ALTERNATE_KEY_ENVS):
        return False
    name = config.grant_lobes_secret
    if name is None or environ.get(GUARD_ENV):
        return False
    args = list(sys.argv[1:] if argv is None else argv)
    command = [
        GRANT_EXECUTABLE,
        "run",
        "--inject",
        f"{LOBES_KEY_ENV}={name}",
        "--",
        sys.executable,
        "-m",
        "shabbos_goy",
        *args,
    ]
    child_env = dict(environ)
    child_env[GUARD_ENV] = "1"
    try:
        execvpe(GRANT_EXECUTABLE, command, child_env)  # nosec B606 - closed argv, no shell
    except OSError:
        return False
    return True

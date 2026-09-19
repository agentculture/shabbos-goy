"""The liveness heartbeat: three timestamps on tmpfs, and nothing else.

Compose's ``restart: unless-stopped`` reacts to a process exit, never to an
"unhealthy" healthcheck, so a healthcheck here is a *report*, not a
supervisor. Its job is to let an operator (and ``docker inspect``) tell a
listener that is working from one that is merely running: a wedged session
keeps its socket open and its process alive while nothing arrives.

Three activity kinds are tracked, and they are deliberately the three that
prove different things:

* ``pong`` --- the socket is alive (we answered the server's PING);
* ``audio`` --- we are still *sending* capture, so the mic feeder is alive;
* ``transcript`` --- the server is still *understanding* us.

The file holds timestamps and a window. It holds no transcript text, no
class, no intent, no key and no pod id --- the type itself is the guarantee,
the same trick :class:`shabbos_goy.pipeline.LogRecord` uses. It lives on
tmpfs (wherever ``SHABBOS_GOY_HEARTBEAT`` points, else a per-user runtime
directory), so it never survives a reboot and is not state the listener could
resume from (invariant #3): it is a report about the present, written for
something else to read.

Where it lives, and why not ``/tmp/shabbos-goy/heartbeat.json``
---------------------------------------------------------------
A fixed, predictable path in a world-writable directory is a symlink trap:
any other user on the host can pre-create it as a link and make this process
write through it. So the default is ``$XDG_RUNTIME_DIR/shabbos-goy/`` (a
per-user tmpfs, mode 0700) and, when that is unset, ``<tempdir>/shabbos-goy-
<uid>/`` --- and either way the directory is created 0700, **refused** if it
is a symlink or not owned by this uid, and the file is opened ``O_NOFOLLOW``
0600. An explicit path (``--heartbeat``, ``SHABBOS_GOY_HEARTBEAT``) still
wins: the container mounts its own tmpfs and names it.

Timestamps are **wall clock** (``time.time``), not monotonic, because the
reader is usually a different process.
"""

from __future__ import annotations

import json
import os
import stat
import tempfile
import time
from pathlib import Path
from typing import Callable, Mapping, Optional

__all__ = [
    "DEFAULT_HEARTBEAT_NAME",
    "DEFAULT_WINDOW_SECONDS",
    "ENV_HEARTBEAT_PATH",
    "ENV_XDG_RUNTIME_DIR",
    "KINDS",
    "Heartbeat",
    "UnsafeHeartbeatDirectory",
    "default_heartbeat_dir",
    "healthcheck",
    "heartbeat_path",
    "read_heartbeat",
]

#: Where the heartbeat file lives; a tmpfs path in the container.
ENV_HEARTBEAT_PATH = "SHABBOS_GOY_HEARTBEAT"

#: The per-user runtime directory, when the session manager provides one.
ENV_XDG_RUNTIME_DIR = "XDG_RUNTIME_DIR"

DEFAULT_HEARTBEAT_NAME = "heartbeat.json"

#: Owner-only, for both the directory and the file.
DIR_MODE = 0o700
FILE_MODE = 0o600


class UnsafeHeartbeatDirectory(OSError):
    """The heartbeat directory is a symlink, or is not owned by this user."""


#: How recent "recent activity" has to be. Generous on purpose: a quiet
#: household produces no transcripts for hours, so the window has to be wide
#: enough that silence alone is not unhealthy -- PING/PONG and the audio
#: feeder keep it fresh meanwhile.
DEFAULT_WINDOW_SECONDS = 120.0

#: The whole vocabulary. An unknown kind is ignored, never stored.
KINDS = ("pong", "audio", "transcript")


def default_heartbeat_dir(env: Optional[Mapping[str, str]] = None) -> Path:
    """The per-user directory the heartbeat defaults into.

    ``$XDG_RUNTIME_DIR/shabbos-goy`` when the session manager gives us one
    (already per-user, already tmpfs, already 0700), else
    ``<tempdir>/shabbos-goy-<uid>``. Never a name another user could predict
    *and* create first in a shared directory.
    """
    env = os.environ if env is None else env
    runtime_dir = (env.get(ENV_XDG_RUNTIME_DIR) or "").strip()
    if runtime_dir:
        return Path(runtime_dir) / "shabbos-goy"
    return Path(tempfile.gettempdir()) / f"shabbos-goy-{os.getuid()}"


def heartbeat_path(
    env: Optional[Mapping[str, str]] = None, override: Optional[str | Path] = None
) -> Path:
    """Where to write (or read) the heartbeat: explicit > env > per-user default."""
    if override is not None:
        return Path(override)
    env = os.environ if env is None else env
    configured = (env.get(ENV_HEARTBEAT_PATH) or "").strip()
    if configured:
        return Path(configured)
    return default_heartbeat_dir(env) / DEFAULT_HEARTBEAT_NAME


def _prepare_directory(directory: Path) -> Path:
    """Create *directory* 0700 and prove it is ours. Raises otherwise.

    The two refusals are the whole point: a **symlink** could redirect the
    write anywhere, and a directory owned by **another uid** is one somebody
    else can replace the file inside. Either way we would rather have no
    heartbeat than write through someone else's path.
    """
    directory.mkdir(mode=DIR_MODE, parents=True, exist_ok=True)
    info = os.lstat(directory)
    if stat.S_ISLNK(info.st_mode):
        raise UnsafeHeartbeatDirectory(f"{directory} is a symlink")
    if not stat.S_ISDIR(info.st_mode):
        raise UnsafeHeartbeatDirectory(f"{directory} is not a directory")
    if info.st_uid != os.getuid():
        raise UnsafeHeartbeatDirectory(f"{directory} is not owned by this user")
    return directory


class Heartbeat:
    """Marks activity in memory; writes a tiny JSON snapshot on demand.

    Marking is cheap and happens on hot paths (every audio chunk); writing
    touches the filesystem and is done by the listener's ticker, so a slow
    disk can never back up the reader thread.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        clock: Callable[[], float] = time.time,
        window_seconds: float = DEFAULT_WINDOW_SECONDS,
    ) -> None:
        self.path = Path(path)
        self._clock = clock
        self._window = float(window_seconds)
        self._last: dict[str, Optional[float]] = dict.fromkeys(KINDS)

    # -- marking -----------------------------------------------------------

    def mark(self, kind: str) -> None:
        """Record that ``kind`` just happened. An unknown kind is ignored."""
        if kind in self._last:
            self._last[kind] = self._clock()

    def snapshot(self) -> dict[str, Optional[float]]:
        return dict(self._last)

    def latest(self) -> Optional[float]:
        stamps = [value for value in self._last.values() if value is not None]
        return max(stamps) if stamps else None

    @property
    def window_seconds(self) -> float:
        return self._window

    # -- the file ----------------------------------------------------------

    def payload(self) -> dict:
        return {
            "written_at": self._clock(),
            "window_seconds": self._window,
            "last": self.snapshot(),
        }

    def write(self) -> bool:
        """Write the snapshot atomically, refusing an unsafe path. ``False`` on
        any OS error.

        A heartbeat that cannot be written must never take the listener down
        with it -- a full or read-only tmpfs, or a directory that turns out
        not to be ours, is an operator problem, not a reason to stop
        listening. The healthcheck will report ``missing``, which is true.

        The write itself is ``O_NOFOLLOW`` 0600 into a temporary file in the
        *same* directory, then :func:`os.replace` -- so a reader never sees a
        half-written file, and a symlink planted at either name is refused
        rather than followed.
        """
        payload = json.dumps(self.payload()).encode("utf-8")
        temporary = self.path.with_name(f"{self.path.name}.tmp")
        try:
            _prepare_directory(self.path.parent)
            flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW
            handle = os.open(temporary, flags, FILE_MODE)
            try:
                os.write(handle, payload)
            finally:
                os.close(handle)
            os.replace(temporary, self.path)
        except OSError:
            return False
        return True

    def fresh(self, now: Optional[float] = None) -> bool:
        latest = self.latest()
        if latest is None:
            return False
        moment = self._clock() if now is None else now
        return (moment - latest) <= self._window

    def health(self) -> dict:
        """The ``/healthz`` body: a verdict and a short reason code."""
        latest = self.latest()
        if latest is None:
            return {"ok": False, "reason": "no_activity"}
        if not self.fresh():
            return {"ok": False, "reason": "stale"}
        return {"ok": True, "reason": "ok"}

    def __repr__(self) -> str:  # counts and times only, never text
        return f"Heartbeat(path={self.path.name!r}, window={self._window:.0f}s)"

    __str__ = __repr__


def read_heartbeat(path: str | Path) -> Optional[dict]:
    """The heartbeat file as a dict, or ``None`` if it is absent or unusable."""
    try:
        raw = Path(path).read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def healthcheck(
    path: str | Path,
    *,
    window_seconds: Optional[float] = None,
    now: Optional[float] = None,
) -> tuple[bool, str]:
    """``(healthy, reason)`` from the heartbeat file. Never raises.

    This is what ``shabbos-goy listen --healthcheck`` (and therefore the
    container's ``HEALTHCHECK``) runs. Every failure is a named reason code:
    ``missing`` (no file), ``unreadable`` (corrupt), ``no_activity`` (written,
    but nothing has ever arrived) or ``stale``.
    """
    data = read_heartbeat(path)
    if data is None:
        return False, "missing" if not Path(path).exists() else "unreadable"
    last = data.get("last")
    if not isinstance(last, dict):
        return False, "unreadable"
    stamps = [
        float(value)
        for value in last.values()
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    ]
    if not stamps:
        return False, "no_activity"
    window = window_seconds
    if window is None:
        configured = data.get("window_seconds")
        window = (
            float(configured)
            if isinstance(configured, (int, float)) and not isinstance(configured, bool)
            else DEFAULT_WINDOW_SECONDS
        )
    moment = time.time() if now is None else now
    if (moment - max(stamps)) > window:
        return False, "stale"
    return True, "ok"

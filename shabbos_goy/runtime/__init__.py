"""The ambient runtime: the listener's threads, its heartbeat, its ears' state.

Three modules, deliberately separate:

* :mod:`shabbos_goy.runtime.listener` -- the loop itself: which threads run,
  the one bounded queue between them, the servers, and a clean shutdown.
* :mod:`shabbos_goy.runtime.heartbeat` -- the liveness file on tmpfs and the
  ``--healthcheck`` verdict the container reads.
* :mod:`shabbos_goy.runtime.connection` -- one word for where the lobes
  session currently is, for the dashboard. Text-free.

Nothing here decides anything. Every gate lives in
:mod:`shabbos_goy.pipeline`, :mod:`shabbos_goy.policy`,
:mod:`shabbos_goy.config` and :mod:`shabbos_goy.mode`; this package only
gives them a lifetime.
"""

from __future__ import annotations

from .connection import (
    STATE_AUTH_FAILED,
    STATE_CONNECTED,
    STATE_CONNECTING,
    STATE_IDLE,
    STATE_RECONNECTING,
    STATE_STALLED,
    ConnectionMonitor,
)
from .heartbeat import (  # noqa: F401 - re-exported for callers of this package
    DEFAULT_HEARTBEAT_NAME,
    DEFAULT_WINDOW_SECONDS,
    ENV_HEARTBEAT_PATH,
    Heartbeat,
    UnsafeHeartbeatDirectory,
    default_heartbeat_dir,
    healthcheck,
    heartbeat_path,
    read_heartbeat,
)
from .listener import (
    Listener,
    ListenerOptions,
    RuntimeNote,
    control_address_for,
    events_file_source,
    lobes_source,
    pipewire_audio_source,
    read_events_file,
    wav_audio_source,
)

__all__ = [
    "DEFAULT_HEARTBEAT_NAME",
    "DEFAULT_WINDOW_SECONDS",
    "ENV_HEARTBEAT_PATH",
    "STATE_AUTH_FAILED",
    "STATE_CONNECTED",
    "STATE_CONNECTING",
    "STATE_IDLE",
    "STATE_RECONNECTING",
    "STATE_STALLED",
    "ConnectionMonitor",
    "Heartbeat",
    "Listener",
    "ListenerOptions",
    "RuntimeNote",
    "UnsafeHeartbeatDirectory",
    "control_address_for",
    "default_heartbeat_dir",
    "events_file_source",
    "healthcheck",
    "heartbeat_path",
    "lobes_source",
    "read_heartbeat",
    "pipewire_audio_source",
    "read_events_file",
    "wav_audio_source",
]

"""Suite-wide hermeticity: a test never sees the host's config or secrets store.

Without this, a developer box that has a real ``~/.config/shabbos-goy/config.json``
naming ``grant`` secrets makes any verb that loads the default config re-exec the
pytest worker under ``grant`` (found the hard way), and makes results depend on
whoever ran the suite.
"""

from __future__ import annotations

import pytest

from shabbos_goy import grant_inject


@pytest.fixture(autouse=True)
def _hermetic_environment(tmp_path_factory, monkeypatch):
    # An empty config home: the default config path never resolves to a real file.
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path_factory.mktemp("xdg-config")))
    monkeypatch.delenv("SHABBOS_GOY_CONFIG", raising=False)
    # Never replace the test process, whatever a config says. Tests of the
    # re-exec itself pass an explicit ``env`` and an injected ``execvpe``.
    monkeypatch.setenv(grant_inject.GUARD_ENV, "1")

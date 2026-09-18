"""The LIVE golden run: the real model, on the box, behind the ``golden`` marker.

This module never runs in CI. It is excluded from the default test run by
``addopts = -m "not golden"`` in ``pyproject.toml``, and when it *is* selected
(``uv run pytest -m golden``) it skips with a named reason unless the
environment points at a real lobes gateway.

It is a thin wrapper around ``tests/golden/runner.py`` so that the numbers the
operator reads come from exactly the same scoring code CI exercises offline.
See ``tests/golden/README.md`` for how to run it.
"""

from __future__ import annotations

import os

import pytest

from tests.golden import runner as gr

pytestmark = pytest.mark.golden

_REASON = (
    "the live golden run needs a real lobes gateway: set SHABBOS_GOY_LOBES_URL "
    "(or SHABBOS_GOY_SENSES_URL) and SHABBOS_GOY_LOBES_API_KEY in the environment, "
    "and run it on the box (it is never run in CI)"
)


def _configured() -> bool:
    return bool(
        (os.environ.get(gr.ENV_URL) or "").strip()
        or (os.environ.get(gr.ENV_SENSES_URL) or "").strip()
    )


requires_gateway = pytest.mark.skipif(not _configured(), reason=_REASON)


@requires_gateway
def test_live_text_entrance_has_no_hard_false_positives():
    """Entrance 1: manifest text -> the real model -> policy."""
    assert gr.main(["--entrance", "text", "--mode", "strict"]) == gr.EXIT_OK


@requires_gateway
def test_live_audio_batch_entrance_has_no_hard_false_positives():
    """Entrance 2: TTS wav -> POST /v1/audio/transcriptions -> the real model."""
    assert gr.main(["--entrance", "audio-batch", "--mode", "strict"]) == gr.EXIT_OK


@requires_gateway
def test_live_audio_realtime_entrance_has_no_hard_false_positives():
    """Entrance 3: TTS wav -> the ears-only realtime session + joiner -> the model."""
    assert gr.main(["--entrance", "audio-realtime", "--mode", "strict"]) == gr.EXIT_OK

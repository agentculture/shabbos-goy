#!/usr/bin/env python3
"""Run the golden set (deviation d4) -- a thin entry point for the operator.

The whole implementation lives in ``tests/golden/runner.py`` so that the live
run on the box and the offline tests in CI share one scoring code path. See
``tests/golden/README.md``.

    python3 scripts/golden-set.py --entrance text --mode both
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tests.golden.runner import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())

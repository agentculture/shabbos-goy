"""Entry point for ``python -m shabbos_goy``."""

from __future__ import annotations

import sys

from shabbos_goy.cli import main

if __name__ == "__main__":
    sys.exit(main())

"""The golden set: the committed manifest, its runner, and the ASR cache.

Deviation d4. ``build_manifest.py`` builds ``manifest.jsonl``; ``runner.py``
runs it against the real model on the box (and, offline, against a replay or
the rule oracle). See ``README.md`` in this directory.
"""

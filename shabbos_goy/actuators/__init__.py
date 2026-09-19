"""Actuator backends: adapters between an approved intent and a mesh sibling CLI.

Every adapter here composes a sibling's CLI as a subprocess (never imports it,
never talks to its API directly) so this repo's classifier/gate/whitelist stay
the only place that decides whether an action happens at all.
"""

from __future__ import annotations

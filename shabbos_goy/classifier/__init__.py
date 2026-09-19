"""The classifier: which kind of utterance this is, and what it implies.

This is the product (CLAUDE.md, "The classifier is the product"). It decides
whether an utterance is a **direct or implied command** -- which must never
be acted on -- or an **indirect hint** the agent may act on, and what the
hint implies. It is rules and a lexicon only: no model, no network, no
third-party package, and no state carried between calls, so the same text
always classifies the same way and every verdict is reproducible in a test.

Usage::

    from shabbos_goy.classifier import classify
    from shabbos_goy.policy import may_act

    verdict = classify(text)
    if may_act(mode, verdict.klass):
        ...  # then the calendar gate, the whitelist, and --apply

The interface is deliberately narrow -- one function, ``str | None`` in, a
frozen :class:`Classification` out -- so a model-backed classifier could be
swapped in behind it later. What must not change with it: the class names
come from :data:`shabbos_goy.policy.CLASSES`, the gating decision stays in
:func:`shabbos_goy.policy.may_act`, and in doubt the class is ``unrelated``.
"""

from __future__ import annotations

from .rules import CLASSES, INTENTS, Classification, classify

__all__ = ["CLASSES", "INTENTS", "Classification", "classify"]

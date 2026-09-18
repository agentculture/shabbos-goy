"""The mode x utterance-class gate: the single source of truth for whether
shabbos-goy may act on a classified utterance.

Pure stdlib, no I/O. Every other module and both UIs (CLI + the ambient
listener) must call :func:`may_act` rather than re-deriving this table --
see the invariant in CLAUDE.md: a false positive on an imperative/request/
rebuke breaks Shabbat for the user, so this table is deliberately narrow and
fails closed on anything it does not recognise.
"""

from __future__ import annotations

CLASSES: tuple[str, ...] = (
    "imperative",
    "request",
    "rebuke",
    "remark",
    "wish",
    "discomfort",
    "unrelated",
)

MODES: tuple[str, ...] = (
    "weekday",
    "strict",
)

# Weekday mode acts on every class except "unrelated" (there is nothing to
# act on). Strict mode (Shabbat / Yom Kippur) narrows further: only the
# indirect-hint classes may trigger an action. Imperatives, requests and
# rebukes -- direct or implied commands -- never act in strict mode: that is
# the core invariant this repo exists to enforce.
_TABLE: dict[str, frozenset[str]] = {
    "weekday": frozenset({"imperative", "request", "rebuke", "remark", "wish", "discomfort"}),
    "strict": frozenset({"remark", "wish", "discomfort"}),
}


def may_act(mode: str | None, klass: str | None, *, clock_untrusted: bool = False) -> bool:
    """Return whether an utterance of ``klass`` may trigger an action in ``mode``.

    An unrecognised ``mode`` or ``klass`` never acts (fails closed). When
    ``clock_untrusted`` is set -- wall-clock time or location could not be
    verified, e.g. no NTP sync after boot -- the strict column is selected
    regardless of the nominal ``mode``, because the agent must never fail
    toward acting when it cannot trust what day or time it is.
    """
    effective_mode = "strict" if clock_untrusted else mode
    allowed = _TABLE.get(effective_mode)
    if allowed is None:
        return False
    if klass not in CLASSES:
        return False
    return klass in allowed

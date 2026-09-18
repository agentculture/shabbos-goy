"""Tests for the utterance-class x mode gate policy table.

Pure stdlib, no I/O: shabbos_goy.policy is the single source every other
module and both UIs call. Assert every cell of the mode x class matrix, plus
the unknown-class/unknown-mode/clock-untrusted-selects-strict rules.
"""

from __future__ import annotations

import itertools

import pytest

from shabbos_goy.policy import CLASSES, MODES, may_act

ACTIONABLE_WEEKDAY = {"imperative", "request", "rebuke", "remark", "wish", "discomfort"}
ACTIONABLE_STRICT = {"remark", "wish", "discomfort"}


def test_classes_and_modes_are_the_expected_sets() -> None:
    assert set(CLASSES) == {
        "imperative",
        "request",
        "rebuke",
        "remark",
        "wish",
        "discomfort",
        "unrelated",
    }
    assert set(MODES) == {"weekday", "strict"}


@pytest.mark.parametrize("klass", sorted(CLASSES))
def test_weekday_acts_on_all_but_unrelated(klass: str) -> None:
    expected = klass in ACTIONABLE_WEEKDAY
    assert may_act("weekday", klass) is expected


@pytest.mark.parametrize("klass", sorted(CLASSES))
def test_strict_acts_only_on_remark_wish_discomfort(klass: str) -> None:
    expected = klass in ACTIONABLE_STRICT
    assert may_act("strict", klass) is expected


def test_every_matrix_cell_is_covered() -> None:
    """Belt-and-suspenders: enumerate the full mode x class matrix explicitly."""
    expected = {
        ("weekday", "imperative"): True,
        ("weekday", "request"): True,
        ("weekday", "rebuke"): True,
        ("weekday", "remark"): True,
        ("weekday", "wish"): True,
        ("weekday", "discomfort"): True,
        ("weekday", "unrelated"): False,
        ("strict", "imperative"): False,
        ("strict", "request"): False,
        ("strict", "rebuke"): False,
        ("strict", "remark"): True,
        ("strict", "wish"): True,
        ("strict", "discomfort"): True,
        ("strict", "unrelated"): False,
    }
    assert set(expected) == set(itertools.product(MODES, CLASSES))
    for (mode, klass), expected_result in expected.items():
        assert may_act(mode, klass) is expected_result


def test_unknown_class_never_acts() -> None:
    assert may_act("weekday", "nonsense") is False
    assert may_act("strict", "") is False
    assert (
        may_act("weekday", "IMPERATIVE") is False
    )  # case-sensitive, not normalized to a known class


def test_unknown_mode_never_acts() -> None:
    assert may_act("holiday", "remark") is False
    assert may_act("", "wish") is False
    assert may_act(None, "wish") is False  # type: ignore[arg-type]


def test_clock_untrusted_flag_selects_strict_column() -> None:
    # When the clock cannot be trusted, behave as strict regardless of the
    # nominal mode requested -- never fail toward acting.
    assert may_act("weekday", "imperative", clock_untrusted=True) is False
    assert may_act("weekday", "remark", clock_untrusted=True) is True
    assert may_act("weekday", "unrelated", clock_untrusted=True) is False
    # strict stays strict either way
    assert may_act("strict", "remark", clock_untrusted=True) is True
    assert may_act("strict", "imperative", clock_untrusted=True) is False


def test_clock_untrusted_with_unknown_mode_still_never_acts_on_unknown_class() -> None:
    assert may_act("bogus-mode", "remark", clock_untrusted=True) is True
    assert may_act("bogus-mode", "bogus-class", clock_untrusted=True) is False

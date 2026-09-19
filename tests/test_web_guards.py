"""Repo guards the dashboard must not break.

The core invariant forbids a confirmation dialog: "Should I turn on the AC?"
followed by "yes" turns the exchange into a command. The cheapest mechanical
proxy for that, repo-wide, is that no Hebrew string the agent could ever
speak contains a question mark. UI text in English is fine -- an operator
reading a screen is not speaking to a listening box.
"""

from __future__ import annotations

import ast
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1] / "shabbos_goy"

#: The Hebrew block, plus Hebrew presentation forms.
HEBREW_RANGES = ((0x0590, 0x05FF), (0xFB1D, 0xFB4F))

QUESTION_MARKS = ("?", "؟")  # ASCII and Arabic question mark

#: Regex source is not speech. The rule lexicon
#: (``shabbos_goy/classifier/lexicon.py``) is full of Hebrew patterns whose
#: ``?`` is a quantifier, matched against what a person said and never spoken
#: back -- and per deviation d2 the rules are not even in the runtime. A
#: literal carrying any of these is a pattern, not a sentence.
REGEX_MARKERS = ("\\w", "\\d", "\\s", "(?", "[", "]", "{0,")


def _has_hebrew(text: str) -> bool:
    return any(any(low <= ord(ch) <= high for low, high in HEBREW_RANGES) for ch in text)


def _is_regex_source(text: str) -> bool:
    return any(marker in text for marker in REGEX_MARKERS)


def _is_spoken_question(text: str) -> bool:
    return (
        _has_hebrew(text)
        and not _is_regex_source(text)
        and any(mark in text for mark in QUESTION_MARKS)
    )


def _string_constants(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            yield node.lineno, node.value


def test_no_hebrew_string_literal_contains_a_question_mark() -> None:
    offenders = []
    for path in sorted(PACKAGE.rglob("*.py")):
        for lineno, value in _string_constants(path):
            if _is_spoken_question(value):
                offenders.append(f"{path.relative_to(PACKAGE.parent)}:{lineno}")
    assert offenders == [], f"Hebrew question marks (a confirmation prompt): {offenders}"


def test_the_guard_would_catch_a_planted_offender(tmp_path) -> None:
    """The guard is only worth having if it can fail."""
    planted = tmp_path / "planted.py"
    planted.write_text('SPEECH = "להדליק את המזגן?"\n', encoding="utf-8")
    found = [value for _lineno, value in _string_constants(planted) if _is_spoken_question(value)]
    assert found
    # ... and it still lets a Hebrew regex quantifier through.
    pattern = tmp_path / "pattern.py"
    pattern.write_text('PAT = r"ה?דל[יו]?ק\\w*"\n', encoding="utf-8")
    assert not [v for _l, v in _string_constants(pattern) if _is_spoken_question(v)]


def test_the_web_package_imports_only_the_standard_library() -> None:
    """``dependencies = []`` stays empty: the dashboard is http.server and
    json, nothing else."""
    import sys
    import sysconfig

    import shabbos_goy.web  # noqa: F401
    import shabbos_goy.web.bind  # noqa: F401
    import shabbos_goy.web.page  # noqa: F401
    import shabbos_goy.web.server  # noqa: F401

    stdlib = sysconfig.get_paths()["stdlib"]
    for name, module in list(sys.modules.items()):
        if not name.startswith("shabbos_goy.web"):
            continue
        for imported in getattr(module, "__dict__", {}).values():
            origin = getattr(getattr(imported, "__spec__", None), "origin", None)
            if not isinstance(origin, str) or origin in ("built-in", "frozen"):
                continue
            assert origin.startswith(stdlib) or "shabbos_goy" in origin, origin


def test_the_page_is_embedded_in_python_not_a_packaged_data_file() -> None:
    """Embedding the HTML in a module sidesteps every build-backend
    include question: there is no data file to forget to ship."""
    from shabbos_goy.web import page

    assert isinstance(page.DASHBOARD_HTML, str)
    assert not list((PACKAGE / "web").glob("*.html"))
    assert not list((PACKAGE / "web").rglob("*.css"))
    assert not list((PACKAGE / "web").rglob("*.js"))

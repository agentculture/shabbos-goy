"""Every parser path (verb or noun) must have an explain catalog entry.

Walks the *built* argparse parser (not a hand-maintained list) so a new verb
or noun that forgets its ``explain`` entry fails this test, not a human
review. Also guards the domain rewrite: the catalog root and ``learn`` text
must no longer describe "a clonable template" — they must describe the
Shabbat/Yom Kippur domain.
"""

from __future__ import annotations

import argparse

from shabbos_goy.cli import _build_parser, main
from shabbos_goy.explain.catalog import ENTRIES


def _walk(parser: argparse.ArgumentParser, prefix: tuple[str, ...]) -> list[tuple[str, ...]]:
    paths: list[tuple[str, ...]] = []
    for action in parser._actions:  # noqa: SLF001 - the only way to walk argparse's tree
        if isinstance(action, argparse._SubParsersAction):  # noqa: SLF001
            for name, subparser in action.choices.items():
                path = prefix + (name,)
                paths.append(path)
                paths.extend(_walk(subparser, path))
    return paths


def _all_command_paths() -> list[tuple[str, ...]]:
    parser = _build_parser()
    return _walk(parser, ())


def test_every_parser_path_has_a_catalog_entry() -> None:
    paths = _all_command_paths()
    assert paths, "the parser walk found nothing -- the walker itself is broken"
    missing = [p for p in paths if p not in ENTRIES]
    assert missing == [], f"missing explain catalog entries for: {missing}"


def test_every_catalog_path_is_still_resolvable(capsys) -> None:
    # Belt-and-braces: every registered path must also resolve through the
    # real `explain` verb (this already exists in tests/test_cli.py for the
    # OLD set of paths; re-run it here over the full, current parser walk).
    for path in _all_command_paths():
        rc = main(["explain", *path])
        assert rc == 0, f"explain {' '.join(path)} failed"
        capsys.readouterr()


def test_new_nouns_expose_an_overview_subcommand() -> None:
    # Rubric contract: any noun with action-verbs must also expose `overview`.
    parser = _build_parser()
    for action in parser._actions:  # noqa: SLF001
        if not isinstance(action, argparse._SubParsersAction):  # noqa: SLF001
            continue
        for noun in ("ac", "volume", "mode"):
            sub = action.choices[noun]
            nested = [
                a
                for a in sub._actions  # noqa: SLF001
                if isinstance(a, argparse._SubParsersAction)  # noqa: SLF001
            ]
            assert nested, f"{noun!r} has no nested subparsers"
            assert "overview" in nested[0].choices


def test_root_and_learn_describe_the_domain_not_the_template() -> None:
    root = ENTRIES[()]
    assert "clonable template" not in root
    assert "Shabbat" in root or "shabbat" in root.lower()

    from shabbos_goy.cli._commands.learn import _TEXT

    assert "clonable template" not in _TEXT
    assert "Shabbat" in _TEXT


def test_the_root_parser_description_is_not_the_template_either() -> None:
    from shabbos_goy.cli import _build_parser

    description = _build_parser().description or ""
    assert "clonable template" not in description
    assert "Shabbat" in description

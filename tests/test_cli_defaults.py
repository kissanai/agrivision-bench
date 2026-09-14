"""The quickstart must work on a clean clone with no flags.

`agribench run --adapter echo --limit 20` relies on the CLI's default manifest
path. When suites were reorganised, that default silently pointed at a
directory that no longer existed and the first command in the README failed.
This pins the default to a file that ships.
"""

from __future__ import annotations

from pathlib import Path

from agribench import cli

ROOT = Path(__file__).resolve().parents[1]


def test_default_manifest_ships() -> None:
    assert (ROOT / cli.DEFAULT_MANIFEST).is_file(), (
        f"cli.DEFAULT_MANIFEST={cli.DEFAULT_MANIFEST} does not exist in the repository"
    )


def test_default_manifest_is_a_diagnosis_suite() -> None:
    assert cli.DEFAULT_MANIFEST.parts[:3] == ("tracks", "diagnosis", "suites")

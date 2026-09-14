"""Documentation must not describe suites that no longer exist.

The openfield-v1 suite shrank once, when cases no system could win were
removed, and every headline count in the docs silently became wrong. A
benchmark whose own README misstates its size is not one anyone should trust,
so the counts are pinned here and drift fails the build.

This guard now covers every shipped suite, not just the first one, because the
Diagnosis track publishes three of them and the README quotes all three.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SUITES_DIR = ROOT / "tracks" / "diagnosis" / "suites"


def _cases(suite: str) -> list[dict]:
    path = SUITES_DIR / suite / "manifest.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


SUITES = {name: _cases(name) for name in ("field_v1", "clean_v1", "openfield_v1")}
COUNTS = {name: len(cases) for name, cases in SUITES.items()}

#: The two suites the published Dhenu Vision 1.0 figures were measured on.
#: Their pooled size is quoted outside this repository, so it is pinned here.
POOLED = COUNTS["field_v1"] + COUNTS["clean_v1"]

DOCS = [
    ROOT / "README.md",
    ROOT / "AGENTS.md",
    ROOT / "docs" / "METRICS.md",
    ROOT / "docs" / "LIMITATIONS.md",
]

#: Counts that belonged to earlier drafts. If one reappears in prose, a doc was
#: written against a stale manifest.
SUPERSEDED = (564, 282)


@pytest.mark.parametrize("doc", DOCS, ids=lambda p: p.name)
def test_no_superseded_case_counts_survive(doc: Path) -> None:
    found = [n for n in SUPERSEDED if re.search(rf"\b{n}\b", doc.read_text())]
    assert not found, f"{doc.name} still cites {found}; suites are {COUNTS}"


@pytest.mark.parametrize("suite", sorted(COUNTS), ids=str)
def test_readme_states_each_suite_size(suite: str) -> None:
    text = (ROOT / "README.md").read_text()
    assert re.search(rf"\b{COUNTS[suite]}\b", text), (
        f"README omits the {suite} case count ({COUNTS[suite]})"
    )


def test_readme_states_the_pooled_size() -> None:
    text = (ROOT / "README.md").read_text()
    assert re.search(rf"\b{POOLED}\b", text), (
        f"README omits the pooled field_v1+clean_v1 size ({POOLED}), which is "
        "the denominator quoted in published Dhenu Vision 1.0 figures"
    )


def test_openfield_shape_is_what_the_docs_describe() -> None:
    """The invariants the docs lean on: two images per condition, crop-only."""
    cases = SUITES["openfield_v1"]
    per_condition: dict[tuple[str, str], int] = {}
    for case in cases:
        key = (case["entity"], case["issue"])
        per_condition[key] = per_condition.get(key, 0) + 1
    assert set(per_condition.values()) == {2}, "docs claim exactly 2 images per condition"
    assert len(cases) == len(per_condition) * 2
    assert len({c["entity"] for c in cases}) == 79

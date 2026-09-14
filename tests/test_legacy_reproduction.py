"""Historical results must stay auditable, and the current rules must stay fair.

Two things are pinned here, and they deliberately disagree:

* :mod:`agribench.legacy` reproduces the pre-1.0 published numbers exactly,
  asymmetries included, so past claims can always be checked.
* :mod:`agribench.contract.metrics` applies one rule to every system, which is
  the only basis on which a leaderboard can rank systems against each other.

The fixture holds nothing but scored booleans — no images, no prompts, no model
outputs — so these tests run anywhere without proprietary data.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agribench.contract.metrics import compute_metrics
from agribench.legacy import LEGACY_DIFFERENCES, compute_metrics_legacy

FIXTURE = json.loads(
    (Path(__file__).parent / "golden" / "legacy_v02_published.json").read_text()
)
SYSTEMS = FIXTURE["systems"]
METRICS = ("precision", "coverage", "wrong_pct", "subject_accuracy", "issue_class_accuracy")


@pytest.mark.parametrize("system", SYSTEMS, ids=[s["name"] for s in SYSTEMS])
def test_legacy_formulas_reproduce_every_published_cell(system: dict) -> None:
    """All 11 published systems, every metric, to full float precision."""
    got = compute_metrics_legacy(system["rows"], system_type=system["system_type"])
    for key, expected in system["expected"].items():
        assert got[key] == pytest.approx(expected, abs=1e-12), (
            f"{system['name']} / {key}: expected {expected}, got {got[key]}"
        )


def test_legacy_rejects_an_unknown_system_type() -> None:
    with pytest.raises(ValueError, match="system_type"):
        compute_metrics_legacy([], system_type="pipeline")


def test_current_rules_use_one_denominator_for_every_system() -> None:
    """The asymmetry the legacy formulas carry must be gone from the current ones.

    Under the legacy rules an abstaining system's exact-issue score was divided
    by the cases it chose to answer, while every system it was ranked against
    was divided by all valid cases. A system could raise that column simply by
    declining more. This test fails if that behaviour ever returns.
    """
    abstaining = next(s for s in SYSTEMS if s["system_type"] == "system")
    rows = abstaining["rows"]

    current = compute_metrics(rows)
    exact_hits = sum(1 for row in rows if row["issue_class_ok"])

    assert current["issue_class_accuracy"] == pytest.approx(exact_hits / len(rows))
    # And it genuinely differs from what the old rule reported.
    legacy = compute_metrics_legacy(rows, system_type="system")
    assert legacy["issue_class_accuracy"] != pytest.approx(
        current["issue_class_accuracy"]
    )


def test_declining_never_counts_as_a_wrong_answer() -> None:
    """Abstention costs coverage; it must not also be charged as wrongness."""
    rows = [
        {"answered": True, "issue_cat_ok": True, "issue_class_ok": True, "subject_ok": True},
        {"answered": False, "issue_cat_ok": False, "issue_class_ok": False, "subject_ok": True},
    ]
    metrics = compute_metrics(rows)
    assert metrics["answered"] == 1
    assert metrics["coverage"] == pytest.approx(0.5)
    assert metrics["precision"] == pytest.approx(1.0)
    assert metrics["wrong_pct"] == pytest.approx(0.0)


def test_the_two_headline_identities_hold(
) -> None:
    """precision*coverage and wrong_pct must reconstruct the whole picture."""
    for system in SYSTEMS:
        m = compute_metrics(system["rows"])
        if not m["answered"]:
            continue
        assert m["precision"] * m["coverage"] + m["wrong_pct"] == pytest.approx(
            m["coverage"], abs=1e-12
        ), system["name"]


def test_the_differences_are_documented() -> None:
    assert set(LEGACY_DIFFERENCES) == {"issue_class_accuracy", "wrong_pct"}

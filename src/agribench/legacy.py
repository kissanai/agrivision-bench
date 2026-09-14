"""Bit-exact reproduction of the pre-1.0 (v0.2-era) metric formulas.

WHY THIS MODULE EXISTS
======================
The historical comparison builder that produced the v0.2 result tables used
**different denominators depending on the kind of system being scored**. Two
columns are affected:

``issue_class_accuracy``
    single-call systems were divided by *valid cases*; abstaining systems were
    divided by *answered cases*. On a run that answers half the set, that alone
    roughly doubles the reported number. Comparing the two in one column is
    apples-to-oranges, and it flatters the abstaining system.

``wrong_pct``
    single-call systems were charged for every case they got wrong *including
    ones they never answered*; abstaining systems were charged only for cases
    they answered and got wrong. The second rule is the defensible one — a
    declined case is not a wrong answer — but the historical code applied it
    only to abstaining systems.

:mod:`agribench.contract.metrics` deliberately does **not** reproduce those
asymmetries. It applies one rule to every system, which is the only basis on
which a public leaderboard can rank systems against each other.

This module exists so that historical numbers remain auditable: given the same
inputs it reproduces the old published values exactly, asymmetries included.
Use it to verify past results, never to produce new ones.

Every function here is read-only, stdlib-only, and takes already-scored rows.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

__all__ = ["LEGACY_DIFFERENCES", "compute_metrics_legacy"]

#: Plain-English record of where :func:`compute_metrics_legacy` and the current
#: :func:`agribench.contract.metrics.compute_metrics` disagree, and why.
LEGACY_DIFFERENCES: dict[str, str] = {
    "issue_class_accuracy": (
        "legacy divided abstaining systems by answered cases and single-call "
        "systems by valid cases; current divides every system by valid cases"
    ),
    "wrong_pct": (
        "legacy charged single-call systems for unanswered cases; current "
        "charges only cases that were answered and wrong, for every system"
    ),
}


def _answered(row: Mapping[str, Any], system_type: str) -> bool:
    """Reproduce the historical answered/committed test.

    Abstaining systems carried an explicit flag. Single-call systems were
    judged by ``primary_category``: a missing or ``"unknown"`` category meant
    the model declined. Note that an *empty string* category counted as
    answered historically, which is why this does not reuse the current
    ``DECLINED_CATEGORIES`` set.
    """
    for key in ("answered", "committed"):
        if key in row:
            return bool(row[key])
    if system_type == "system":
        return False
    return row.get("primary_category") not in (None, "unknown")


def compute_metrics_legacy(
    rows: Iterable[Mapping[str, Any]],
    *,
    system_type: str,
) -> dict[str, Any]:
    """Recompute the v0.2 published metrics, asymmetries preserved.

    Args:
        rows: scored rows, one per case. Reads ``error``/``errored``,
            ``answered``/``committed``, ``primary_category``, ``subject_ok``,
            ``issue_class_ok`` and ``issue_cat_ok``.
        system_type: ``"system"`` for a system that may decline, or
            ``"single_call"`` for one that answers every case. This argument
            changes the arithmetic — that is precisely the asymmetry being
            reproduced.

    Returns:
        The published key set: ``n``, ``errors``, ``answered``, ``precision``,
        ``coverage``, ``wrong_pct``, ``subject_accuracy``,
        ``issue_class_accuracy``. Ratios are ``None`` on a zero denominator.

    Raises:
        ValueError: if ``system_type`` is not one of the two known values.
    """
    if system_type not in ("system", "single_call"):
        raise ValueError(
            f"system_type must be 'system' or 'single_call', got {system_type!r}"
        )

    valid = [
        row
        for row in rows
        if not (row.get("error") or row.get("errored"))
    ]
    total = len(valid)
    answered = [row for row in valid if _answered(row, system_type)]

    def ratio(numerator: int, denominator: int) -> float | None:
        return numerator / denominator if denominator else None

    correct_answered = sum(1 for row in answered if row.get("issue_cat_ok"))

    if system_type == "system":
        # Charged only for answered-and-wrong; exact issue over answered.
        wrong = sum(1 for row in answered if not row.get("issue_cat_ok"))
        exact = ratio(
            sum(1 for row in answered if row.get("issue_class_ok")),
            len(answered),
        )
    else:
        # Charged for every valid case that is not category-correct.
        wrong = sum(1 for row in valid if not row.get("issue_cat_ok"))
        exact = ratio(
            sum(1 for row in valid if row.get("issue_class_ok")), total
        )

    return {
        "n": total,
        "errors": 0,
        "answered": len(answered),
        "precision": ratio(correct_answered, len(answered)),
        "coverage": ratio(len(answered), total),
        "wrong_pct": ratio(wrong, total),
        "subject_accuracy": ratio(
            sum(1 for row in valid if row.get("subject_ok")), total
        ),
        "issue_class_accuracy": exact,
    }

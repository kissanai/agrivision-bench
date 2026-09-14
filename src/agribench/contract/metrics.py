"""Frozen metric definitions for the openfield diagnosis benchmark.

FROZEN ZONE. These formulas define every published number. Changing one
changes what "precision" means and silently invalidates comparability with
previously published results.

The unit of measurement is a :class:`CaseScore`: one bench case, already
scored, reduced to the four booleans the metrics depend on. Producing a
``CaseScore`` from a run file is the caller's job (see ``agribench.record``);
this module never touches disk, never imports anything outside the standard
library, and holds no notion of who produced the answers.

Definitions
-----------
``valid_cases``
    cases that ran without a harness/provider error. Errored cases are never
    scored as wrong; they are excluded from every denominator and reported
    separately as ``errored_cases``.
``answered``
    valid cases where the system returned a usable diagnosis.
``precision``
    ``correct_category_among_answered / answered``
``coverage``
    ``answered / valid_cases``
``wrong_pct``
    ``(answered - correct_category_among_answered) / valid_cases``
``subject_accuracy``
    ``subject_ok / valid_cases``
``issue_class_accuracy``
    ``exact_issue_ok / valid_cases``

THE TWO-DENOMINATOR RULE
------------------------
Two different denominators are in play above, and conflating them is the
easiest way to misread a result table::

    precision            -> divided by ANSWERED
    coverage             -> divided by VALID CASES
    wrong_pct            -> divided by VALID CASES
    subject_accuracy     -> divided by VALID CASES
    issue_class_accuracy -> divided by VALID CASES

Only ``precision`` uses the answered denominator. Everything else uses valid
cases, which is why the others stay comparable across systems that answer at
different rates and ``precision`` does not.

Stated plainly: **precision alone is close to meaningless.** A system that
answers 10 of 500 cases and gets all 10 right reports 100% precision at 2%
coverage. A system that answers all 500 and gets 400 right reports 80%
precision at 100% coverage. The second is far more useful; the first has the
better-looking headline number. This is exactly why an abstaining system can
show high precision at low coverage, and why precision and coverage must
always be read as a pair.

``wrong_pct`` exists to keep that trade honest: it is the share of ALL valid
cases where the system answered and was wrong. Because its denominator is
valid cases, abstaining genuinely lowers it — a declined case is never charged
as a wrong answer — but abstaining earns nothing, because coverage falls in
lockstep. Two identities recover the whole picture from the reported fields::

    precision * coverage             == correct answers / valid cases
    precision * coverage + wrong_pct == coverage

Every ratio is ``None`` (not ``0.0``) when its denominator is zero, so an
empty run is never reported as a perfect or a failed one, and no division here
can raise ``ZeroDivisionError``.

Two entry points aggregate the same arithmetic, and there is deliberately only
one implementation of the formulas beneath them: :func:`compute`, which takes
:class:`CaseScore` objects and returns a rich :class:`Metrics`, and
:func:`compute_metrics`, which takes plain mapping rows and returns the
compact published key set.
"""

from __future__ import annotations

from collections.abc import Hashable, Iterable
from dataclasses import dataclass
from typing import Any

__all__ = [
    "DECLINED_CATEGORIES",
    "METRIC_DEFINITIONS",
    "METRIC_KEYS",
    "CaseScore",
    "Metrics",
    "compute",
    "compute_by_group",
    "compute_metrics",
]

#: Values of ``primary_category`` that mean the system declined to diagnose.
#: Used only to derive ``answered`` for a row that carries no explicit flag.
DECLINED_CATEGORIES = frozenset({"", "unknown"})

#: Human-readable formulas, emitted verbatim into reports so a published
#: artifact always carries the definition of the numbers it shows.
METRIC_DEFINITIONS: dict[str, str] = {
    "answered": "cases where the system returned a usable diagnosis",
    "precision": "correct_category_among_answered / answered",
    "coverage": "answered / valid_cases",
    "wrong_pct": "(answered - correct_category_among_answered) / valid_cases",
    "subject_accuracy": "subject_ok / valid_cases",
    "issue_class_accuracy": "exact_issue_ok / valid_cases",
    "valid_cases": "cases that ran without a harness or provider error",
}

#: The ratio metrics, in the order reports and leaderboards present them.
METRIC_KEYS: tuple[str, ...] = (
    "precision",
    "coverage",
    "wrong_pct",
    "subject_accuracy",
    "issue_class_accuracy",
)


@dataclass(frozen=True, slots=True)
class CaseScore:
    """One scored bench case, reduced to what the metrics need.

    Attributes
    ----------
    answered:
        the system returned a usable diagnosis for this case.
    subject_ok:
        the predicted subject matched the ground-truth entity.
    issue_class_ok:
        the predicted issue matched the exact ground-truth issue.
    issue_cat_ok:
        the predicted issue was at least in the right issue family
        (implied by ``issue_class_ok``).
    errored:
        the case never produced a scoreable answer. Errored cases are
        excluded from every denominator.
    """

    answered: bool = False
    subject_ok: bool = False
    issue_class_ok: bool = False
    issue_cat_ok: bool = False
    errored: bool = False


@dataclass(frozen=True, slots=True)
class Metrics:
    """Counts and ratios for one set of cases."""

    total_cases: int
    valid_cases: int
    errored_cases: int
    answered: int
    correct_category: int
    exact_issue: int
    subject_correct: int
    precision: float | None
    coverage: float | None
    wrong_pct: float | None
    subject_accuracy: float | None
    issue_class_accuracy: float | None

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-ready mapping in a stable key order."""
        return {
            "total_cases": self.total_cases,
            "valid_cases": self.valid_cases,
            "errored_cases": self.errored_cases,
            "answered": self.answered,
            "correct_category": self.correct_category,
            "exact_issue": self.exact_issue,
            "subject_correct": self.subject_correct,
            "precision": self.precision,
            "coverage": self.coverage,
            "wrong_pct": self.wrong_pct,
            "subject_accuracy": self.subject_accuracy,
            "issue_class_accuracy": self.issue_class_accuracy,
        }


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def compute(scores: Iterable[CaseScore]) -> Metrics:
    """Aggregate ``scores`` into :class:`Metrics`.

    Errored cases are counted and then dropped: they inflate no denominator
    and are never charged as wrong answers.
    """
    total = 0
    errored = 0
    valid = 0
    answered = 0
    correct_category = 0
    exact_issue = 0
    subject_correct = 0

    for score in scores:
        total += 1
        if score.errored:
            errored += 1
            continue
        valid += 1
        if score.subject_ok:
            subject_correct += 1
        if score.issue_class_ok:
            exact_issue += 1
        if score.answered:
            answered += 1
            if score.issue_cat_ok:
                correct_category += 1

    return Metrics(
        total_cases=total,
        valid_cases=valid,
        errored_cases=errored,
        answered=answered,
        correct_category=correct_category,
        exact_issue=exact_issue,
        subject_correct=subject_correct,
        precision=_ratio(correct_category, answered),
        coverage=_ratio(answered, valid),
        wrong_pct=_ratio(answered - correct_category, valid),
        subject_accuracy=_ratio(subject_correct, valid),
        issue_class_accuracy=_ratio(exact_issue, valid),
    )


def compute_by_group(
    items: Iterable[tuple[Hashable, CaseScore]],
) -> dict[Any, Metrics]:
    """Aggregate ``(group_key, score)`` pairs into per-group metrics.

    Groups come back sorted by key so reports are byte-stable across runs.
    """
    buckets: dict[Any, list[CaseScore]] = {}
    for key, score in items:
        buckets.setdefault(key, []).append(score)
    return {key: compute(buckets[key]) for key in sorted(buckets, key=str)}


def _row_answered(row: Any) -> bool:
    """Whether a mapping row records a usable diagnosis.

    Prefers an explicit ``answered`` flag, which is how a ``system`` run
    records that it stands behind its output. When that key is absent the
    verdict is derived from the schema response identically for every system:
    a ``primary_category`` that is missing, empty, or ``"unknown"`` means the
    system declined.
    """
    if "answered" in row:
        return bool(row["answered"])
    category = row.get("primary_category")
    if category is None:
        return False
    return str(category).strip().lower() not in DECLINED_CATEGORIES


def _as_case_score(row: Any) -> CaseScore:
    """Adapt one mapping row (or a passthrough ``CaseScore``) for scoring."""
    if isinstance(row, CaseScore):
        return row
    return CaseScore(
        answered=_row_answered(row),
        subject_ok=bool(row.get("subject_ok")),
        issue_class_ok=bool(row.get("issue_class_ok")),
        issue_cat_ok=bool(
            row.get("issue_cat_ok", row.get("issue_category_ok"))
        ),
        errored=bool(row.get("error", row.get("errored"))),
    )


def compute_metrics(rows: Iterable[Any]) -> dict[str, Any]:
    """Aggregate scored rows into the compact published key set.

    The mapping-shaped counterpart to :func:`compute`, for callers holding
    decoded run rows rather than :class:`CaseScore` objects. It delegates the
    arithmetic to :func:`compute` — the formulas exist in exactly one place —
    and projects the result onto the keys the published tables use.

    Args:
        rows: Any iterable of mappings (or :class:`CaseScore` objects), one
            per bench case. Recognised mapping keys, all optional and all read
            by truthiness:

            ``error`` / ``errored``
                the case produced nothing scoreable. Excluded from ``n``,
                counted in ``errors``, never charged as a wrong answer.
            ``answered``
                the system returned a usable diagnosis. Derived from
                ``primary_category`` when absent.
            ``issue_cat_ok`` / ``issue_category_ok``
                the issue family was right — the second element of
                :func:`~agribench.contract.scoring.score_issue`.
            ``issue_class_ok``
                the exact issue class was right — the first element.
            ``subject_ok``
                the subject was right —
                :func:`~agribench.contract.scoring.score_subject`.

    Returns:
        A dict with exactly these keys::

            n                     valid cases (scoreable rows)
            errors                rows excluded as unscoreable
            answered              valid cases with a usable diagnosis
            precision             correct_category_among_answered / answered
            coverage              answered / valid_cases
            wrong_pct             (answered - correct_category_among_answered)
                                  / valid_cases
            subject_accuracy      subject_ok / valid_cases
            issue_class_accuracy  exact_issue_ok / valid_cases

    ``issue_cat_ok`` is counted only on answered rows: a system cannot bank
    credit for a case it declined. ``subject_ok`` and ``issue_class_ok`` are
    counted across all valid rows, answered or not, because their denominator
    is valid cases.

    Ratios are ``None`` when their denominator is zero. In particular
    ``precision`` is ``None`` when nothing was answered — an abstain-everything
    run has no precision to report, and emitting ``0.0`` or ``1.0`` there would
    both be false.
    """
    result = compute(_as_case_score(row) for row in rows)
    return {
        "n": result.valid_cases,
        "errors": result.errored_cases,
        "answered": result.answered,
        "precision": result.precision,
        "coverage": result.coverage,
        "wrong_pct": result.wrong_pct,
        "subject_accuracy": result.subject_accuracy,
        "issue_class_accuracy": result.issue_class_accuracy,
    }

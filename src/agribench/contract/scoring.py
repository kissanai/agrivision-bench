"""Frozen scoring rules for the diagnosis benchmark.

FROZEN ZONE. Together with ``prompt.py``, ``schema.py``, and ``matching.py``
this module defines what "correct" means for every published number. Changing
any comparison here silently rescores history, so treat edits the way you would
treat editing the dataset: cut a new benchmark version instead.

Stdlib only -- no third-party imports on the scoring path, ever. The only
internal dependency is :mod:`agribench.contract.matching`, from which this
module imports exactly three names: ``family_of``, ``matches``, and ``tokens``.

What each function decides
--------------------------
``gt_category``   the reference issue family for a case, for reporting.
``score_subject`` did the system name the right crop/animal/weed?
``score_issue``   did the system name the right issue class, and failing that,
                  at least the right issue family?

How these feed the reported metrics
-----------------------------------
A case is *answered* when the diagnosis system returned a usable diagnosis. For
a ``single_call`` run that is any parseable response; for a ``system`` run it is
a response the system chose to stand behind. Provider/transport errors are not
answered cases and are never scored as wrong -- they are excluded from
``valid_cases`` entirely. On top of that denominator::

    precision            = correct_category_among_answered / answered
    coverage             = answered / valid_cases
    wrong_pct            = (answered - correct_category_among_answered) / valid_cases
    subject_accuracy     = subject_ok / valid_cases
    issue_class_accuracy = exact_issue_ok / valid_cases

``correct_category_among_answered`` counts the second element of
:func:`score_issue`; ``exact_issue_ok`` counts the first; ``subject_ok`` counts
:func:`score_subject`.

Disclosed matcher quirks
------------------------
The matcher is deliberately generous, and generosity has edges. Both of the
following are known, load-bearing behaviours that the published numbers already
contain. They are documented rather than fixed, because fixing them would move
every historical result.

1. **An empty predicted subject scores as correct.** :func:`score_subject`
   tests ``predicted in expected``, and the empty string is a substring of
   every string, so a model that omits ``subject`` (or returns ``""``/``None``)
   is credited with a subject hit. ``subject_accuracy`` is therefore an upper
   bound for any model that sometimes leaves the field blank. Runners should
   report the blank-subject rate alongside it.

2. **Ground truth can tokenize to nothing, and then nothing can match it.**
   :func:`~agribench.contract.matching.tokens` drops tokens of two characters
   or fewer, drops tokens equal to the crop name passed as ``crop``, and drops
   a small stop list that includes agronomically real words -- notably
   ``"rice"``, plus ``"disease"``, ``"leaf"``, ``"plant"``, and ``"spot"``. If
   every token of the ground-truth label is removed,
   :func:`~agribench.contract.matching.matches` short-circuits to ``False`` for
   *any* prediction, including a perfect one. A ground-truth issue of ``"rice"``
   is unmatchable; so is ``"leaf spot"``, whose every token is on the stop list.
   The same short-circuit applies when the *prediction* tokenizes to nothing.
   Because :func:`score_issue` passes ``gt_entity`` as ``crop``, an issue label
   made only of the crop name is likewise unmatchable. Cases like this depress
   ``issue_class_accuracy`` uniformly across models -- the comparison stays
   fair, the absolute number is pessimistic.

Note also that the category fallback in :func:`score_issue` is gated on
``family_of(gt_issue) != "unknown"``: when the reference label maps to no known
family, only an exact class match can score, and the "at least the family" path
is unavailable.
"""

from __future__ import annotations

from .matching import family_of, matches, tokens

__all__ = ["gt_category", "score_subject", "score_issue"]


def gt_category(issue: str) -> str:
    """Return the reference issue family for a ground-truth label.

    Thin wrapper over :func:`~agribench.contract.matching.family_of` that
    renders an unresolvable family as ``"?"`` so report tables show an explicit
    hole rather than a plausible-looking category.

    Args:
        issue: The ground-truth issue label for the case.

    Returns:
        One of ``"disease"``, ``"pest"``, ``"weed"``, ``"nutrient_deficiency"``,
        ``"healthy"``, or ``"?"`` when the label maps to no known family.
    """
    result = family_of(issue)
    return result if result != "unknown" else "?"


def score_subject(gt_entity: str, subject: str) -> bool:
    """Apply the benchmark's deliberately generous subject matcher.

    A prediction counts as correct if *any* of four tests pass: the shared
    label matcher accepts it, the expected string is contained in the
    prediction, the prediction is contained in the expected string, or the two
    share at least one content token. The intent is that ``"tomato plant"``,
    ``"tomato"``, and ``"Solanum lycopersicum tomato"`` all score against a
    ground truth of ``tomato``, since naming conventions differ across the
    source datasets and the benchmark is not testing botanical phrasing.

    Args:
        gt_entity: Ground-truth entity for the case (``entity`` in the
            manifest). Underscores are treated as spaces for the matcher pass.
        subject: The ``subject`` field the system returned. ``None`` and
            missing values are coerced to ``""``.

    Returns:
        ``True`` if the subject is accepted.

    Quirk (disclosed): an empty or missing ``subject`` scores ``True``, because
    ``predicted in expected`` is satisfied by the empty string for every
    ground truth. See the module docstring. Do not "fix" this in place --
    it is baked into published ``subject_accuracy`` figures.
    """
    predicted = (subject or "").lower()
    expected = gt_entity.lower()
    return bool(
        matches(expected.replace("_", " "), predicted)
        or expected in predicted
        or predicted in expected
        or (tokens(expected) & tokens(predicted))
    )


def score_issue(
    gt_issue: str,
    gt_entity: str,
    issue: str,
    category: str,
) -> tuple[bool, bool]:
    """Return exact-class and at-least-category correctness.

    Two independent judgements are returned together:

    * **class_ok** -- the predicted issue name matches the ground-truth issue
      name under the shared label matcher, with ``gt_entity`` supplied as the
      crop so that repeating the crop name earns no credit (a prediction of
      ``"tomato"`` cannot match a ground truth of ``"tomato late blight"`` on
      the strength of the word ``tomato``).
    * **category_ok** -- the returned ``primary_category`` equals the family
      the ground-truth issue maps to. An exact class match implies category
      correctness, so the second element is ``class_ok or category_ok``.

    Args:
        gt_issue: Ground-truth issue label for the case.
        gt_entity: Ground-truth entity, passed to the matcher as the crop term
            to suppress. Case-sensitive as given; the matcher lowercases.
        issue: The ``primary_issue`` the system returned. ``None`` is coerced
            to ``""``.
        category: The ``primary_category`` the system returned. ``None`` is
            coerced to ``""`` and the value is lowercased before comparison.

    Returns:
        ``(class_ok, category_ok)`` where the first element drives
        ``issue_class_accuracy`` and the second drives ``precision`` and
        ``wrong_pct``.

    Quirks (disclosed):

    * The category comparison is exact string equality against
      ``family_of(gt_issue)``. No normalisation is applied to the model's
      category, so a synonym such as ``"insect"`` for ``"pest"`` does **not**
      score, even though the response schema permits the model to emit
      ``insect`` as a ``subject_type``. Category normalisation is deliberately
      not part of this scoring path.
    * When ``family_of(gt_issue)`` is ``"unknown"``, ``category_ok`` can only
      become ``True`` via an exact class match -- the fallback is disabled
      rather than granted.
    * If ``gt_issue`` tokenizes to the empty set (stop words such as ``rice``,
      short tokens, or tokens equal to ``gt_entity``), ``class_ok`` is ``False``
      for every possible prediction. See the module docstring.
    """
    class_ok = matches(gt_issue, issue or "", gt_entity)
    expected_family = family_of(gt_issue)
    category_ok = bool(
        expected_family != "unknown"
        and expected_family == (category or "").lower()
    )
    return class_ok, class_ok or category_ok

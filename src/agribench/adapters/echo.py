"""A deterministic offline adapter. No network, no key, no model.

This is the day-one path. Clone the repo, run::

    python -m agribench run --limit 20

and a complete scored run lands in ``runs/`` before you have signed up for
anything. It is also what the test suite runs against, because a benchmark
whose own tests need an API key is a benchmark nobody can contribute to.

What it does
------------
Nothing intelligent. For each case it hashes ``(seed, case_id, image_sha256)``
and uses the digest to pick a subject and an issue out of a small fixed
vocabulary. Identical inputs always produce identical output -- same case, same
seed, same prediction, on any machine, forever. That is the entire point: the
scoring path can be tested end to end with byte-exact expectations.

Read the numbers it produces as noise, not as a baseline. The vocabulary is a
handful of common labels, so it lands a few cases by coincidence; the resulting
precision is a property of the label distribution, not of anything that could
be called a model. It is a floor for "the plumbing works", not a floor for
"a system is useful".

Knobs worth knowing
-------------------
``answer_rate``
    Fraction of cases answered. The rest come back as abstentions, so you can
    watch coverage fall while precision holds -- the central trade this
    benchmark exists to measure -- without spending a cent.
``fail_rate``
    Fraction of cases that raise :class:`~agribench.adapters.base.AdapterError`.
    Exercises the other path that matters: errors are excluded from the
    valid-case denominator rather than scored as wrong. Set it non-zero to
    prove your reporting pipeline does not silently absorb an outage.

Both draws are deterministic functions of the case id, not random, so a run
with ``fail_rate=0.1`` fails the *same* cases every time.
"""

from __future__ import annotations

import hashlib
import time
from typing import Any

from agribench.adapters.base import (
    Adapter,
    AdapterConfigError,
    Case,
    Prediction,
    TransientAdapterError,
)

__all__ = ["EchoAdapter"]


#: (subject, subject_type) pairs. Ordinary field subjects, nothing exotic.
SUBJECTS: tuple[tuple[str, str], ...] = (
    ("rice", "crop"),
    ("wheat", "crop"),
    ("maize", "crop"),
    ("cotton", "crop"),
    ("tomato", "crop"),
    ("potato", "crop"),
    ("banana", "crop"),
    ("grape", "crop"),
    ("chilli", "crop"),
    ("soybean", "crop"),
    ("sugarcane", "crop"),
    ("mango", "crop"),
    ("cattle", "animal"),
    ("goat", "animal"),
    ("poultry", "animal"),
)

#: (primary_issue, primary_category) pairs spanning every scorable family.
ISSUES: tuple[tuple[str, str], ...] = (
    ("leaf blight", "disease"),
    ("powdery mildew", "disease"),
    ("anthracnose", "disease"),
    ("bacterial leaf streak", "disease"),
    ("rust", "disease"),
    ("mosaic virus", "disease"),
    ("aphids", "pest"),
    ("fall armyworm", "pest"),
    ("whitefly", "pest"),
    ("stem borer", "pest"),
    ("nitrogen deficiency", "nutrient_deficiency"),
    ("potassium deficiency", "nutrient_deficiency"),
    ("healthy", "healthy"),
    ("water stress", "abiotic"),
    ("broadleaf weed pressure", "weed"),
)


class EchoAdapter(Adapter):
    """Deterministic offline stand-in for a diagnosis system.

    Args:
        seed: Salt for the hash. Change it to get a different -- but equally
            reproducible -- assignment of labels to cases.
        answer_rate: Fraction of cases answered, in ``[0.0, 1.0]``. ``1.0``
            answers everything; ``0.0`` abstains on everything, which is the
            degenerate 100%-precision-at-0%-coverage run worth seeing once.
        fail_rate: Fraction of cases that raise a transient adapter error, in
            ``[0.0, 1.0]``. Drawn independently of ``answer_rate`` and applied
            first.
        latency_ms: Artificial per-case delay in milliseconds. Useful for
            eyeballing thread-pool behaviour; leave at ``0`` for tests.
        system_type: Reported run type. Defaults to ``"single_call"``; set
            ``"system"`` when using the echo adapter to stand in for a pipeline
            in a plumbing test.

    Raises:
        AdapterConfigError: If a rate is outside ``[0.0, 1.0]``.
    """

    name = "echo"
    system_type = "single_call"

    def __init__(
        self,
        *,
        seed: str = "agribench",
        answer_rate: float = 1.0,
        fail_rate: float = 0.0,
        latency_ms: float = 0.0,
        system_type: str | None = None,
    ) -> None:
        self.seed = str(seed)
        self.answer_rate = _rate("answer_rate", answer_rate)
        self.fail_rate = _rate("fail_rate", fail_rate)
        self.latency_ms = max(0.0, float(latency_ms))
        if system_type is not None:
            self.system_type = str(system_type)

    # -- the plug ----------------------------------------------------------

    def predict(self, case: Case) -> Prediction:
        """Return the deterministic prediction for ``case``.

        Pure apart from the optional sleep: no file is read, no socket is
        opened. The image on disk is never even touched, which is why this
        works before the dataset has been fetched.
        """
        if self.latency_ms:
            time.sleep(self.latency_ms / 1000.0)

        if self.fail_rate and self._unit(case, "fail") < self.fail_rate:
            raise TransientAdapterError(
                f"simulated transport failure for case {case.case_id} "
                f"(fail_rate={self.fail_rate})"
            )

        subject, subject_type = SUBJECTS[self._index(case, "subject", len(SUBJECTS))]
        issue, category = ISSUES[self._index(case, "issue", len(ISSUES))]

        if self._unit(case, "answer") >= self.answer_rate:
            # Declined -- but still report the subject. subject_accuracy is
            # measured over all valid cases, so dropping it here would
            # under-report a system that names the crop and declines the
            # diagnosis, which is a perfectly reasonable thing to do.
            return Prediction.abstain(subject=subject, subject_type=subject_type)

        secondary: list[dict[str, str]] = []
        if self._unit(case, "secondary") < 0.25:
            extra_issue, extra_category = ISSUES[
                self._index(case, "secondary-pick", len(ISSUES))
            ]
            if extra_issue != issue:
                secondary = [{"issue": extra_issue, "category": extra_category}]

        return Prediction(
            answered=True,
            subject=subject,
            subject_type=subject_type,
            primary_issue=issue,
            primary_category=category,
            secondary_issues=secondary,
        )

    def describe(self) -> dict[str, Any]:
        return {
            "adapter": self.name,
            "system_type": self.system_type,
            "seed": self.seed,
            "answer_rate": self.answer_rate,
            "fail_rate": self.fail_rate,
            "offline": True,
            "note": "deterministic fixture, not a baseline",
        }

    # -- deterministic draws ----------------------------------------------

    def _digest(self, case: Case, salt: str) -> bytes:
        key = f"{self.seed}|{salt}|{case.case_id}|{case.image_sha256}"
        return hashlib.sha256(key.encode("utf-8")).digest()

    def _unit(self, case: Case, salt: str) -> float:
        """A stable pseudo-uniform draw in ``[0, 1)`` for this case and salt."""
        return int.from_bytes(self._digest(case, salt)[:8], "big") / 2.0**64

    def _index(self, case: Case, salt: str, size: int) -> int:
        """A stable index into a sequence of length ``size``."""
        return int.from_bytes(self._digest(case, salt)[8:16], "big") % size


def _rate(label: str, value: Any) -> float:
    try:
        rate = float(value)
    except (TypeError, ValueError) as exc:
        raise AdapterConfigError(f"{label} must be a number, got {value!r}") from exc
    if not 0.0 <= rate <= 1.0:
        raise AdapterConfigError(f"{label} must be between 0.0 and 1.0, got {rate}")
    return rate

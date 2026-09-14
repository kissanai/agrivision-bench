"""The frozen benchmark contract.

Four things together define the task, and all four are pinned by
``contract.lock.json``:

* ``PROMPT`` (``prompt.py``) -- the exact instruction every system receives.
* ``SCHEMA`` (``schema.py``) -- the exact structured response it must return.
* the matcher (``matching.py``, ``scoring.py``) -- how a predicted label is
  judged against ground truth.
* the metrics (``metrics.py``) -- how per-case judgements become published
  rates.

Every system in a results table must have been run under the same
``CONTRACT_VERSION``. If a pinned byte changes, numbers from before and after
are measuring different tasks and must not share a table;
``python -m agribench.contract.verify`` enforces that in CI.

The whole scoring path is stdlib only, so any published result can be
reproduced from a bare Python install with no dependency resolution.

Importing this package has no side effects: no file reads, no network, no
logging setup, no mutable global state.

Two denominators are in play throughout — ``precision`` is over answered
cases, every other rate is over valid cases. See :mod:`agribench.contract.metrics`.
"""

from __future__ import annotations

from .matching import family_of, matches, tokens
from .metrics import compute_metrics
from .prompt import PROMPT
from .schema import SCHEMA
from .scoring import gt_category, score_issue, score_subject

#: Bumped only when a pinned byte of the contract intentionally changes.
#: Must equal ``contract_version`` in ``contract.lock.json``; ``verify.py``
#: checks the two against each other.
CONTRACT_VERSION = "1.0"

__all__ = [
    "CONTRACT_VERSION",
    "PROMPT",
    "SCHEMA",
    "compute_metrics",
    "family_of",
    "gt_category",
    "matches",
    "score_issue",
    "score_subject",
    "tokens",
]

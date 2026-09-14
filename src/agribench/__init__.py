"""agribench -- an open benchmark for agricultural image diagnosis systems.

Two things make a benchmark trustworthy: everyone answers the same question,
and nobody can quietly change the ruler. Both are enforced here rather than
promised.

The frozen contract (:mod:`agribench.contract`) pins the prompt, the response
schema, the label matcher, and the metric formulas by sha256. The adapter layer
(:mod:`agribench.adapters`) is the only place a specific system appears, so any
system -- a hosted model, a local server, somebody's private pipeline -- plugs
in without touching anything that decides what counts as correct.

Start here, with no credentials and no dataset::

    python -m agribench run --limit 20

The headline metric is precision *among answered cases*, reported beside
coverage, because a system that declines what it does not know is behaving
correctly and a benchmark that punishes that is measuring the wrong thing. The
same property makes precision alone gameable, which is why no table in this
repository ever prints it on its own.

The scoring path is stdlib-only. A published number must be reproducible from
a bare CPython install.
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "0.1.0"

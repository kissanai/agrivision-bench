"""Entry point for ``python -m agribench``.

Kept trivial on purpose: everything lives in :mod:`agribench.cli`, which is
also what the ``agribench`` console script calls, so the two invocations cannot
drift apart.
"""

from __future__ import annotations

from agribench.cli import main

if __name__ == "__main__":
    raise SystemExit(main())

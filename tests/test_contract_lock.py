"""The frozen zone must not drift.

Every published result is only comparable to a future one if the prompt, the
schema, the matcher and the metric formulas are byte-identical between the two
runs. These tests are what make ``contract/verify.py``'s promise true: a single
changed byte in the frozen zone fails the build.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from agribench.contract import prompt as prompt_mod

CONTRACT_DIR = Path(prompt_mod.__file__).parent
LOCK = json.loads((CONTRACT_DIR / "contract.lock.json").read_text())

# The task prompt as sent to every system since the v0.2 runs. Pinned as a
# literal, not recomputed, so an edit to prompt.py cannot quietly move it.
EXPECTED_PROMPT_SHA256 = (
    "bc0b0f252f45d34f57a0dbef75e8f9933696513a58ed76ac8eee3984179af603"
)
EXPECTED_PROMPT_LEN = 552


def test_prompt_is_byte_identical_to_the_published_runs() -> None:
    digest = hashlib.sha256(prompt_mod.PROMPT.encode("utf-8")).hexdigest()
    assert len(prompt_mod.PROMPT) == EXPECTED_PROMPT_LEN
    assert digest == EXPECTED_PROMPT_SHA256


def test_lock_agrees_with_the_pinned_prompt() -> None:
    assert LOCK["prompt_sha256"] == EXPECTED_PROMPT_SHA256
    assert LOCK["prompt_len"] == EXPECTED_PROMPT_LEN


def test_every_frozen_file_matches_its_recorded_hash() -> None:
    drifted = []
    for name, expected in LOCK["files"].items():
        actual = hashlib.sha256((CONTRACT_DIR / name).read_bytes()).hexdigest()
        if actual != expected:
            drifted.append(f"{name}: locked {expected[:12]} != actual {actual[:12]}")
    assert not drifted, "frozen zone changed without a lock bump:\n" + "\n".join(drifted)


def test_matcher_records_the_upstream_it_was_forked_from() -> None:
    """The fork's provenance must stay auditable.

    The matcher is forked, not vendored: an unused production-only helper was
    removed. The hash it was derived from is recorded so anyone can verify the
    scoring logic descends from the code that produced the published numbers.
    """
    assert LOCK["matching_derived_from_sha256"]
    assert LOCK["matching_sha256"] != LOCK["matching_derived_from_sha256"]
    assert "category_norm" in LOCK["matching_fork_note"]


def test_scoring_path_imports_nothing_outside_the_standard_library() -> None:
    """Scoring must never depend on a third-party package.

    A benchmark that cannot be scored without installing something is a
    benchmark most people will not reproduce.
    """
    source = (CONTRACT_DIR / "scoring.py").read_text()
    for banned in ("import numpy", "import pandas", "dhenu_pipeline", "requests"):
        assert banned not in source, f"scoring.py must not import {banned}"


def test_production_only_helper_is_absent_from_the_matcher() -> None:
    source = (CONTRACT_DIR / "matching.py").read_text()
    assert "category_norm" not in source

"""The shipped suite must stay winnable, licensed, and free of image bytes.

A benchmark case that no system can ever score is not a hard case — it is a
broken one, and it silently depresses one column for every entrant equally.
These tests keep such cases out of openfield_v1.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agribench.contract.matching import family_of, matches

SUITE = Path(__file__).resolve().parents[1] / "tracks" / "diagnosis" / "suites" / "openfield_v1"
CASES = [json.loads(line) for line in (SUITE / "manifest.jsonl").read_text().splitlines() if line.strip()]

#: Only licences that permit redistribution and commercial use.
ALLOWED_LICENCES = {"CC0", "CC-BY", "MIT", "Apache-2.0"}


def test_suite_is_large_enough_to_be_meaningful() -> None:
    assert len(CASES) >= 500


def test_every_case_carries_the_required_fields() -> None:
    required = {
        "case_id", "image", "image_sha256", "entity",
        "issue", "issue_family", "license", "source",
    }
    for case in CASES:
        missing = required - case.keys()
        assert not missing, f"{case.get('case_id')} missing {sorted(missing)}"


def test_case_ids_and_images_are_unique() -> None:
    ids = [c["case_id"] for c in CASES]
    hashes = [c["image_sha256"] for c in CASES]
    assert len(set(ids)) == len(ids)
    assert len(set(hashes)) == len(hashes), "the same image appears twice"


def test_every_image_is_redistributable() -> None:
    """No noncommercial, private, or unresolved-licence image may ship."""
    offenders = [
        (c["case_id"], c["license"])
        for c in CASES
        if c["license"] not in ALLOWED_LICENCES
    ]
    assert not offenders, f"non-redistributable licences present: {offenders[:5]}"


def test_no_case_is_unwinnable_by_construction() -> None:
    """Ground truth must be able to match itself under the frozen matcher.

    A label whose every token is a stop word ("Raspberry leaf spot" -> no
    tokens) can never be scored correct by anyone.
    """
    unwinnable = [
        c["case_id"] for c in CASES if not matches(c["issue"], c["issue"], c["entity"])
    ]
    assert not unwinnable, f"ground truth cannot self-match: {unwinnable[:5]}"


def test_declared_family_agrees_with_the_frozen_matcher() -> None:
    """Otherwise a system answering the declared family is scored wrong."""
    conflicts = [
        (c["case_id"], c["issue"], c["issue_family"], family_of(c["issue"]))
        for c in CASES
        if family_of(c["issue"]) != "unknown"
        and family_of(c["issue"]) != c["issue_family"]
    ]
    assert not conflicts, f"family conflicts: {conflicts[:5]}"


def test_scope_is_crop_disease_and_pest_only() -> None:
    assert {c["issue_family"] for c in CASES} == {"disease", "pest"}


def test_manifest_paths_are_relative_and_content_addressed() -> None:
    for case in CASES:
        assert not case["image"].startswith("/"), case["case_id"]
        assert ".." not in case["image"], case["case_id"]
        assert case["image_sha256"] in case["image"], case["case_id"]


def test_no_image_bytes_are_committed() -> None:
    """The repo ships a manifest, not a redistribution of third-party images."""
    binaries = [
        p.name
        for p in SUITE.rglob("*")
        if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}
    ]
    assert not binaries, f"image binaries committed: {binaries[:5]}"


def test_attribution_covers_every_case() -> None:
    """CC-BY legally requires attribution; a missing row is a licence breach."""
    attribution = (SUITE / "ATTRIBUTION.csv").read_text()
    for case in CASES:
        assert case["image_sha256"] in attribution, case["case_id"]


@pytest.mark.parametrize("field", ["entity", "issue"])
def test_labels_are_free_text_not_internal_codes(field: str) -> None:
    """Ground truth must not leak an internal class-code scheme."""
    for case in CASES:
        assert not case[field].startswith(case["entity"] + "_"), case["case_id"]

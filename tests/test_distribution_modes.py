"""The three distribution modes, and the rules that keep each one honest.

A case declares how its image bytes reach the person running the benchmark.
Getting this wrong is not a cosmetic defect: calling a case ``bundled`` asserts
a redistribution right, and asserting one nobody granted is the single most
expensive mistake this repository could ship.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agribench.errors import ManifestError
from agribench.manifest import DISTRIBUTION_VALUES, parse_manifest

ROOT = Path(__file__).resolve().parents[1]
SUITES = ROOT / "tracks" / "diagnosis" / "suites"

BASE = {
    "case_id": "t-0001",
    "entity": "rice_paddy",
    "image": "images/" + "a" * 64 + ".jpg",
    "image_sha256": "a" * 64,
    "issue": "Sheath Blight",
    "issue_family": "disease",
    "license": "CC-BY",
    "source": "Example",
}


def _parse(**overrides):
    row = {**BASE, **overrides}
    return parse_manifest([json.dumps(row)], source="test")


def test_the_three_modes_are_the_whole_vocabulary() -> None:
    assert DISTRIBUTION_VALUES == {"bundled", "link-only", "source-only"}


def test_bundled_requires_a_redistributable_licence() -> None:
    with pytest.raises(ManifestError, match="does not permit redistribution"):
        _parse(distribution="bundled", license="research-eval-only")


def test_link_only_requires_a_url() -> None:
    with pytest.raises(ManifestError, match="must carry an image_url"):
        _parse(distribution="link-only", license="all-rights-reserved")


def test_link_only_url_must_be_http() -> None:
    with pytest.raises(ManifestError, match="must be http or https"):
        _parse(distribution="link-only", license="x", image_url="ftp://example.org/a.jpg")


def test_source_only_accepts_a_licence_that_cannot_be_redistributed() -> None:
    """The point of the mode: record the licence, claim nothing."""
    cases = _parse(distribution="source-only", license="unknown")
    assert cases[0].license == "unknown"


def test_source_only_still_requires_a_digest() -> None:
    """We hold the bytes; we just may not ship them. An unpinned source-only
    case would be unidentifiable inside whatever archive its publisher ships,
    so only link-only is exempt from the digest requirement."""
    row = {**BASE, "distribution": "source-only"}
    del row["image_sha256"]
    with pytest.raises(ManifestError, match="missing required field.*image_sha256"):
        parse_manifest([json.dumps(row)], source="test")


def test_an_unknown_mode_is_rejected() -> None:
    with pytest.raises(ManifestError):
        _parse(distribution="mirrored")


@pytest.mark.parametrize("suite", ["field_v1", "clean_v1", "openfield_v1"])
def test_every_shipped_suite_passes_the_strict_loader(suite: str) -> None:
    """Regression: field_v1 and clean_v1 both used to claim `bundled` under
    licences that grant no redistribution right, and the strict loader rejected
    them -- while the CLI, which used a different loader, reported them OK."""
    text = (SUITES / suite / "manifest.jsonl").read_text()
    cases = parse_manifest(text.splitlines(), source=suite)
    assert cases


def test_field_v1_is_fetchable_end_to_end() -> None:
    """Every case must carry a URL, or `agribench fetch` silently skips it."""
    text = (SUITES / "field_v1" / "manifest.jsonl").read_text()
    rows = [json.loads(line) for line in text.splitlines() if line.strip()]
    assert all(r.get("distribution") == "link-only" for r in rows)
    missing = [r["case_id"] for r in rows if not str(r.get("image_url") or "").startswith("https://")]
    assert not missing, f"{len(missing)} field_v1 cases have no https image_url"


def test_clean_v1_declares_no_redistribution_right() -> None:
    """Its licences are mixed and partly unknown, so no case may claim bundled."""
    text = (SUITES / "clean_v1" / "manifest.jsonl").read_text()
    rows = [json.loads(line) for line in text.splitlines() if line.strip()]
    assert all(r.get("distribution") == "source-only" for r in rows)

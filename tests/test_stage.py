"""Staging must match by content and refuse anything it cannot verify.

The repository ships a manifest rather than the images, so staging is the step
that turns a checkout into a runnable suite. Two properties matter: a source
tree that renamed or reorganised the files still works, and a file whose bytes
do not match its recorded digest is never silently benchmarked.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from agribench.datasets.stage import audit_images, sha256_file, stage_images


def _write(path: Path, payload: bytes) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return hashlib.sha256(payload).hexdigest()


@pytest.fixture()
def suite(tmp_path: Path) -> dict:
    """A two-case suite whose images sit, renamed, in a nested source tree."""
    source = tmp_path / "source" / "nested" / "deep"
    digest_a = _write(source / "totally_unrelated_name.jpg", b"image-alpha")
    digest_b = _write(source / "IMG_9999.jpg", b"image-beta")
    cases = [
        {"case_id": "c-0", "image": f"images/{digest_a}.jpg", "image_sha256": digest_a},
        {"case_id": "c-1", "image": f"images/{digest_b}.jpg", "image_sha256": digest_b},
    ]
    return {
        "cases": cases,
        "source": tmp_path / "source",
        "images": tmp_path / "bench" / "images",
        "digests": (digest_a, digest_b),
    }


def test_images_are_matched_by_content_not_filename(suite: dict) -> None:
    result = stage_images(suite["cases"], suite["source"], suite["images"])
    assert result.staged == 2
    assert result.complete
    for digest in suite["digests"]:
        assert (suite["images"] / f"{digest}.jpg").exists()


def test_staging_is_idempotent(suite: dict) -> None:
    stage_images(suite["cases"], suite["source"], suite["images"])
    again = stage_images(suite["cases"], suite["source"], suite["images"])
    assert again.staged == 0
    assert again.already_present == 2
    assert again.complete


def test_missing_images_are_reported_not_ignored(suite: dict) -> None:
    absent = hashlib.sha256(b"never-existed").hexdigest()
    cases = suite["cases"] + [
        {"case_id": "c-2", "image": f"images/{absent}.jpg", "image_sha256": absent}
    ]
    result = stage_images(cases, suite["source"], suite["images"])
    assert result.missing == [absent]
    assert not result.complete


def test_corrupted_file_is_caught_by_a_deep_audit(suite: dict) -> None:
    stage_images(suite["cases"], suite["source"], suite["images"])
    victim = suite["images"] / f"{suite['digests'][0]}.jpg"
    victim.write_bytes(b"different bytes entirely")

    shallow = audit_images(suite["cases"], suite["images"])
    assert shallow.complete, "a shallow audit only checks presence"

    deep = audit_images(suite["cases"], suite["images"], deep=True)
    assert not deep.complete
    assert len(deep.corrupt) == 1


def test_audit_reports_an_unstaged_suite_as_incomplete(suite: dict) -> None:
    result = audit_images(suite["cases"], suite["images"])
    assert len(result.missing) == 2
    assert not result.complete


def test_sha256_file_matches_hashlib(tmp_path: Path) -> None:
    payload = b"x" * (1 << 20) + b"tail"
    path = tmp_path / "big.bin"
    path.write_bytes(payload)
    assert sha256_file(path) == hashlib.sha256(payload).hexdigest()


def test_staging_never_copies_an_unwanted_file(suite: dict) -> None:
    """A source tree full of other images must not pollute the suite."""
    _write(suite["source"] / "decoy.jpg", b"not part of the suite")
    stage_images(suite["cases"], suite["source"], suite["images"])
    assert sorted(p.name for p in suite["images"].iterdir()) == sorted(
        f"{d}.jpg" for d in suite["digests"]
    )

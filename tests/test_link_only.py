"""Link-only cases must be usable without ever being redistributable.

A link-only case exists because the most realistic field photographs are
usually the ones nobody has licensed for redistribution. The benchmark carries
the reference and the ground truth; the operator downloads the bytes from the
origin. Three properties make that safe, and each is pinned here:

* a case whose licence does not permit redistribution cannot be marked bundled;
* a link-only case must carry a usable ``http(s)`` URL;
* the bytes never enter the repository.

The fetch tests run against a loopback HTTP server, never the public internet.
"""

from __future__ import annotations

import functools
import hashlib
import http.server
import json
import socketserver
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from agribench.datasets.fetch import fetch_images, pin_manifest
from agribench.errors import ManifestError
from agribench.manifest import parse_manifest

BUNDLED = {
    "case_id": "b1",
    "entity": "rice_paddy",
    "image": "images/" + "a" * 64 + ".jpg",
    "image_sha256": "a" * 64,
    "issue": "Sheath Blight",
    "issue_family": "disease",
    "license": "CC0",
    "source": "Example",
}

LINKED = {
    "case_id": "l1",
    "entity": "cotton",
    "image": "images/l1.jpg",
    "issue": "American Bollworm",
    "issue_family": "pest",
    "license": "all-rights-reserved",
    "source": "Extension",
    "distribution": "link-only",
    "image_url": "https://example.org/bollworm.jpg",
}


def _parse(row: dict) -> object:
    return parse_manifest([json.dumps(row)])[0]


# -- schema rules ----------------------------------------------------------


def test_link_only_case_may_be_authored_unpinned() -> None:
    """You cannot know a digest for a file you have not downloaded yet."""
    case = _parse(LINKED)
    assert case.image_sha256 == ""


def test_link_only_case_accepts_a_digest_once_pinned() -> None:
    case = _parse({**LINKED, "image_sha256": "b" * 64})
    assert case.image_sha256 == "b" * 64


def test_link_only_case_must_carry_a_url() -> None:
    row = {k: v for k, v in LINKED.items() if k != "image_url"}
    with pytest.raises(ManifestError, match="image_url"):
        _parse(row)


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://host/x.jpg", "javascript:x"])
def test_only_http_urls_are_accepted(url: str) -> None:
    with pytest.raises(ManifestError, match="http"):
        _parse({**LINKED, "image_url": url})


def test_a_non_redistributable_image_cannot_be_bundled() -> None:
    """The rule that keeps a licence breach from being one typo away."""
    with pytest.raises(ManifestError, match="redistribut"):
        _parse({**BUNDLED, "license": "all-rights-reserved"})


def test_a_bundled_case_must_be_pinned() -> None:
    row = {k: v for k, v in BUNDLED.items() if k != "image_sha256"}
    with pytest.raises(ManifestError, match="image_sha256"):
        _parse(row)


def test_unknown_distribution_is_rejected() -> None:
    with pytest.raises(ManifestError, match="distribution"):
        _parse({**BUNDLED, "distribution": "mirrored"})


# -- fetching --------------------------------------------------------------


@pytest.fixture()
def origin(tmp_path: Path) -> Iterator[dict]:
    """A loopback HTTP server serving one image."""
    served = tmp_path / "served"
    served.mkdir()
    payload = b"\xff\xd8\xff" + b"pretend-jpeg" * 64
    (served / "a.jpg").write_bytes(payload)

    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(served))

    class Quiet(socketserver.TCPServer):
        allow_reuse_address = True

        def handle_error(self, request, client_address):  # noqa: ANN001, D102
            pass

    server = Quiet(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield {
            "url": f"http://127.0.0.1:{server.server_address[1]}/a.jpg",
            "digest": hashlib.sha256(payload).hexdigest(),
            "root": served,
            "tmp": tmp_path,
        }
    finally:
        server.shutdown()


def test_fetch_downloads_and_content_addresses(origin: dict) -> None:
    rows = [{**LINKED, "image_url": origin["url"]}]
    result = fetch_images(rows, origin["tmp"] / "images", pause=0, retries=0)
    assert result.complete
    assert len(result.fetched) == 1
    assert result.fetched[0].digest == origin["digest"]
    assert (origin["tmp"] / "images" / f"{origin['digest']}.jpg").exists()


def test_a_dead_link_is_reported_not_ignored(origin: dict) -> None:
    rows = [{**LINKED, "image_url": origin["url"].replace("a.jpg", "gone.jpg")}]
    result = fetch_images(rows, origin["tmp"] / "images", pause=0, retries=0)
    assert not result.complete
    assert len(result.failed) == 1


def test_pinning_records_the_observed_digest(origin: dict) -> None:
    manifest = origin["tmp"] / "manifest.jsonl"
    rows = [{**LINKED, "image_url": origin["url"]}]
    manifest.write_text(json.dumps(rows[0]) + "\n")

    result = fetch_images(rows, origin["tmp"] / "images", pause=0, retries=0)
    pinned, warnings = pin_manifest(manifest, result)

    assert pinned == 1
    assert not warnings
    written = json.loads(manifest.read_text().strip())
    assert written["image_sha256"] == origin["digest"]
    assert written["image"] == f"images/{origin['digest']}.jpg"


def test_an_origin_that_changes_its_bytes_is_rejected(origin: dict) -> None:
    """The whole point of pinning: a swapped image must not be scored."""
    rows = [{**LINKED, "image_url": origin["url"], "image_sha256": "f" * 64}]
    result = fetch_images(rows, origin["tmp"] / "images", pause=0, retries=0)
    assert not result.complete
    assert len(result.mismatched) == 1
    # and nothing was written under the claimed digest
    assert not (origin["tmp"] / "images" / f"{'f' * 64}.jpg").exists()


def test_pinning_never_overwrites_an_existing_digest(origin: dict) -> None:
    """Re-pinning would erase the check that detects a changed origin."""
    manifest = origin["tmp"] / "manifest.jsonl"
    rows = [{**LINKED, "image_url": origin["url"], "image_sha256": "f" * 64}]
    manifest.write_text(json.dumps(rows[0]) + "\n")

    result = fetch_images(rows, origin["tmp"] / "images", pause=0, retries=0)
    pinned, _ = pin_manifest(manifest, result)

    assert pinned == 0
    assert json.loads(manifest.read_text().strip())["image_sha256"] == "f" * 64


def test_bundled_cases_are_not_fetched(origin: dict) -> None:
    result = fetch_images([BUNDLED], origin["tmp"] / "images", pause=0, retries=0)
    assert result.outcomes == []


# -- the invariant that matters most ---------------------------------------


def test_no_link_only_image_is_committed() -> None:
    """Link-only bytes in the repository would be exactly the breach avoided."""
    suite = Path(__file__).resolve().parents[1] / "bench"
    binaries = [p.name for p in suite.rglob("*") if p.suffix.lower() in {".jpg", ".png", ".webp"}]
    assert not binaries, f"image bytes present under bench/: {binaries[:5]}"

"""Fetch link-only case images from their source URLs.

WHY LINK-ONLY CASES EXIST
=========================
The most realistic field images are usually the ones nobody has licensed for
redistribution: an extension-service photograph, a research figure, a grower's
own picture. Excluding them would bias the benchmark toward whatever happens to
be CC-licensed, which is not the same distribution a system meets in the field.

So a case may instead carry a URL. The benchmark ships the *reference* and the
*ground truth*; whoever runs it downloads the bytes themselves, directly from
the origin, under whatever terms that origin sets. Nothing copyrighted is
redistributed by this repository, and the case still participates in scoring
exactly like any other.

The cost is that a link can rot or silently change. That is what
``image_sha256`` is for: once a case is pinned, every future download is
verified against the digest, so a replaced or re-encoded image is rejected
rather than quietly scored. A suite with unpinned cases is usable but not yet
reproducible, and :func:`fetch_images` says so loudly.

AUTHORING FLOW
==============
1. Add cases with ``distribution: "link-only"`` and an ``image_url``. The
   digest may be omitted -- you do not have the file yet.
2. Run ``agribench fetch --pin``. Each image is downloaded, hashed, and the
   observed digest written back into the manifest.
3. Commit the manifest. The bytes stay out of the repository; ``.gitignore``
   and a test both enforce that.

Only ``http`` and ``https`` URLs are accepted, redirects are bounded, and the
download size is capped, so a manifest entry cannot be turned into a local file
read or an unbounded write.
"""

from __future__ import annotations

import json
import mimetypes
import os
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agribench.datasets.stage import sha256_file

__all__ = [
    "DEFAULT_USER_AGENT",
    "FetchOutcome",
    "FetchResult",
    "fetch_images",
    "pin_manifest",
]

#: Identifies the tool to origin servers. Several extension sites reject the
#: default urllib agent outright.
DEFAULT_USER_AGENT = "agribench/0.1 (+https://github.com/kissanai/agrivision-bench)"

#: Refuse anything larger. A benchmark photograph is never 100 MB, and an
#: unbounded write driven by a manifest entry is not something to allow.
MAX_BYTES = 64 * 1024 * 1024

#: Only these schemes. Notably excludes file:// and ftp://.
ALLOWED_SCHEMES = frozenset({"http", "https"})

_EXT_BY_TYPE = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/tiff": ".tif",
    "image/bmp": ".bmp",
}


@dataclass
class FetchOutcome:
    """What happened to one case."""

    case_id: str
    status: str  # "fetched" | "present" | "failed" | "digest-mismatch" | "skipped"
    digest: str | None = None
    path: Path | None = None
    detail: str = ""


@dataclass
class FetchResult:
    """Aggregate of a fetch run."""

    outcomes: list[FetchOutcome] = field(default_factory=list)

    def _of(self, status: str) -> list[FetchOutcome]:
        return [o for o in self.outcomes if o.status == status]

    @property
    def fetched(self) -> list[FetchOutcome]:
        return self._of("fetched")

    @property
    def present(self) -> list[FetchOutcome]:
        return self._of("present")

    @property
    def failed(self) -> list[FetchOutcome]:
        return self._of("failed")

    @property
    def mismatched(self) -> list[FetchOutcome]:
        return self._of("digest-mismatch")

    @property
    def complete(self) -> bool:
        return not self.failed and not self.mismatched

    def summary(self) -> str:
        return (
            f"fetched {len(self.fetched)}, already present {len(self.present)}, "
            f"failed {len(self.failed)}, digest mismatch {len(self.mismatched)}"
        )


def _validate_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ALLOWED_SCHEMES:
        raise ValueError(
            f"only {sorted(ALLOWED_SCHEMES)} URLs may be fetched, got {parsed.scheme!r}"
        )
    if not parsed.netloc:
        raise ValueError(f"URL has no host: {url!r}")
    return url


def _extension_for(url: str, content_type: str | None) -> str:
    """Pick a file extension from the response type, falling back to the URL."""
    if content_type:
        base = content_type.split(";")[0].strip().lower()
        if base in _EXT_BY_TYPE:
            return _EXT_BY_TYPE[base]
        guessed = mimetypes.guess_extension(base)
        if guessed:
            return ".jpg" if guessed == ".jpe" else guessed
    suffix = Path(urllib.parse.urlparse(url).path).suffix.lower()
    return suffix if suffix else ".jpg"


def _download(
    url: str,
    destination: Path,
    *,
    timeout: float,
    user_agent: str,
    context: ssl.SSLContext | None = None,
) -> tuple[Path, str]:
    """Download ``url`` to a temp file beside ``destination``; return path+type."""
    request = urllib.request.Request(url, headers={"User-Agent": user_agent})
    temporary = destination.with_suffix(destination.suffix + ".part")
    written = 0
    with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
        content_type = response.headers.get("Content-Type")
        declared = response.headers.get("Content-Length")
        if declared and declared.isdigit() and int(declared) > MAX_BYTES:
            raise ValueError(f"declared size {declared} exceeds the {MAX_BYTES} byte cap")
        temporary.parent.mkdir(parents=True, exist_ok=True)
        with temporary.open("wb") as handle:
            while chunk := response.read(1 << 16):
                written += len(chunk)
                if written > MAX_BYTES:
                    handle.close()
                    temporary.unlink(missing_ok=True)
                    raise ValueError(f"response exceeded the {MAX_BYTES} byte cap")
                handle.write(chunk)
    return temporary, content_type or ""


def fetch_images(
    cases: Iterable[dict[str, Any]],
    images_dir: str | Path,
    *,
    timeout: float = 30.0,
    retries: int = 2,
    pause: float = 0.5,
    user_agent: str = DEFAULT_USER_AGENT,
    verify_existing: bool = False,
    only_missing: bool = True,
) -> FetchResult:
    """Download every link-only case image into ``images_dir``.

    Cases without an ``image_url`` are skipped: they are bundled cases, handled
    by :func:`agribench.datasets.stage.stage_images` instead.

    A case whose ``image_sha256`` is already recorded is verified against it and
    the download discarded on mismatch, because a link that now serves different
    bytes is a broken case, not an updated one. A case with no recorded digest is
    accepted and its observed digest reported, so :func:`pin_manifest` can write
    it back.

    Args:
        cases: manifest rows.
        images_dir: destination directory.
        timeout: per-request timeout in seconds.
        retries: additional attempts after a failure.
        pause: seconds between attempts, and between downloads, to stay polite
            to origin servers that are usually small institutional hosts.
        user_agent: sent with every request.
        verify_existing: re-hash files already on disk.
        only_missing: skip cases whose image is already staged.

    Returns:
        A :class:`FetchResult`; ``complete`` is False if anything failed or
        failed verification.
    """
    images_dir = Path(images_dir)
    images_dir.mkdir(parents=True, exist_ok=True)
    result = FetchResult()

    for case in cases:
        case_id = str(case.get("case_id") or "?")
        url = case.get("image_url")
        if not url:
            continue

        recorded = str(case.get("image_sha256") or "") or None
        declared_name = str(case.get("image") or "")
        existing = images_dir / Path(declared_name).name if declared_name else None

        if only_missing and existing and existing.exists():
            if verify_existing and recorded and sha256_file(existing) != recorded:
                result.outcomes.append(
                    FetchOutcome(case_id, "digest-mismatch", recorded, existing,
                                 "staged file does not match the recorded digest")
                )
            else:
                result.outcomes.append(
                    FetchOutcome(case_id, "present", recorded, existing)
                )
            continue

        try:
            _validate_url(str(url))
        except ValueError as exc:
            result.outcomes.append(FetchOutcome(case_id, "failed", detail=str(exc)))
            continue

        last_error = ""
        for attempt in range(retries + 1):
            if attempt:
                time.sleep(pause * (2 ** (attempt - 1)))
            temporary: Path | None = None
            try:
                provisional = images_dir / f".{case_id}.download"
                temporary, content_type = _download(
                    str(url), provisional, timeout=timeout, user_agent=user_agent
                )
                digest = sha256_file(temporary)

                if recorded and digest != recorded:
                    temporary.unlink(missing_ok=True)
                    result.outcomes.append(
                        FetchOutcome(
                            case_id, "digest-mismatch", digest, None,
                            f"expected {recorded[:12]}…, origin served {digest[:12]}…",
                        )
                    )
                    last_error = ""
                    break

                extension = _extension_for(str(url), content_type)
                final = images_dir / f"{digest}{extension}"
                os.replace(temporary, final)
                result.outcomes.append(FetchOutcome(case_id, "fetched", digest, final))
                last_error = ""
                break
            except (urllib.error.URLError, ValueError, OSError, TimeoutError) as exc:
                last_error = str(getattr(exc, "reason", None) or exc)
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
        else:
            pass

        if last_error:
            result.outcomes.append(
                FetchOutcome(case_id, "failed", detail=last_error)
            )
        time.sleep(pause)

    return result


def pin_manifest(
    manifest_path: str | Path,
    result: FetchResult,
    *,
    dry_run: bool = False,
) -> tuple[int, list[str]]:
    """Write observed digests back into a manifest for unpinned cases.

    Only cases that had no ``image_sha256`` are touched. An already-pinned case
    is never rewritten: silently re-pinning would erase the very check that
    detects a link serving different bytes than the one the ground truth was
    written against.

    Args:
        manifest_path: JSON Lines manifest to update in place.
        result: outcome of a :func:`fetch_images` run.
        dry_run: report what would change without writing.

    Returns:
        ``(count_pinned, warnings)``.
    """
    manifest_path = Path(manifest_path)
    observed = {
        o.case_id: (o.digest, o.path)
        for o in result.outcomes
        if o.status == "fetched" and o.digest
    }

    rows: list[dict[str, Any]] = []
    pinned = 0
    warnings: list[str] = []
    for line in manifest_path.read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        case_id = str(row.get("case_id") or "")
        if not row.get("image_sha256") and case_id in observed:
            digest, path = observed[case_id]
            row["image_sha256"] = digest
            if path is not None:
                row["image"] = f"images/{path.name}"
            pinned += 1
        elif row.get("image_url") and not row.get("image_sha256"):
            warnings.append(f"{case_id}: still unpinned (fetch did not succeed)")
        rows.append(row)

    if not dry_run and pinned:
        manifest_path.write_text(
            "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows)
        )
    return pinned, warnings

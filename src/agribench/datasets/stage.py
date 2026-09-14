"""Materialise a suite's images into the content-addressed layout.

The repository ships a manifest, not a redistribution of third-party images.
Each case names its image only by SHA-256, so before a suite can be run the
bytes have to be put on disk as ``images/<sha256><ext>`` next to the manifest.

This module does that from any local directory that happens to contain the
images -- a dataset checkout, an extracted archive, a scratch download folder.
Files are matched by **content**, never by filename, so a source tree that has
renamed or reorganised everything still works, and a file that has been
re-encoded or truncated is rejected rather than silently benchmarked.

Nothing here reaches the network. Obtaining the bytes in the first place is
deliberately out of scope: each image carries its own licence, and the terms
under which it may be copied are the operator's responsibility, not a
convenience this tool should paper over.
"""

from __future__ import annotations

import hashlib
import os
import shutil
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = ["StageResult", "iter_candidate_files", "sha256_file", "stage_images"]

#: Extensions considered image candidates when scanning a source tree.
IMAGE_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"})

#: Directories never worth descending into while scanning.
SKIP_DIRS = frozenset({".git", "node_modules", "__pycache__", ".venv", "venv"})

_CHUNK = 1 << 20


def sha256_file(path: str | Path) -> str:
    """Return the hex SHA-256 of a file's bytes, read in chunks."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass
class StageResult:
    """What :func:`stage_images` did, and what is still missing.

    Attributes:
        staged: images newly written into the suite.
        already_present: images that were already staged and verified.
        missing: SHA-256 digests the source tree did not contain.
        corrupt: paths whose bytes did not match their recorded digest.
        scanned: candidate files examined in the source tree.
    """

    staged: int = 0
    already_present: int = 0
    missing: list[str] = field(default_factory=list)
    corrupt: list[str] = field(default_factory=list)
    scanned: int = 0

    @property
    def complete(self) -> bool:
        """Whether every case image is now present and verified."""
        return not self.missing and not self.corrupt

    def summary(self) -> str:
        parts = [
            f"staged {self.staged}",
            f"already present {self.already_present}",
            f"missing {len(self.missing)}",
        ]
        if self.corrupt:
            parts.append(f"corrupt {len(self.corrupt)}")
        return ", ".join(parts)


def iter_candidate_files(root: str | Path) -> Iterator[Path]:
    """Yield image-looking files beneath ``root``, skipping noise directories."""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            if Path(name).suffix.lower() in IMAGE_SUFFIXES:
                yield Path(dirpath) / name


def stage_images(
    cases: Iterable[dict[str, Any]],
    source_root: str | Path,
    images_dir: str | Path,
    *,
    move: bool = False,
    verify_existing: bool = True,
) -> StageResult:
    """Copy every case image out of ``source_root`` into ``images_dir``.

    Matching is by content digest. The source tree is walked once and each
    candidate hashed only until every wanted digest has been found, so a large
    tree costs at most one full pass and usually much less.

    Args:
        cases: manifest rows, each with ``image`` and ``image_sha256``.
        source_root: directory to search for the bytes.
        images_dir: destination, normally the suite's ``images/`` directory.
        move: move instead of copy. Destructive to the source; off by default.
        verify_existing: re-hash images already in ``images_dir``. Leaving this
            on is what turns a half-finished or tampered staging directory into
            a reported error instead of a silently wrong benchmark run.

    Returns:
        A :class:`StageResult`. A non-empty ``missing`` or ``corrupt`` means the
        suite is not yet runnable.
    """
    images_dir = Path(images_dir)
    images_dir.mkdir(parents=True, exist_ok=True)
    result = StageResult()

    # digest -> destination filename (extension comes from the manifest)
    wanted: dict[str, str] = {}
    for case in cases:
        digest = str(case.get("image_sha256") or "")
        if not digest:
            continue
        wanted[digest] = Path(str(case["image"])).name

    for digest, filename in list(wanted.items()):
        destination = images_dir / filename
        if not destination.exists():
            continue
        if verify_existing and sha256_file(destination) != digest:
            result.corrupt.append(str(destination))
            continue
        result.already_present += 1
        wanted.pop(digest)

    if wanted:
        for candidate in iter_candidate_files(source_root):
            if not wanted:
                break
            result.scanned += 1
            try:
                digest = sha256_file(candidate)
            except OSError:
                continue
            filename = wanted.pop(digest, None)
            if filename is None:
                continue
            destination = images_dir / filename
            if move:
                shutil.move(str(candidate), destination)
            else:
                shutil.copy2(candidate, destination)
            result.staged += 1

    result.missing = sorted(wanted)
    return result


def audit_images(
    cases: Iterable[dict[str, Any]],
    images_dir: str | Path,
    *,
    deep: bool = False,
) -> StageResult:
    """Report which case images are present without copying anything.

    Args:
        cases: manifest rows.
        images_dir: directory the images should already be in.
        deep: re-hash every present file. Slower, but the only way to catch a
            file that was replaced by different bytes under the same name.
    """
    images_dir = Path(images_dir)
    result = StageResult()
    for case in cases:
        digest = str(case.get("image_sha256") or "")
        destination = images_dir / Path(str(case["image"])).name
        if not destination.exists():
            result.missing.append(digest)
            continue
        if deep and sha256_file(destination) != digest:
            result.corrupt.append(str(destination))
            continue
        result.already_present += 1
    return result

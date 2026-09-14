"""Load, validate, and fingerprint a benchmark manifest.

A suite lives in ``tracks/<track>/suites/<suite>/`` and is described by ``manifest.jsonl``:
one JSON object per line, one line per case::

    {"case_id": "of1-0000", "entity": "aloe_vera",
     "image": "images/f988f096….jpg", "image_sha256": "f988f096…",
     "issue": "Anthracnose", "issue_family": "disease",
     "license": "MIT", "source": "Kaggle"}

Three jobs, all of them about making a published number re-derivable:

**Validate.** Every required field must be present, non-empty, and of the right
shape. ``case_id`` must be unique. ``image`` must be a repo-relative POSIX path
that cannot escape the suite directory. A manifest that fails any of these
raises :class:`~agribench.errors.ManifestError` naming the offending line.

**Fingerprint.** :func:`compute_manifest_sha256` reduces the case set to a
single hex digest over sorted ``(case_id, image_sha256)`` pairs. Two runs are
comparable only if they carry the same digest, so this value is stamped into
every run file and checked before any leaderboard is built. It is deliberately
insensitive to line order and to fields that do not change what was measured
(``license``, ``source``), and deliberately sensitive to a case being added,
removed, renamed, or repointed at different image bytes.

**Resolve.** Images are stored by content hash under the suite's ``images/``
and are not committed to the repository, so a manifest is useful before its
images exist. Path resolution and existence checks are therefore separate
steps: loading never touches the image directory.

Stdlib only — this module sits on the scoring path.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from .errors import ManifestError

__all__ = [
    "MANIFEST_FILENAME",
    "REQUIRED_FIELDS",
    "Case",
    "Manifest",
    "compute_manifest_sha256",
    "load_manifest",
    "parse_manifest",
    "sha256_file",
]

#: Conventional file name inside a suite directory.
MANIFEST_FILENAME = "manifest.jsonl"

#: Every field a case must carry. ``license`` and ``source`` are required
#: because a public benchmark that cannot say where an image came from, and
#: under what terms, is not redistributable.
REQUIRED_FIELDS: tuple[str, ...] = (
    "case_id",
    "entity",
    "image",
    "image_sha256",
    "issue",
    "issue_family",
    "license",
    "source",
)

#: How a case's image bytes reach the person running the benchmark.
#:
#: ``bundled``
#:     the licence permits redistribution, so the file may be staged from a
#:     copy and, in principle, shipped alongside the manifest.
#: ``link-only``
#:     the licence does not permit redistribution, or there is no licence at
#:     all. The manifest carries a URL and nothing else; whoever runs the
#:     benchmark downloads the bytes from the origin themselves, under whatever
#:     terms that origin sets. These files must never enter the repository.
#: ``source-only``
#:     there is neither a redistribution grant nor an address that returns the
#:     image. The case names an upstream *dataset*, and whoever runs the
#:     benchmark obtains that dataset from its publisher and stages the file out
#:     of it by content digest. This exists because the honest alternative --
#:     calling such a case ``bundled`` -- asserts a redistribution right nobody
#:     granted, and calling it ``link-only`` invents a URL that does not exist.
#:     A suite of these is reproducible, but not in one command.
DISTRIBUTION_VALUES: frozenset[str] = frozenset({"bundled", "link-only", "source-only"})

#: Default when a case does not say. Bundled is the stricter choice: it demands
#: a digest up front and a redistributable licence, so an omission fails loudly
#: instead of silently creating an unverifiable case.
DEFAULT_DISTRIBUTION = "bundled"

#: Licences under which an image may be redistributed. A ``bundled`` case whose
#: licence is not one of these is a licence breach waiting to happen.
REDISTRIBUTABLE_LICENCES: frozenset[str] = frozenset(
    {"CC0", "CC-BY", "MIT", "Apache-2.0", "public-domain"}
)

#: Optional fields a case may carry. ``image_url`` is required for link-only
#: cases; the rest are attribution metadata, preserved verbatim in reports.
OPTIONAL_FIELDS: tuple[str, ...] = (
    "distribution",
    "image_url",
    "source_page",
    "credit",
    "notes",
)

_SHA256_RE = re.compile(r"\A[0-9a-f]{64}\Z")
_CASE_ID_RE = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._-]*\Z")
_READ_CHUNK = 1024 * 1024


def sha256_file(path: str | Path) -> str:
    """Return the hex sha256 of a file's bytes, read in chunks.

    Raises:
        ManifestError: If the file cannot be read.
    """
    digest = hashlib.sha256()
    try:
        with Path(path).open("rb") as handle:
            for chunk in iter(lambda: handle.read(_READ_CHUNK), b""):
                digest.update(chunk)
    except OSError as exc:
        raise ManifestError(f"cannot read {path}: {exc.strerror or exc}") from exc
    return digest.hexdigest()


@dataclass(frozen=True, slots=True)
class Case:
    """One benchmark case: an image plus its ground truth.

    Attributes:
        case_id: Stable unique identifier, e.g. ``"of1-0000"``.
        entity: Ground-truth subject (crop/animal/weed), e.g. ``"aloe_vera"``.
        image: Suite-relative POSIX path, e.g. ``"images/f988….jpg"``.
        image_sha256: Hex sha256 of the image bytes. Pins the file
            byte-for-byte; a re-encoded image is a different case.
        issue: Ground-truth condition label, e.g. ``"Anthracnose"``.
        issue_family: Coarse family of the condition, e.g. ``"disease"``.
            Used for report breakdowns, never for scoring.
        license: Redistribution licence of the image.
        source: Where the image came from.
        extra: Any additional manifest keys, preserved verbatim.
    """

    case_id: str
    entity: str
    image: str
    image_sha256: str
    issue: str
    issue_family: str
    license: str
    source: str
    extra: dict[str, Any] = field(default_factory=dict, compare=False, repr=False)

    @property
    def image_path(self) -> PurePosixPath:
        """The suite-relative image path."""
        return PurePosixPath(self.image)

    def resolve_image(self, images_root: str | Path, *, must_exist: bool = True) -> Path:
        """Resolve this case's image against ``images_root``.

        Args:
            images_root: Directory the ``image`` field is relative to — the
                suite directory, e.g. ``tracks/diagnosis/suites/openfield_v1``.
            must_exist: Raise if the resolved file is not present.

        Returns:
            The resolved absolute path.

        Raises:
            ManifestError: If the path escapes ``images_root``, or if
                ``must_exist`` and the file is missing.
        """
        root = Path(images_root).resolve()
        resolved = (root / self.image).resolve()
        if not resolved.is_relative_to(root):
            raise ManifestError(
                f"case {self.case_id!r}: image path {self.image!r} escapes the "
                f"images root {root}",
                hint="image must be a relative path inside the suite directory",
            )
        if must_exist and not resolved.is_file():
            raise ManifestError(
                f"case {self.case_id!r}: image not found at {resolved}",
                hint=(
                    "benchmark images are distributed separately from the "
                    "manifest; fetch the image set into the suite directory"
                ),
            )
        return resolved

    def verify_image(self, images_root: str | Path) -> bool:
        """Return ``True`` if the image on disk hashes to ``image_sha256``."""
        return sha256_file(self.resolve_image(images_root)) == self.image_sha256

    def to_dict(self) -> dict[str, Any]:
        """Return the case as a JSON-ready mapping, extras included."""
        return {
            "case_id": self.case_id,
            "entity": self.entity,
            "image": self.image,
            "image_sha256": self.image_sha256,
            "issue": self.issue,
            "issue_family": self.issue_family,
            "license": self.license,
            "source": self.source,
            **self.extra,
        }


def _fail(location: str, message: str, *, hint: str | None = None) -> None:
    raise ManifestError(f"{location}: {message}", hint=hint)


def _case_from_row(row: Any, location: str) -> Case:
    """Build and validate one :class:`Case` from a decoded manifest row."""
    if not isinstance(row, Mapping):
        _fail(location, f"expected a JSON object, got {type(row).__name__}")

    distribution = str(row.get("distribution") or DEFAULT_DISTRIBUTION).strip()
    if distribution not in DISTRIBUTION_VALUES:
        _fail(
            location,
            f"distribution {distribution!r} must be one of "
            f"{', '.join(sorted(DISTRIBUTION_VALUES))}",
        )
    link_only = distribution == "link-only"
    source_only = distribution == "source-only"

    # A link-only case is authored before its image has ever been downloaded,
    # so it cannot state a digest yet. Everything else must.
    required = tuple(
        name for name in REQUIRED_FIELDS if not (link_only and name == "image_sha256")
    )
    missing = [name for name in required if name not in row]
    if missing:
        _fail(
            location,
            f"missing required field(s): {', '.join(missing)}",
            hint=f"required fields are: {', '.join(required)}",
        )

    values: dict[str, str] = {}
    for name in required:
        value = row[name]
        if not isinstance(value, str) or not value.strip():
            _fail(location, f"field {name!r} must be a non-empty string, got {value!r}")
        values[name] = value.strip()
    values.setdefault("image_sha256", str(row.get("image_sha256") or "").strip())

    case_id = values["case_id"]
    if not _CASE_ID_RE.match(case_id):
        _fail(
            location,
            f"case_id {case_id!r} must be alphanumeric with '.', '-' or '_'",
            hint="case ids appear in file names and result tables; keep them portable",
        )

    digest = values["image_sha256"]
    if digest and not _SHA256_RE.match(digest):
        _fail(
            location,
            f"case {case_id!r}: image_sha256 must be 64 lowercase hex characters, "
            f"got {digest!r}",
        )
    if not digest and not link_only:
        _fail(
            location,
            f"case {case_id!r}: image_sha256 is required for a "
            f"{distribution} case",
            hint="only link-only cases may be unpinned, and only until first fetch",
        )

    if link_only:
        url = str(row.get("image_url") or "").strip()
        if not url:
            _fail(
                location,
                f"case {case_id!r}: link-only cases must carry an image_url",
                hint="the URL is the only way anyone can obtain this image",
            )
        scheme = url.split("://", 1)[0].lower() if "://" in url else ""
        if scheme not in ("http", "https"):
            _fail(
                location,
                f"case {case_id!r}: image_url must be http or https, got {url!r}",
            )
    elif source_only:
        # No licence rule: a source-only case makes no redistribution claim, so
        # an unknown or non-commercial licence is a fact to record rather than a
        # defect. What it must carry is a digest (checked above) and a source,
        # which is already required of every case -- without both, the file
        # cannot be identified inside whatever archive the publisher ships.
        pass
    elif values["license"] not in REDISTRIBUTABLE_LICENCES:
        _fail(
            location,
            f"case {case_id!r}: licence {values['license']!r} does not permit "
            f"redistribution, so the case cannot be bundled",
            hint=(
                'mark it {"distribution": "link-only"} with an image_url, or '
                '{"distribution": "source-only"} to point at the upstream dataset '
                "instead, or drop it. Redistributable: "
                f"{', '.join(sorted(REDISTRIBUTABLE_LICENCES))}"
            ),
        )

    image = values["image"]
    if image.startswith("/") or "\\" in image or re.match(r"\A[A-Za-z]:", image):
        _fail(
            location,
            f"case {case_id!r}: image {image!r} must be a relative POSIX path",
            hint="absolute and Windows-style paths are not portable and not publishable",
        )
    if any(part in ("..", "") for part in PurePosixPath(image).parts):
        _fail(location, f"case {case_id!r}: image {image!r} must not contain '..' segments")

    extra = {key: value for key, value in row.items() if key not in REQUIRED_FIELDS}
    return Case(**values, extra=extra)


def parse_manifest(lines: Iterable[str], *, source: str = "<memory>") -> tuple[Case, ...]:
    """Parse and validate JSON Lines into cases.

    Blank lines are skipped. Line numbers in error messages are 1-based and
    count blank lines, so they match what an editor shows.

    Raises:
        ManifestError: On malformed JSON, a bad field, or a duplicate case id.
    """
    cases: list[Case] = []
    seen: dict[str, int] = {}
    for lineno, raw in enumerate(lines, start=1):
        if not raw.strip():
            continue
        location = f"{source}:{lineno}"
        try:
            row = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ManifestError(f"{location}: invalid JSON: {exc.msg}") from exc
        case = _case_from_row(row, location)
        if case.case_id in seen:
            _fail(
                location,
                f"duplicate case_id {case.case_id!r} (first seen on line {seen[case.case_id]})",
                hint="case ids must be unique; they key every result row",
            )
        seen[case.case_id] = lineno
        cases.append(case)

    if not cases:
        raise ManifestError(f"{source}: manifest contains no cases")
    return tuple(cases)


def compute_manifest_sha256(cases: Iterable[Case | Mapping[str, Any]]) -> str:
    """Fingerprint a case set.

    The digest is taken over ``case_id`` and ``image_sha256`` only, sorted by
    ``case_id``, each pair encoded as ``f"{case_id}\\x00{image_sha256}\\n"`` in
    UTF-8. Line order in the file does not matter; adding, removing, renaming,
    or repointing a case does.

    Fields that describe provenance rather than measurement (``license``,
    ``source``) are excluded on purpose: correcting an attribution typo must
    not orphan every run ever done against the suite.

    Raises:
        ManifestError: If a row is missing either field.
    """
    pairs: list[tuple[str, str]] = []
    for case in cases:
        if isinstance(case, Case):
            pairs.append((case.case_id, case.image_sha256))
            continue
        try:
            pairs.append((str(case["case_id"]), str(case["image_sha256"])))
        except (KeyError, TypeError) as exc:
            raise ManifestError(
                f"cannot fingerprint row without case_id and image_sha256: {case!r}"
            ) from exc

    digest = hashlib.sha256()
    for case_id, image_sha256 in sorted(pairs):
        digest.update(f"{case_id}\x00{image_sha256}\n".encode())
    return digest.hexdigest()


@dataclass(frozen=True, slots=True)
class Manifest:
    """A validated case set plus its fingerprint.

    Attributes:
        manifest_id: Suite identifier, e.g. ``"field_v1"``. Defaults to the
            name of the directory the manifest was loaded from.
        cases: The cases, in file order.
        manifest_sha256: See :func:`compute_manifest_sha256`.
        path: Where it was loaded from, or ``None`` if built in memory.
        images_root: Directory that ``image`` paths resolve against. Defaults
            to the manifest's own directory.
    """

    manifest_id: str
    cases: tuple[Case, ...]
    manifest_sha256: str
    path: Path | None = None
    images_root: Path | None = None
    _index: dict[str, Case] = field(
        default_factory=dict, init=False, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        if not self.cases:
            raise ManifestError(f"manifest {self.manifest_id!r} contains no cases")
        index: dict[str, Case] = {}
        for case in self.cases:
            if case.case_id in index:
                raise ManifestError(
                    f"manifest {self.manifest_id!r}: duplicate case_id {case.case_id!r}"
                )
            index[case.case_id] = case
        object.__setattr__(self, "_index", index)

    # -- container behaviour ----------------------------------------------

    def __len__(self) -> int:
        return len(self.cases)

    def __iter__(self) -> Iterator[Case]:
        return iter(self.cases)

    def __contains__(self, case_id: object) -> bool:
        return case_id in self._index

    def __getitem__(self, case_id: str) -> Case:
        return self.get(case_id)

    def get(self, case_id: str) -> Case:
        """Return one case by id.

        Raises:
            ManifestError: If the id is not in this manifest.
        """
        try:
            return self._index[case_id]
        except KeyError:
            raise ManifestError(
                f"case {case_id!r} is not in manifest {self.manifest_id!r}"
            ) from None

    @property
    def case_ids(self) -> tuple[str, ...]:
        """Case ids in file order."""
        return tuple(case.case_id for case in self.cases)

    @property
    def by_id(self) -> dict[str, Case]:
        """A fresh ``case_id -> Case`` mapping."""
        return dict(self._index)

    # -- reporting helpers -------------------------------------------------

    def families(self) -> Counter[str]:
        """Case count per ``issue_family``."""
        return Counter(case.issue_family for case in self.cases)

    def entities(self) -> Counter[str]:
        """Case count per ground-truth entity."""
        return Counter(case.entity for case in self.cases)

    def licenses(self) -> Counter[str]:
        """Case count per licence."""
        return Counter(case.license for case in self.cases)

    def sources(self) -> Counter[str]:
        """Case count per source."""
        return Counter(case.source for case in self.cases)

    def summary(self) -> dict[str, Any]:
        """A JSON-ready description of the suite, for embedding in reports."""
        return {
            "manifest_id": self.manifest_id,
            "manifest_sha256": self.manifest_sha256,
            "case_count": len(self.cases),
            "entity_count": len(set(self.entities())),
            "issue_count": len({case.issue for case in self.cases}),
            "issue_families": dict(sorted(self.families().items())),
            "licenses": dict(sorted(self.licenses().items())),
            "sources": dict(sorted(self.sources().items())),
        }

    # -- image handling ----------------------------------------------------

    def _root(self, images_root: str | Path | None) -> Path:
        root = images_root or self.images_root or (self.path.parent if self.path else None)
        if root is None:
            raise ManifestError(
                f"manifest {self.manifest_id!r} has no images root",
                hint="pass images_root= explicitly for a manifest built in memory",
            )
        return Path(root)

    def resolve_images(
        self,
        images_root: str | Path | None = None,
        *,
        must_exist: bool = True,
    ) -> dict[str, Path]:
        """Return ``case_id -> resolved image path`` for every case.

        Raises:
            ManifestError: If ``must_exist`` and any image is missing. The
                message names how many are missing and gives one example.
        """
        root = self._root(images_root)
        resolved = {
            case.case_id: case.resolve_image(root, must_exist=False) for case in self.cases
        }
        if must_exist:
            missing = [cid for cid, path in resolved.items() if not path.is_file()]
            if missing:
                raise ManifestError(
                    f"{len(missing)} of {len(self.cases)} images are missing under "
                    f"{root} (first: {missing[0]})",
                    hint="fetch the image set for this suite before running",
                )
        return resolved

    def missing_images(self, images_root: str | Path | None = None) -> tuple[str, ...]:
        """Case ids whose image file is absent, in manifest order."""
        return tuple(
            case_id
            for case_id, path in self.resolve_images(images_root, must_exist=False).items()
            if not path.is_file()
        )

    def verify_images(
        self,
        images_root: str | Path | None = None,
        *,
        case_ids: Sequence[str] | None = None,
    ) -> tuple[str, ...]:
        """Re-hash images and return the ids whose bytes do not match.

        Reads every file, so it is slow by design — it is the check that
        proves a local copy of the suite has not drifted. Restrict it with
        ``case_ids`` for a spot check.

        Raises:
            ManifestError: If an image is missing (a missing file is a
                different problem from a corrupted one; see
                :meth:`missing_images`).
        """
        root = self._root(images_root)
        subset = self.cases if case_ids is None else [self.get(cid) for cid in case_ids]
        return tuple(case.case_id for case in subset if not case.verify_image(root))


def load_manifest(
    path: str | Path,
    *,
    manifest_id: str | None = None,
    images_root: str | Path | None = None,
) -> Manifest:
    """Load and validate a manifest from a JSON Lines file.

    Args:
        path: The ``manifest.jsonl`` file, or the suite directory containing
            one.
        manifest_id: Suite id. Defaults to the containing directory's name
            (``tracks/diagnosis/suites/field_v1/manifest.jsonl`` → ``"field_v1"``).
        images_root: Directory that ``image`` paths resolve against. Defaults
            to the manifest's own directory.

    Returns:
        A validated :class:`Manifest`. Image files are never touched here.

    Raises:
        ManifestError: If the file is missing, unreadable, or invalid.
    """
    manifest_path = Path(path)
    if manifest_path.is_dir():
        manifest_path = manifest_path / MANIFEST_FILENAME
    if not manifest_path.is_file():
        raise ManifestError(
            f"manifest not found: {manifest_path}",
            hint=f"expected a JSON Lines file, conventionally named {MANIFEST_FILENAME}",
        )

    try:
        text = manifest_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ManifestError(
            f"cannot read manifest {manifest_path}: {exc.strerror or exc}"
        ) from exc
    except UnicodeDecodeError as exc:
        raise ManifestError(f"manifest {manifest_path} is not valid UTF-8: {exc}") from exc

    cases = parse_manifest(text.splitlines(), source=str(manifest_path))
    resolved_id = manifest_id or manifest_path.parent.name or manifest_path.stem
    return Manifest(
        manifest_id=resolved_id,
        cases=cases,
        manifest_sha256=compute_manifest_sha256(cases),
        path=manifest_path,
        images_root=Path(images_root) if images_root else None,
    )

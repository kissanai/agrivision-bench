"""Run files: the raw, per-case record a published number is derived from.

A leaderboard row is an assertion; a run file is the evidence. Every published
entry ships one, so a reader can re-score it from source instead of trusting
the table. That makes this module's compatibility surface unusually
load-bearing: files written years apart must stay readable, and files written
by an older harness must stay re-scorable.

Format (run file v1)
--------------------
Two encodings, one data model. The extension decides which is written; the
reader sniffs the content and accepts either.

``.jsonl`` (canonical) — a metadata header line followed by one result per
line, so a long run streams to disk and survives an interrupted process with
everything before the cut still readable::

    {"run_file_version": 1, "metadata": {...}}
    {"case_id": "of1-0000", "entity": "aloe_vera", "issue": "Anthracnose", ...}

``.json`` — a single object, convenient for small runs and for diffing::

    {"run_file_version": 1, "metadata": {...}, "results": [...]}

Metadata carries what makes a run comparable: ``manifest_id``,
``manifest_sha256``, ``system_name``, ``system_type`` (``single_call`` or
``system``), ``contract_version``, ``created_at``. A result row carries the
ground truth it was scored against (``case_id``, ``entity``, ``issue``), the
system's ``prediction``, and the three scored flags — ``subject_ok``,
``issue_class_ok``, ``issue_cat_ok`` — so a table can be rebuilt without
re-running the matcher, and the matcher can be re-run without re-running the
system.

Reading historical files
------------------------
:func:`read_legacy` converts run files from before this format existed, whose
per-case payload sat under a provider-shaped key. Without it, previously
published results could not be re-scored under the current contract, and a
benchmark that cannot re-score its own history is asking to be taken on faith.

What is deliberately *not* carried across: absolute filesystem paths, source
file names, and any harness-internal fields from the original payload. A run
file is a published artifact; it inherits nothing it does not need.

Stdlib only.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePath
from typing import Any

from .contract import CONTRACT_VERSION
from .contract.matching import family_of
from .contract.metrics import DECLINED_CATEGORIES, CaseScore
from .errors import AdapterError, ManifestError
from .manifest import Manifest, sha256_file

__all__ = [
    "CONTRACT_VERSION",
    "LEGACY_PAYLOAD_KEYS",
    "RUN_FILE_VERSION",
    "SYSTEM_TYPES",
    "Prediction",
    "Result",
    "RunFile",
    "RunMetadata",
    "atomic_write_text",
    "read_legacy",
    "read_run",
    "utc_now_iso",
    "write_run",
]

#: Bumped only if the on-disk shape changes incompatibly.
RUN_FILE_VERSION = 1

#: ``single_call``: one model, one call, one response.
#: ``system``: a composite — routing, retrieval, several models, or a gate.
SYSTEM_TYPES: tuple[str, ...] = ("single_call", "system")

#: Per-case payload keys used by run files predating this format.
LEGACY_PAYLOAD_KEYS: tuple[str, ...] = ("ours", "gemini")


def utc_now_iso() -> str:
    """Current UTC time as ``YYYY-MM-DDTHH:MM:SSZ`` (whole seconds)."""
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def atomic_write_text(path: str | Path, text: str) -> Path:
    """Write ``text`` to ``path`` atomically, creating parent directories.

    Writes to a temporary file in the destination directory and renames it, so
    a reader never observes a half-written run file and an interrupted write
    leaves the previous version intact.
    """
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(
        dir=destination.parent, prefix=f".{destination.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, destination)
    except BaseException:
        Path(temp_name).unlink(missing_ok=True)
        raise
    return destination


def _clean_str(value: Any) -> str:
    """Coerce a recorded string field. ``None`` becomes ``""``."""
    if value is None:
        return ""
    return value.strip() if isinstance(value, str) else str(value).strip()


def _clean_secondary(value: Any) -> tuple[dict[str, str], ...]:
    """Normalise ``secondary_issues`` into issue/category mappings."""
    if not isinstance(value, Iterable) or isinstance(value, (str, bytes, Mapping)):
        return ()
    items: list[dict[str, str]] = []
    for item in value:
        if isinstance(item, Mapping):
            issue = _clean_str(item.get("issue"))
            category = _clean_str(item.get("category")).lower()
        else:
            issue, category = _clean_str(item), ""
        if issue or category:
            items.append({"issue": issue, "category": category})
    return tuple(items)


@dataclass(frozen=True, slots=True)
class Prediction:
    """What a system returned for one case, as recorded on disk.

    The five response fields mirror the frozen schema; ``answered`` is the
    benchmark's own bit. This is the storage-side counterpart of the
    adapter-side prediction object — :meth:`from_dict` accepts either that
    object or a plain mapping.

    Attributes:
        answered: The system committed to a usable diagnosis. ``False`` is an
            abstention: it lowers coverage and is never scored as wrong.
        subject: Crop, animal, or weed species named by the system.
        subject_type: ``crop``/``animal``/``weed``/``insect``/``other``.
            Recorded, not scored.
        primary_issue: The main problem, named as specifically as possible.
        primary_category: The issue family the system committed to.
        secondary_issues: Other findings, recorded and not scored.
        secondary_count: How many secondary findings the system reported.
            Normally ``len(secondary_issues)``; it exists because some
            historical files recorded only the count.
    """

    answered: bool
    subject: str = ""
    subject_type: str = ""
    primary_issue: str = ""
    primary_category: str = ""
    secondary_issues: tuple[dict[str, str], ...] = ()
    secondary_count: int | None = None

    @property
    def secondaries(self) -> int:
        """Number of secondary findings, whether listed or only counted."""
        return len(self.secondary_issues) if self.secondary_count is None else self.secondary_count

    @classmethod
    def from_dict(cls, payload: Any, *, answered: bool | None = None) -> Prediction:
        """Build from a mapping, or from any object exposing ``to_dict()``.

        Missing fields become empty strings. When ``answered`` is neither
        passed nor present in the payload it is derived from
        ``primary_category`` using the frozen rule shared with the contract:
        missing, empty, or ``"unknown"`` means the system declined.

        Raises:
            AdapterError: If ``payload`` is not mapping-shaped.
        """
        if not isinstance(payload, Mapping):
            to_dict = getattr(payload, "to_dict", None)
            if callable(to_dict):
                payload = to_dict()
        if not isinstance(payload, Mapping):
            raise AdapterError(
                f"prediction must be a JSON object, got {type(payload).__name__}"
            )

        category = _clean_str(payload.get("primary_category")).lower()
        if answered is None:
            answered = (
                bool(payload["answered"])
                if "answered" in payload
                else category not in DECLINED_CATEGORIES
            )
        secondary = _clean_secondary(payload.get("secondary_issues"))
        raw_count = payload.get("secondary_count")
        return cls(
            answered=bool(answered),
            subject=_clean_str(payload.get("subject")),
            subject_type=_clean_str(payload.get("subject_type")).lower(),
            primary_issue=_clean_str(payload.get("primary_issue")),
            primary_category=category,
            secondary_issues=secondary,
            secondary_count=(
                int(raw_count) if isinstance(raw_count, int) and not secondary else None
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready mapping in a stable key order."""
        payload: dict[str, Any] = {
            "answered": self.answered,
            "subject": self.subject,
            "subject_type": self.subject_type,
            "primary_issue": self.primary_issue,
            "primary_category": self.primary_category,
            "secondary_issues": [dict(item) for item in self.secondary_issues],
        }
        if self.secondary_count is not None:
            payload["secondary_count"] = self.secondary_count
        return payload


@dataclass(frozen=True, slots=True)
class Result:
    """One scored case: ground truth, prediction, and the three verdicts.

    Attributes:
        case_id: Manifest case id.
        entity: Ground-truth subject, copied from the manifest.
        issue: Ground-truth condition label, copied from the manifest.
        prediction: What the system returned, or ``None`` when it failed.
        subject_ok: The subject was right.
        issue_class_ok: The exact issue class was right.
        issue_cat_ok: At least the issue family was right (implied by
            ``issue_class_ok``).
        issue_family: Ground-truth family, for report breakdowns. Derived from
            ``issue`` when not supplied.
        error: Short failure reason. A non-empty value marks the case
            unscoreable: it leaves ``valid_cases`` entirely and is never
            charged as a wrong answer.
    """

    case_id: str
    entity: str
    issue: str
    prediction: Prediction | None = None
    subject_ok: bool = False
    issue_class_ok: bool = False
    issue_cat_ok: bool = False
    issue_family: str = ""
    error: str | None = None

    @property
    def errored(self) -> bool:
        """The case produced nothing scoreable."""
        return bool(self.error)

    @property
    def answered(self) -> bool:
        """The system committed to a usable diagnosis for this case."""
        return bool(self.prediction and self.prediction.answered and not self.errored)

    def family(self) -> str:
        """Ground-truth issue family, falling back to the frozen matcher."""
        return self.issue_family or family_of(self.issue)

    def case_score(self) -> CaseScore:
        """Reduce to the booleans the metrics consume."""
        if self.errored:
            return CaseScore(errored=True)
        return CaseScore(
            answered=self.answered,
            subject_ok=self.subject_ok,
            issue_class_ok=self.issue_class_ok,
            issue_cat_ok=self.issue_cat_ok,
        )

    @classmethod
    def from_dict(cls, row: Any, *, location: str = "result") -> Result:
        """Build from a decoded result row.

        Tolerant about layout, because run files are written by more than one
        generation of tooling: the prediction may sit under ``"prediction"`` or
        be flattened into the row, and the scored flags may sit at the top
        level or under ``"scores"``.

        Raises:
            AdapterError: If the row is not an object or has no ``case_id``.
        """
        if not isinstance(row, Mapping):
            raise AdapterError(f"{location}: expected a JSON object, got {type(row).__name__}")

        case_id = _clean_str(row.get("case_id"))
        if not case_id:
            raise AdapterError(f"{location}: missing case_id")

        error = row.get("error")
        error = _clean_str(error) or None if error is not None else None

        raw_prediction = row.get("prediction")
        if raw_prediction is None and not error:
            # Flat layout: response fields merged into the result row.
            raw_prediction = row
        prediction = (
            Prediction.from_dict(raw_prediction) if isinstance(raw_prediction, Mapping) else None
        )

        scores = row.get("scores") if isinstance(row.get("scores"), Mapping) else row
        return cls(
            case_id=case_id,
            entity=_clean_str(row.get("entity")),
            issue=_clean_str(row.get("issue")),
            prediction=None if error else prediction,
            subject_ok=bool(scores.get("subject_ok")),
            issue_class_ok=bool(scores.get("issue_class_ok")),
            issue_cat_ok=bool(scores.get("issue_cat_ok", scores.get("issue_category_ok"))),
            issue_family=_clean_str(row.get("issue_family")),
            error=error,
        )

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready mapping in a stable key order."""
        return {
            "case_id": self.case_id,
            "entity": self.entity,
            "issue": self.issue,
            "issue_family": self.family(),
            "prediction": self.prediction.to_dict() if self.prediction else None,
            "subject_ok": self.subject_ok,
            "issue_class_ok": self.issue_class_ok,
            "issue_cat_ok": self.issue_cat_ok,
            "error": self.error,
        }


@dataclass(frozen=True, slots=True)
class RunMetadata:
    """What makes a run identifiable and comparable.

    Attributes:
        manifest_id: Suite the run was executed against, e.g.
            ``"field_v1"``.
        manifest_sha256: Fingerprint of that suite's case set. Runs with
            different values measured different things and must not share a
            table.
        system_name: Public name of the system under test.
        system_type: ``"single_call"`` or ``"system"``.
        contract_version: Frozen contract the run was scored under.
        created_at: ISO-8601 timestamp.
        notes: Free-text provenance for a reader.
        extra: Additional non-secret provenance (model id, endpoint host,
            decoding parameters). Never put a credential here — this file is
            published.

    Raises:
        AdapterError: If a required field is empty or ``system_type`` is not
            one of :data:`SYSTEM_TYPES`.
    """

    manifest_id: str
    manifest_sha256: str
    system_name: str
    system_type: str = "single_call"
    contract_version: str = CONTRACT_VERSION
    created_at: str = field(default_factory=utc_now_iso)
    notes: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("manifest_id", "manifest_sha256", "system_name", "contract_version"):
            if not _clean_str(getattr(self, name)):
                raise AdapterError(f"run metadata: {name} must not be empty")
        if self.system_type not in SYSTEM_TYPES:
            raise AdapterError(
                f"run metadata: system_type must be one of {', '.join(SYSTEM_TYPES)}, "
                f"got {self.system_type!r}",
                hint="'single_call' is one model answering directly; 'system' is a composite",
            )
        try:
            datetime.fromisoformat(self.created_at.replace("Z", "+00:00"))
        except (AttributeError, ValueError) as exc:
            raise AdapterError(
                f"run metadata: created_at must be an ISO-8601 timestamp, "
                f"got {self.created_at!r}"
            ) from exc

    @classmethod
    def from_dict(cls, payload: Any) -> RunMetadata:
        """Build from a decoded metadata object.

        Raises:
            AdapterError: If the object is malformed or missing a field.
        """
        if not isinstance(payload, Mapping):
            raise AdapterError(
                f"run metadata must be a JSON object, got {type(payload).__name__}"
            )
        known = {
            "manifest_id",
            "manifest_sha256",
            "system_name",
            "system_type",
            "contract_version",
            "created_at",
            "notes",
            "extra",
        }
        missing = [
            name
            for name in ("manifest_id", "manifest_sha256", "system_name")
            if not _clean_str(payload.get(name))
        ]
        if missing:
            raise AdapterError(
                f"run metadata: missing required field(s): {', '.join(missing)}"
            )
        extra = payload.get("extra")
        extra = dict(extra) if isinstance(extra, Mapping) else {}
        extra.update({key: value for key, value in payload.items() if key not in known})
        return cls(
            manifest_id=_clean_str(payload["manifest_id"]),
            manifest_sha256=_clean_str(payload["manifest_sha256"]),
            system_name=_clean_str(payload["system_name"]),
            system_type=_clean_str(payload.get("system_type")) or "single_call",
            contract_version=_clean_str(payload.get("contract_version")) or CONTRACT_VERSION,
            created_at=_clean_str(payload.get("created_at")) or utc_now_iso(),
            notes=_clean_str(payload.get("notes")) or None,
            extra=extra,
        )

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready mapping in a stable key order."""
        payload: dict[str, Any] = {
            "manifest_id": self.manifest_id,
            "manifest_sha256": self.manifest_sha256,
            "system_name": self.system_name,
            "system_type": self.system_type,
            "contract_version": self.contract_version,
            "created_at": self.created_at,
        }
        if self.notes:
            payload["notes"] = self.notes
        if self.extra:
            payload["extra"] = dict(self.extra)
        return payload


@dataclass(frozen=True, slots=True)
class RunFile:
    """One system's complete result over one suite."""

    metadata: RunMetadata
    results: tuple[Result, ...] = ()
    run_file_version: int = RUN_FILE_VERSION
    path: Path | None = field(default=None, compare=False)

    def __len__(self) -> int:
        return len(self.results)

    def __iter__(self) -> Iterator[Result]:
        return iter(self.results)

    @property
    def system_name(self) -> str:
        """Public name of the system under test."""
        return self.metadata.system_name

    @property
    def system_type(self) -> str:
        """``"single_call"`` or ``"system"``."""
        return self.metadata.system_type

    def by_case_id(self) -> dict[str, Result]:
        """A ``case_id -> Result`` mapping.

        Raises:
            AdapterError: If a case id appears twice.
        """
        index: dict[str, Result] = {}
        for result in self.results:
            if result.case_id in index:
                raise AdapterError(
                    f"run {self.metadata.system_name!r}: duplicate case_id "
                    f"{result.case_id!r}"
                )
            index[result.case_id] = result
        return index

    def case_scores(self) -> tuple[CaseScore, ...]:
        """Every result reduced to the booleans the metrics consume."""
        return tuple(result.case_score() for result in self.results)

    def validate_against(self, manifest: Manifest, *, require_complete: bool = False) -> None:
        """Check this run really describes ``manifest``.

        Args:
            manifest: The suite to check against.
            require_complete: Also require every case to be present. A partial
                run is a valid file and a legitimate smoke test; it is not a
                leaderboard entry.

        Raises:
            ManifestError: On a fingerprint mismatch, an unknown case id, a
                duplicate case id, or — with ``require_complete`` — a gap.
        """
        if self.metadata.manifest_sha256 != manifest.manifest_sha256:
            raise ManifestError(
                f"run {self.metadata.system_name!r} was produced against manifest "
                f"{self.metadata.manifest_sha256[:12]}… but was checked against "
                f"{manifest.manifest_sha256[:12]}… ({manifest.manifest_id})",
                hint="results from different case sets are not comparable",
            )
        index = self.by_case_id()
        unknown = sorted(set(index) - set(manifest.case_ids))
        if unknown:
            raise ManifestError(
                f"run {self.metadata.system_name!r} contains {len(unknown)} case id(s) "
                f"not in manifest {manifest.manifest_id!r} (first: {unknown[0]})"
            )
        if require_complete:
            missing = [cid for cid in manifest.case_ids if cid not in index]
            if missing:
                raise ManifestError(
                    f"run {self.metadata.system_name!r} covers {len(index)} of "
                    f"{len(manifest)} cases; {len(missing)} missing (first: {missing[0]})",
                    hint="a partial run is not a leaderboard entry",
                )

    def rescored(
        self,
        scorer: Callable[[Result], tuple[bool, bool, bool]],
        *,
        contract_version: str | None = None,
    ) -> RunFile:
        """Return a copy with every non-errored result re-judged.

        This is why predictions are stored verbatim: a run recorded under one
        contract can be re-judged under another without re-running the system.

        Args:
            scorer: Called with each non-errored :class:`Result`; returns
                ``(subject_ok, issue_class_ok, issue_cat_ok)``.
            contract_version: Stamp the copy with this contract version.
                Defaults to the current one, since the scores are now that
                contract's, not the original's.
        """
        rescored: list[Result] = []
        for result in self.results:
            if result.errored:
                rescored.append(result)
                continue
            subject_ok, issue_class_ok, issue_cat_ok = scorer(result)
            rescored.append(
                Result(
                    case_id=result.case_id,
                    entity=result.entity,
                    issue=result.issue,
                    prediction=result.prediction,
                    subject_ok=bool(subject_ok),
                    issue_class_ok=bool(issue_class_ok),
                    issue_cat_ok=bool(issue_cat_ok),
                    issue_family=result.issue_family,
                    error=result.error,
                )
            )
        metadata = RunMetadata(
            manifest_id=self.metadata.manifest_id,
            manifest_sha256=self.metadata.manifest_sha256,
            system_name=self.metadata.system_name,
            system_type=self.metadata.system_type,
            contract_version=contract_version or CONTRACT_VERSION,
            created_at=self.metadata.created_at,
            notes=self.metadata.notes,
            extra=dict(self.metadata.extra),
        )
        return RunFile(metadata=metadata, results=tuple(rescored))

    @classmethod
    def from_dict(cls, payload: Any, *, source: str = "run file") -> RunFile:
        """Build from a decoded ``{"metadata": ..., "results": [...]}`` object.

        Raises:
            AdapterError: If the object is malformed.
        """
        if not isinstance(payload, Mapping):
            raise AdapterError(
                f"{source}: expected a JSON object, got {type(payload).__name__}"
            )
        version = payload.get("run_file_version", RUN_FILE_VERSION)
        if not isinstance(version, int) or version > RUN_FILE_VERSION:
            raise AdapterError(
                f"{source}: unsupported run_file_version {version!r}",
                hint=f"this build reads run file v{RUN_FILE_VERSION} and earlier",
            )
        # A run with no results is legitimate (an empty smoke run, or a
        # header-only JSON Lines file), so an absent 'results' key means zero
        # rows rather than a malformed file. A present one must be a list.
        rows = payload.get("results", [])
        if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
            raise AdapterError(f"{source}: 'results' must be a list")
        metadata = RunMetadata.from_dict(payload.get("metadata"))
        results = tuple(
            Result.from_dict(row, location=f"{source}: result {index}")
            for index, row in enumerate(rows)
        )
        return cls(metadata=metadata, results=results, run_file_version=version)

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready mapping in a stable key order."""
        return {
            "run_file_version": self.run_file_version,
            "metadata": self.metadata.to_dict(),
            "results": [result.to_dict() for result in self.results],
        }

    def to_jsonl(self) -> str:
        """Serialise as a metadata header line plus one line per result."""
        lines = [
            json.dumps(
                {"run_file_version": self.run_file_version, "metadata": self.metadata.to_dict()},
                ensure_ascii=False,
                sort_keys=False,
            )
        ]
        lines.extend(
            json.dumps(result.to_dict(), ensure_ascii=False, sort_keys=False)
            for result in self.results
        )
        return "\n".join(lines) + "\n"

    def to_json(self, *, indent: int = 2) -> str:
        """Serialise as a single JSON object."""
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent) + "\n"


def _parse_jsonl_run(text: str, source: str) -> RunFile:
    """Parse the header-line JSON Lines encoding."""
    metadata: RunMetadata | None = None
    results: list[Result] = []
    version = RUN_FILE_VERSION
    for lineno, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        location = f"{source}:{lineno}"
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise AdapterError(f"{location}: invalid JSON: {exc.msg}") from exc
        if not isinstance(row, Mapping):
            raise AdapterError(f"{location}: expected a JSON object")
        if metadata is None and "metadata" in row:
            metadata = RunMetadata.from_dict(row["metadata"])
            raw_version = row.get("run_file_version", RUN_FILE_VERSION)
            if not isinstance(raw_version, int) or raw_version > RUN_FILE_VERSION:
                raise AdapterError(f"{location}: unsupported run_file_version {raw_version!r}")
            version = raw_version
            continue
        if metadata is None and {"manifest_id", "system_name"} <= set(row):
            metadata = RunMetadata.from_dict(row)
            continue
        results.append(Result.from_dict(row, location=location))

    if metadata is None:
        raise AdapterError(
            f"{source}: no metadata header found",
            hint="run file v1 begins with a line carrying a 'metadata' object",
        )
    return RunFile(metadata=metadata, results=tuple(results), run_file_version=version)


def read_run(path: str | Path) -> RunFile:
    """Read a run file, accepting either encoding.

    The content is sniffed rather than trusted to the extension: a single JSON
    object is read as the ``.json`` form, anything else as JSON Lines.

    Raises:
        AdapterError: If the file is missing, unreadable, or malformed.
    """
    source = Path(path)
    try:
        text = source.read_text(encoding="utf-8")
    except OSError as exc:
        raise AdapterError(f"cannot read run file {source}: {exc.strerror or exc}") from exc
    except UnicodeDecodeError as exc:
        raise AdapterError(f"run file {source} is not valid UTF-8: {exc}") from exc

    if not text.strip():
        raise AdapterError(f"run file {source} is empty")

    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        run = _parse_jsonl_run(text, str(source))
    else:
        run = RunFile.from_dict(payload, source=str(source))
    return RunFile(
        metadata=run.metadata,
        results=run.results,
        run_file_version=run.run_file_version,
        path=source,
    )


def write_run(run: RunFile, path: str | Path, *, fmt: str | None = None) -> Path:
    """Write ``run`` to ``path`` atomically.

    Args:
        run: The run to serialise.
        path: Destination. Parent directories are created.
        fmt: ``"jsonl"`` or ``"json"``. Defaults to the path's extension,
            falling back to JSON Lines.

    Returns:
        The path written.

    Raises:
        AdapterError: If ``fmt`` is not a known format.
    """
    destination = Path(path)
    chosen = fmt or ("json" if destination.suffix.lower() == ".json" else "jsonl")
    if chosen not in ("json", "jsonl"):
        raise AdapterError(f"unknown run file format {chosen!r}; expected 'json' or 'jsonl'")
    return atomic_write_text(
        destination, run.to_json() if chosen == "json" else run.to_jsonl()
    )


# --------------------------------------------------------------------------
# historical files
# --------------------------------------------------------------------------


def _legacy_rows(payload: Any, source: str) -> list[Mapping[str, Any]]:
    """Extract the result rows from a historical file."""
    rows = payload.get("results") if isinstance(payload, Mapping) else payload
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
        raise AdapterError(
            f"{source}: expected a list of result rows, or an object with 'results'"
        )
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            raise AdapterError(f"{source}: row {index} is not a JSON object")
    return list(rows)


def _detect_payload_key(rows: Sequence[Mapping[str, Any]], source: str) -> str:
    """Decide which historical payload key holds the system's answers."""
    present = [key for key in LEGACY_PAYLOAD_KEYS if any(key in row for row in rows)]
    if not present:
        raise AdapterError(
            f"{source}: no historical payload key found "
            f"(looked for: {', '.join(LEGACY_PAYLOAD_KEYS)})"
        )
    if len(present) > 1:
        raise AdapterError(
            f"{source}: rows carry more than one payload key ({', '.join(present)})",
            hint="pass payload_key= to choose which system's results to convert",
        )
    return present[0]


def _legacy_prediction(payload: Mapping[str, Any], *, composite: bool) -> Prediction:
    """Map one historical payload onto a :class:`Prediction`.

    Composite (``system``) payloads carry an explicit commit flag, which is
    the system's own decision and is honoured verbatim. Single-call payloads
    carry no such flag, so ``answered`` is derived from the response exactly
    as it is for a live single-call run: a missing, empty, or ``"unknown"``
    category means the model declined.

    Historical files recorded the specific issue string under one of two keys
    depending on the harness generation, and only the *count* of secondary
    findings rather than the findings themselves.
    """
    if composite:
        primary_issue = payload.get("main_issue") or payload.get("main_class")
        primary_category = payload.get("main_category")
        answered: bool | None = bool(payload.get("committed"))
    else:
        primary_issue = payload.get("primary_issue")
        primary_category = payload.get("primary_category")
        answered = None

    raw_count = payload.get("n_secondary")
    return Prediction.from_dict(
        {
            "subject": payload.get("subject"),
            "subject_type": payload.get("subject_type"),
            "primary_issue": primary_issue,
            "primary_category": primary_category,
            "secondary_issues": payload.get("secondary_issues"),
            "secondary_count": raw_count if isinstance(raw_count, int) else None,
        },
        answered=answered,
    )


def read_legacy(
    path: str | Path,
    *,
    system_name: str,
    manifest: Manifest | None = None,
    manifest_id: str | None = None,
    manifest_sha256: str | None = None,
    system_type: str | None = None,
    payload_key: str | None = None,
    case_ids: Sequence[str] | Mapping[str, str] | None = None,
    case_id_prefix: str = "legacy-",
    contract_version: str = CONTRACT_VERSION,
    created_at: str | None = None,
    notes: str | None = None,
) -> RunFile:
    """Convert a historical run file into run file v1.

    Older harnesses wrote one object per case with the system's answers nested
    under a provider-shaped key — ``"ours"`` for a composite system,
    ``"gemini"`` for a single model answering the prompt directly — alongside
    the ground truth and an absolute path to the image. This reads those files
    so previously published results can be re-scored under the current
    contract instead of being taken on trust.

    Field mapping, composite payloads (``"ours"``):

    ==========================  ==================================
    historical                  run file v1
    ==========================  ==================================
    ``committed``               ``prediction.answered``
    ``main_issue``/``main_class``  ``prediction.primary_issue``
    ``main_category``           ``prediction.primary_category``
    ``n_secondary``             ``prediction.secondary_count``
    ==========================  ==================================

    Single-call payloads (``"gemini"``) already use the frozen response field
    names and are copied across directly. They carry no commit flag, so
    ``answered`` is derived from ``primary_category`` — the same rule a live
    single-call run uses, which keeps a converted historical row and a fresh
    row scoring identically.

    A payload containing ``error`` becomes an errored result: excluded from
    ``valid_cases``, never charged as a wrong answer.

    Not carried across, deliberately: the image path (historical files stored
    absolute developer paths), the source file name, and harness-internal
    fields from the original payload. Only ``source_sha256`` is recorded, which
    identifies the input file without describing it.

    Args:
        path: The historical file.
        system_name: Public name for the converted run.
        manifest: Suite the run was produced against. Supplies ``manifest_id``
            and ``manifest_sha256`` when given.
        manifest_id: Suite id, if no manifest is available.
        manifest_sha256: Suite fingerprint, if no manifest is available.
        system_type: Overrides the type inferred from the payload key
            (``"ours"`` → ``"system"``, ``"gemini"`` → ``"single_call"``).
        payload_key: Which key holds the answers. Required when a file carries
            both — several historical files hold two systems side by side.
        case_ids: Case ids for the rows, since historical files have none.
            Either a sequence aligned with the rows, or a mapping keyed by the
            historical ``image`` value or its file name.
        case_id_prefix: Prefix for synthesised ids when ``case_ids`` cannot
            supply one. Ids are positional (``legacy-0000``), which keeps the
            file self-consistent without inventing a claim about which
            manifest case a row was.
        contract_version: Contract version to stamp. Defaults to the current
            one; pass the historical value if the scores are being carried
            over as-is rather than re-scored.
        created_at: ISO-8601 timestamp for the converted run.
        notes: Free-text provenance.

    Returns:
        A run file whose per-case flags are the historical verdicts. Re-judge
        them with :meth:`RunFile.rescored` to score under the current contract.

    Raises:
        AdapterError: If the file is malformed, the payload key is absent or
            ambiguous, or ``case_ids`` does not line up with the rows.
        ManifestError: If neither ``manifest`` nor both ``manifest_id`` and
            ``manifest_sha256`` are given.
    """
    source = Path(path)
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except OSError as exc:
        raise AdapterError(f"cannot read {source}: {exc.strerror or exc}") from exc
    except json.JSONDecodeError as exc:
        raise AdapterError(f"{source}: invalid JSON: {exc.msg}") from exc

    resolved_id = manifest_id or (manifest.manifest_id if manifest else None)
    resolved_sha = manifest_sha256 or (manifest.manifest_sha256 if manifest else None)
    if not resolved_id or not resolved_sha:
        raise ManifestError(
            "converting a historical run needs the suite it was produced against",
            hint="pass manifest=, or both manifest_id= and manifest_sha256=",
        )

    rows = _legacy_rows(payload, str(source))
    key = payload_key or _detect_payload_key(rows, str(source))
    if key not in LEGACY_PAYLOAD_KEYS:
        raise AdapterError(
            f"unknown payload key {key!r}; expected one of {', '.join(LEGACY_PAYLOAD_KEYS)}"
        )
    composite = key == "ours"

    if (
        isinstance(case_ids, Sequence)
        and not isinstance(case_ids, (str, bytes))
        and len(case_ids) != len(rows)
    ):
        raise AdapterError(
            f"{source}: case_ids has {len(case_ids)} entries but the file has "
            f"{len(rows)} rows"
        )

    results: list[Result] = []
    for index, row in enumerate(rows):
        if key not in row:
            raise AdapterError(f"{source}: row {index} has no {key!r} payload")
        case_id = _legacy_case_id(row, index, case_ids, case_id_prefix)
        entity = _clean_str(row.get("entity"))
        issue = _clean_str(row.get("issue"))
        item = row[key]
        if not isinstance(item, Mapping):
            raise AdapterError(f"{source}: row {index} payload is not a JSON object")

        error = _clean_str(item.get("error")) or None
        results.append(
            Result(
                case_id=case_id,
                entity=entity,
                issue=issue,
                prediction=None if error else _legacy_prediction(item, composite=composite),
                subject_ok=bool(item.get("subject_ok")),
                issue_class_ok=bool(item.get("issue_class_ok")),
                issue_cat_ok=bool(item.get("issue_cat_ok", item.get("issue_category_ok"))),
                issue_family=family_of(issue),
                error=error,
            )
        )

    metadata = RunMetadata(
        manifest_id=resolved_id,
        manifest_sha256=resolved_sha,
        system_name=system_name,
        system_type=system_type or ("system" if composite else "single_call"),
        contract_version=contract_version,
        created_at=created_at or utc_now_iso(),
        notes=notes,
        extra={"converted_from": "legacy", "source_sha256": sha256_file(source)},
    )
    if manifest is not None and case_ids is not None:
        run = RunFile(metadata=metadata, results=tuple(results))
        run.validate_against(manifest)
        return run
    return RunFile(metadata=metadata, results=tuple(results))


def _legacy_case_id(
    row: Mapping[str, Any],
    index: int,
    case_ids: Sequence[str] | Mapping[str, str] | None,
    prefix: str,
) -> str:
    """Resolve a case id for one historical row."""
    existing = _clean_str(row.get("case_id"))
    if existing:
        return existing
    if isinstance(case_ids, Mapping):
        image = _clean_str(row.get("image"))
        for candidate in (image, PurePath(image).name if image else ""):
            if candidate and candidate in case_ids:
                return case_ids[candidate]
    elif isinstance(case_ids, Sequence) and not isinstance(case_ids, (str, bytes)):
        return case_ids[index]
    return f"{prefix}{index:04d}"

"""The case loop: run an adapter over a manifest and score as it goes.

Three properties this file exists to guarantee, in order of how much they hurt
when missing.

**One bad case never kills a run.** Every :meth:`~agribench.adapters.base.Adapter.predict`
call is wrapped. A timeout, a 500, a malformed payload, a bug in somebody's
adapter -- all become an error *record*, and the loop moves on. Losing 500
completed cases to the 501st is not an acceptable failure mode when each one
cost real money.

**A crash costs you nothing.** Each result is appended to a JSONL sidecar and
flushed to the OS as it completes; the aggregate JSON is rewritten atomically
via ``os.replace``, so a reader never sees a half-written file. Re-run the same
command and it picks up where it stopped. Interrupting a run with Ctrl-C is
safe and expected.

**Errors are not wrong answers.** Scoring happens inline, per case, through
:mod:`agribench.contract.scoring`, and error rows carry ``error`` so the
contract's metrics drop them from the valid-case denominator. A provider outage
cannot move an accuracy number in either direction. This is also why scoring is
inline rather than a later pass: the scored record is the durable artifact, so
a crashed run is still a partially scored run.

Concurrency is a plain thread pool. The work is IO-bound -- one HTTPS request
per case -- so threads are the right tool and the GIL is not in the way.

What a run leaves on disk::

    runs/my-run.json            aggregate: run metadata + metrics + results
    runs/my-run.records.jsonl   append-only log, one scored record per line

Both are stdlib JSON, both are diffable, and neither contains an absolute path
or a credential.

Stdlib only.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agribench.adapters import Adapter, Case, Prediction
from agribench.contract import CONTRACT_VERSION, compute_metrics
from agribench.contract.scoring import gt_category, score_issue, score_subject
from agribench.record import RUN_FILE_VERSION, SYSTEM_TYPES

__all__ = [
    "MANIFEST_FIELDS",
    "RunResult",
    "iter_records",
    "load_manifest",
    "records_path_for",
    "resolve_image_path",
    "run_benchmark",
    "score_record",
]

#: Fields every manifest row must carry. ``license`` and ``source`` are also
#: present in the shipped manifests but are attribution, not evaluation.
MANIFEST_FIELDS: tuple[str, ...] = (
    "case_id",
    "image",
    "image_sha256",
    "entity",
    "issue",
)

#: Result-record format version. Bumped if the record shape ever changes so an
#: old run file stays readable rather than silently misparsed.
RECORD_SCHEMA_VERSION = 1


# --------------------------------------------------------------------------
# manifest
# --------------------------------------------------------------------------


def load_manifest(path: str | Path, *, limit: int | None = None) -> list[dict[str, Any]]:
    """Read a JSONL manifest into a list of rows.

    Args:
        path: Path to ``manifest.jsonl``.
        limit: Stop after this many rows. Applied to the head of the file, not
            sampled, so ``--limit`` is reproducible and two limited runs of
            different systems cover the same cases.

    Returns:
        Manifest rows in file order.

    Raises:
        FileNotFoundError: If the manifest is missing, with a hint about the
            shipped one.
        ValueError: On malformed JSON, a missing required field, or a duplicate
            ``case_id``. A manifest is the ground truth of the whole exercise;
            silently tolerating a broken one is how you publish a wrong number.
    """
    manifest = Path(path)
    if not manifest.exists():
        raise FileNotFoundError(
            f"manifest not found: {manifest}. The shipped one lives at "
            f"tracks/diagnosis/suites/field_v1/manifest.jsonl"
        )

    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    with manifest.open("r", encoding="utf-8") as handle:
        for lineno, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{manifest.name}:{lineno}: invalid JSON: {exc}") from exc
            if not isinstance(row, dict):
                raise ValueError(
                    f"{manifest.name}:{lineno}: expected a JSON object, "
                    f"got {type(row).__name__}"
                )
            missing = [key for key in MANIFEST_FIELDS if not row.get(key)]
            if missing:
                raise ValueError(
                    f"{manifest.name}:{lineno}: missing required field(s): "
                    f"{', '.join(missing)}"
                )
            case_id = str(row["case_id"])
            if case_id in seen:
                raise ValueError(f"{manifest.name}:{lineno}: duplicate case_id {case_id!r}")
            seen.add(case_id)
            rows.append(row)
            if limit is not None and len(rows) >= limit:
                break
    return rows


def resolve_image_path(images_root: str | Path, relative: str) -> Path:
    """Locate a case image beneath ``images_root``.

    The manifest stores image paths relative to its own directory
    (``images/<sha256>.jpg``). ``--images`` may point either at that parent
    directory or straight at the image directory itself, because both are
    natural things to type; the flat-basename fallback covers the second.

    Args:
        images_root: Directory the manifest's relative paths resolve against.
        relative: The manifest row's ``image`` value.

    Returns:
        The first candidate that exists, or the primary candidate if none do.
        A missing file is not raised here -- an adapter that never opens the
        image (the echo fixture) must still run before the dataset is fetched.
    """
    root = Path(images_root)
    primary = root / relative
    if primary.exists():
        return primary
    flat = root / Path(relative).name
    if flat.exists():
        return flat
    return primary


def case_from_row(row: dict[str, Any], images_root: str | Path) -> Case:
    """Build the ground-truth-free :class:`Case` handed to an adapter."""
    return Case(
        case_id=str(row["case_id"]),
        image_path=str(resolve_image_path(images_root, str(row["image"]))),
        image_sha256=str(row.get("image_sha256") or ""),
    )


# --------------------------------------------------------------------------
# scoring
# --------------------------------------------------------------------------


def score_record(row: dict[str, Any], prediction: Prediction) -> dict[str, Any]:
    """Score one prediction against one manifest row.

    Judgement lives entirely in :mod:`agribench.contract.scoring`; this
    function only assembles the flat record shape that
    :func:`~agribench.contract.metrics.compute_metrics` consumes.

    Note that ``subject_ok`` and ``issue_class_ok`` are computed for declined
    cases too. Their denominator is valid cases, not answered cases, so a
    system that names the crop and then declines to name the disease gets
    credit for the crop -- and a system that names the disease while declining
    to answer does not get precision credit for it, because
    :func:`~agribench.contract.metrics.compute_metrics` counts
    ``issue_category_ok`` only on answered rows.

    Args:
        row: The manifest row, carrying ``entity`` and ``issue``.
        prediction: What the adapter returned.

    Returns:
        A flat record: manifest identity, the prediction fields, and the three
        correctness flags.
    """
    entity = str(row["entity"])
    issue = str(row["issue"])
    class_ok, category_ok = score_issue(
        issue, entity, prediction.primary_issue, prediction.primary_category
    )
    return {
        "case_id": str(row["case_id"]),
        "image": str(row["image"]),
        "image_sha256": str(row.get("image_sha256") or ""),
        "entity": entity,
        "issue": issue,
        "issue_family": row.get("issue_family") or "",
        "gt_category": gt_category(issue),
        **prediction.to_dict(),
        "subject_ok": score_subject(entity, prediction.subject),
        "issue_class_ok": class_ok,
        "issue_category_ok": category_ok,
        "error": None,
    }


def error_record(row: dict[str, Any], error: BaseException) -> dict[str, Any]:
    """Build the record for a case that could not be scored.

    ``error`` is truthy, which is the only thing
    :func:`~agribench.contract.metrics.compute_metrics` needs in order to drop
    the row from the valid-case denominator. Correctness flags are ``False``
    rather than absent so the record shape stays uniform for anyone reading the
    JSONL with a table tool.
    """
    return {
        "case_id": str(row["case_id"]),
        "image": str(row["image"]),
        "image_sha256": str(row.get("image_sha256") or ""),
        "entity": str(row["entity"]),
        "issue": str(row["issue"]),
        "issue_family": row.get("issue_family") or "",
        "gt_category": gt_category(str(row["issue"])),
        "answered": False,
        "subject": "",
        "subject_type": "",
        "primary_issue": "",
        "primary_category": "",
        "secondary_issues": [],
        "subject_ok": False,
        "issue_class_ok": False,
        "issue_category_ok": False,
        "error": f"{type(error).__name__}: {error}"[:500],
    }


# --------------------------------------------------------------------------
# incremental, crash-safe persistence
# --------------------------------------------------------------------------


def records_path_for(out_path: str | Path) -> Path:
    """The JSONL sidecar beside an aggregate run file."""
    out = Path(out_path)
    return out.with_name(f"{out.stem}.records.jsonl")


def iter_records(path: str | Path) -> Iterator[dict[str, Any]]:
    """Yield records from a JSONL sidecar, skipping any truncated final line.

    A process killed mid-write can leave a partial last line. That line is
    dropped rather than raised on: the whole point of the sidecar is that an
    ungraceful exit is recoverable.
    """
    source = Path(path)
    if not source.exists():
        return
    with source.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict) and record.get("case_id"):
                yield record


def load_completed(path: str | Path) -> dict[str, dict[str, Any]]:
    """Read a sidecar into ``case_id -> record``, last write winning."""
    completed: dict[str, dict[str, Any]] = {}
    for record in iter_records(path):
        completed[str(record["case_id"])] = record
    return completed


def atomic_write_text(path: str | Path, text: str) -> None:
    """Write a file so that a reader sees either the old bytes or the new ones.

    Write to a temp file in the same directory, fsync it, then ``os.replace``,
    which is atomic within a filesystem. Without this, a crash during the
    snapshot leaves a truncated JSON file where a valid run used to be -- the
    exact moment you most want the data.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    try:
        with tmp.open("w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, target)
    finally:
        tmp.unlink(missing_ok=True)


class _RecordLog:
    """Append-only, flush-on-write JSONL log guarded by a lock."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = path.open("a", encoding="utf-8")
        self._lock = threading.Lock()

    def append(self, record: dict[str, Any]) -> None:
        line = json.dumps(record, ensure_ascii=False, sort_keys=False)
        with self._lock:
            self._handle.write(line + "\n")
            self._handle.flush()
            os.fsync(self._handle.fileno())

    def close(self) -> None:
        with self._lock:
            self._handle.close()


# --------------------------------------------------------------------------
# the run
# --------------------------------------------------------------------------


@dataclass(slots=True)
class RunResult:
    """Everything a finished (or interrupted) run produced."""

    run: dict[str, Any]
    metrics: dict[str, Any]
    results: list[dict[str, Any]] = field(default_factory=list)
    out_path: Path | None = None
    records_path: Path | None = None
    interrupted: bool = False

    def to_document(self) -> dict[str, Any]:
        """The on-disk aggregate shape.

        Carries two headers on purpose, because two readers consume this file:

        ``run`` / ``metrics``
            the harness's own view, read by ``score``/``report``/``compare``.
        ``run_file_version`` / ``metadata``
            the published run-file v1 header defined in
            :mod:`agribench.record`, so :func:`~agribench.record.read_run`
            can read a file this runner wrote.

        The second header is not decoration. A published result is only
        checkable if a reader can load the evidence with the documented
        reader; a run file the harness can write but its own loader cannot
        read is an unverifiable artifact.
        """
        return {
            "agribench_record_schema": RECORD_SCHEMA_VERSION,
            "run_file_version": RUN_FILE_VERSION,
            "metadata": self.metadata(),
            "run": self.run,
            "metrics": self.metrics,
            "results": self.results,
        }

    def metadata(self) -> dict[str, Any]:
        """The run-file v1 ``metadata`` block for this run.

        ``system_type`` is coerced to a value the reader accepts: a
        third-party adapter can set the attribute to anything, and an
        adapter's typo must not render the run file unreadable.
        """
        system_type = self.run.get("system_type")
        if system_type not in SYSTEM_TYPES:
            system_type = "single_call"
        return {
            "manifest_id": self.run.get("manifest_id") or "unknown",
            "manifest_sha256": self.run.get("manifest_sha256") or "",
            "system_name": self.run.get("label") or "unknown",
            "system_type": system_type,
            "contract_version": self.run.get("contract_version") or CONTRACT_VERSION,
            "created_at": self.run.get("started_at_utc") or "",
            "extra": {
                "adapter": self.run.get("adapter"),
                "manifest": self.run.get("manifest"),
                "case_count": self.run.get("case_count"),
                "completed": self.run.get("completed"),
                "interrupted": self.run.get("interrupted"),
            },
        }


def run_benchmark(
    adapter: Adapter,
    rows: Sequence[dict[str, Any]],
    *,
    images_root: str | Path,
    out_path: str | Path,
    manifest_path: str | Path | None = None,
    workers: int = 4,
    resume: bool = True,
    retry_errors: bool = True,
    snapshot_every: int = 25,
    label: str | None = None,
    progress: Callable[[str], None] | None = None,
) -> RunResult:
    """Run ``adapter`` over ``rows``, scoring and persisting as it goes.

    Args:
        adapter: The system under test. ``setup()`` is called before the loop
            and ``close()`` after it, even if the loop raises.
        rows: Manifest rows from :func:`load_manifest`.
        images_root: Directory the manifest's relative image paths resolve
            against.
        out_path: Aggregate run JSON to write. The JSONL sidecar is derived
            from it by :func:`records_path_for`.
        manifest_path: Recorded in metadata for provenance. Stored as a
            repo-relative path when possible, never as an absolute one.
        workers: Thread pool size. The work is IO-bound. Be considerate of
            rate limits: a provider that 429s under 32 threads will produce a
            slower and more expensive run than one that never does.
        resume: Skip cases already present in the sidecar.
        retry_errors: When resuming, re-attempt cases whose stored record is an
            error. Usually right -- errors are typically transient -- but set
            it ``False`` to freeze a run's error set.
        snapshot_every: Rewrite the aggregate JSON every N completions. The
            sidecar is written on every case regardless; this only controls how
            fresh the pre-aggregated file is.
        label: Display name for reports. Defaults to the adapter's ``name``.
        progress: Called with a one-line status string per completed case.
            ``None`` disables it.

    Returns:
        A :class:`RunResult`. On Ctrl-C, ``interrupted`` is ``True`` and the
        partial results are already on disk.

    Raises:
        AdapterConfigError: From ``adapter.setup()``, before any case runs.
            Configuration errors fail fast rather than 564 times.
    """
    rows = list(rows)
    out = Path(out_path)
    records_file = records_path_for(out)
    started = datetime.now(UTC)

    completed = load_completed(records_file) if resume else {}
    if not resume:
        records_file.unlink(missing_ok=True)
    if retry_errors:
        completed = {
            case_id: record
            for case_id, record in completed.items()
            if not record.get("error")
        }

    pending = [row for row in rows if str(row["case_id"]) not in completed]
    results_by_id: dict[str, dict[str, Any]] = dict(completed)
    stop = threading.Event()
    interrupted = False
    done = 0
    log = _RecordLog(records_file)

    def emit(message: str) -> None:
        if progress is not None:
            progress(message)

    def snapshot(final: bool = False) -> RunResult:
        ordered = [
            results_by_id[str(row["case_id"])]
            for row in rows
            if str(row["case_id"]) in results_by_id
        ]
        result = RunResult(
            run=_run_meta(
                adapter=adapter,
                label=label,
                rows=rows,
                manifest_path=manifest_path,
                workers=workers,
                started=started,
                finished=datetime.now(UTC) if final else None,
                completed=len(ordered),
                resumed=len(completed),
                interrupted=interrupted,
            ),
            metrics=compute_metrics(ordered),
            results=ordered,
            out_path=out,
            records_path=records_file,
            interrupted=interrupted,
        )
        atomic_write_text(
            out, json.dumps(result.to_document(), ensure_ascii=False, indent=2) + "\n"
        )
        return result

    def work(row: dict[str, Any]) -> dict[str, Any]:
        if stop.is_set():
            raise _Cancelled(row["case_id"])
        case = case_from_row(row, images_root)
        began = time.monotonic()
        try:
            prediction = adapter.predict(case)
            record = score_record(row, prediction)
        except _Cancelled:
            raise
        except Exception as exc:  # noqa: BLE001 - see comment
            # Deliberately broad, and the most important line in this file.
            # AdapterError is the documented contract, but a third-party
            # adapter can raise anything at all, and one adapter bug must not
            # destroy a run that has already cost real money. The record keeps
            # the exception type, so a systematic failure shows up in the
            # output rather than being hidden by it.
            record = error_record(row, exc)
        record["latency_ms"] = round((time.monotonic() - began) * 1000.0, 1)
        mode = getattr(adapter, "structured_mode", None)
        if callable(mode):
            record["structured_mode"] = mode()
        return record

    try:
        adapter.setup()
        if not pending:
            emit(f"nothing to do: all {len(results_by_id)} case(s) already recorded")
            return snapshot(final=True)

        emit(
            f"running {len(pending)} case(s) with {workers} worker(s)"
            + (f", resuming {len(completed)} from disk" if completed else "")
        )
        with ThreadPoolExecutor(max_workers=max(1, int(workers))) as pool:
            futures: dict[Future[dict[str, Any]], dict[str, Any]] = {
                pool.submit(work, row): row for row in pending
            }
            try:
                for future in as_completed(futures):
                    try:
                        record = future.result()
                    except _Cancelled:
                        continue
                    results_by_id[record["case_id"]] = record
                    log.append(record)
                    done += 1
                    emit(_progress_line(done, len(pending), record))
                    if snapshot_every and done % snapshot_every == 0:
                        snapshot()
            except KeyboardInterrupt:
                interrupted = True
                stop.set()
                emit("interrupted -- saving what completed; re-run to resume")
                for future in futures:
                    future.cancel()
    finally:
        log.close()
        try:
            adapter.close()
        except Exception as exc:  # noqa: BLE001 - teardown must not mask results
            print(f"agribench: adapter.close() failed: {exc}", file=sys.stderr)

    return snapshot(final=True)


class _Cancelled(Exception):
    """Internal: a queued case that was dropped after an interrupt."""


def _progress_line(done: int, total: int, record: dict[str, Any]) -> str:
    width = len(str(total))
    if record.get("error"):
        status = f"ERROR {record['error'][:60]}"
    elif not record.get("answered"):
        status = "declined"
    elif record.get("issue_category_ok"):
        status = f"ok       {record.get('primary_issue', '')[:40]}"
    else:
        status = f"wrong    {record.get('primary_issue', '')[:40]}"
    return f"[{done:>{width}}/{total}] {record['case_id']}  {status}"


def _run_meta(
    *,
    adapter: Adapter,
    label: str | None,
    rows: Sequence[dict[str, Any]],
    manifest_path: str | Path | None,
    workers: int,
    started: datetime,
    finished: datetime | None,
    completed: int,
    resumed: int,
    interrupted: bool,
) -> dict[str, Any]:
    """Provenance for a run. Portable paths only, secrets never."""
    manifest_id, manifest_sha256 = _suite_identity(manifest_path, rows)
    return {
        "label": label or adapter.name,
        "system_type": adapter.system_type,
        "adapter": adapter.describe(),
        "contract_version": CONTRACT_VERSION,
        "manifest": portable_path(manifest_path) if manifest_path else None,
        "manifest_id": manifest_id,
        "manifest_sha256": manifest_sha256,
        "case_count": len(rows),
        "completed": completed,
        "resumed_from_disk": resumed,
        "workers": int(workers),
        "started_at_utc": started.isoformat(),
        "finished_at_utc": finished.isoformat() if finished else None,
        "interrupted": interrupted,
        "python": f"{sys.version_info.major}.{sys.version_info.minor}",
    }


def _suite_identity(
    manifest_path: str | Path | None, rows: Sequence[dict[str, Any]]
) -> tuple[str, str]:
    """Identify the suite a run was produced against.

    Returns ``(manifest_id, manifest_sha256)``. The fingerprint is taken over
    the WHOLE manifest, not over ``rows``, because ``--limit`` produces a
    partial run of the same suite rather than a different suite. Fingerprinting
    the truncated subset would make every limited run claim its own private
    benchmark and defeat the comparability check that consumes this field.

    Falls back to fingerprinting ``rows`` when the manifest file cannot be
    re-read, and to an empty digest when even that is not possible: missing
    provenance is recorded as missing, never as a plausible-looking value.
    """
    from agribench.manifest import compute_manifest_sha256

    manifest_id = "unknown"
    cases: Sequence[dict[str, Any]] = rows
    if manifest_path is not None:
        path = Path(manifest_path)
        manifest_id = path.parent.name or path.stem
        try:
            cases = load_manifest(path)
        except (FileNotFoundError, ValueError, OSError):
            cases = rows
    try:
        return manifest_id, compute_manifest_sha256(cases)
    except Exception:
        return manifest_id, ""


def portable_path(path: str | Path) -> str:
    """Render a path for publication: relative to the working directory if it
    can be, otherwise bare filename.

    Run artifacts are meant to be committed and shared. A home directory in a
    published JSON file leaks a username at best, and an internal directory
    layout at worst.
    """
    candidate = Path(path)
    try:
        return candidate.resolve().relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        return candidate.name

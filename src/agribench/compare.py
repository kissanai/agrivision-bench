"""Leaderboards: several runs, one table, or a clear refusal.

The value of a comparison table is entirely in what it refuses to put in the
same table. Two systems are comparable only if they answered the same
questions and were graded by the same rules, so this module checks both before
it will emit anything:

* **Same case set.** Every run must carry the same ``manifest_sha256``. A
  differing fingerprint means a case was added, removed, or repointed at
  different image bytes, and the rows are measuring different populations —
  :class:`~agribench.errors.ManifestError`, with the offending runs named.
* **Same rules.** Every run must carry the same ``contract_version``. A
  differing version means the prompt, schema, or matcher moved underneath the
  numbers — :class:`~agribench.errors.ContractDriftError`.

Neither check has an override flag. A flag would be used, and a table with a
footnote saying "these two rows are not comparable" is a table that will be
screenshotted without the footnote.

Ranking is by precision, descending — but the table always carries coverage
beside it, and a run with nothing answered has no precision and sorts last
rather than sorting first on a null. ``single_call`` and ``system`` entries
share a table on purpose: the interesting comparison is between a model
answering directly and a composite that can decline. Which is which is always
printed.

Three artifacts out: JSON (source of truth), CSV (spreadsheets), Markdown
(what people read).

Stdlib only.
"""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

from .contract.metrics import METRIC_DEFINITIONS, compute
from .errors import AdapterError, ContractDriftError, ManifestError
from .record import RunFile, atomic_write_text, utc_now_iso

__all__ = [
    "LEADERBOARD_VERSION",
    "ROW_FIELDS",
    "build_leaderboard",
    "compare_runs",
    "render_csv",
    "render_markdown",
    "write_leaderboard",
]

#: Bumped only if the leaderboard JSON changes shape incompatibly.
LEADERBOARD_VERSION = 1

#: CSV columns, in order.
ROW_FIELDS: tuple[str, ...] = (
    "rank",
    "system_name",
    "system_type",
    "valid_cases",
    "errors",
    "answered",
    "precision",
    "coverage",
    "wrong_pct",
    "subject_accuracy",
    "issue_class_accuracy",
)


def _pct(value: float | None) -> str:
    """Render a ratio for display: one decimal place, ``n/a`` when undefined."""
    return "n/a" if value is None else f"{value * 100:.1f}%"


def _check_comparable(runs: Sequence[RunFile]) -> None:
    """Refuse to build a table out of runs that do not belong in one.

    Raises:
        ManifestError: If the runs used different case sets.
        ContractDriftError: If the runs were scored under different contracts.
        AdapterError: If two runs share a system name.
    """
    fingerprints: dict[str, list[str]] = {}
    for run in runs:
        fingerprints.setdefault(run.metadata.manifest_sha256, []).append(
            run.metadata.system_name
        )
    if len(fingerprints) > 1:
        detail = "; ".join(
            f"{sha[:12]}… ({', '.join(sorted(names))})"
            for sha, names in sorted(fingerprints.items())
        )
        raise ManifestError(
            f"cannot compare runs produced against {len(fingerprints)} different case "
            f"sets: {detail}",
            hint=(
                "every run in a table must carry the same manifest_sha256; re-run the "
                "outliers against the same suite instead of merging the tables"
            ),
        )

    suites: dict[str, list[str]] = {}
    for run in runs:
        suites.setdefault(run.metadata.manifest_id, []).append(run.metadata.system_name)
    if len(suites) > 1:
        detail = "; ".join(
            f"{suite} ({', '.join(sorted(names))})" for suite, names in sorted(suites.items())
        )
        raise ManifestError(
            f"runs share a case-set fingerprint but disagree on the suite name: {detail}",
            hint="one of these runs was labelled with the wrong manifest_id",
        )

    contracts: dict[str, list[str]] = {}
    for run in runs:
        contracts.setdefault(run.metadata.contract_version, []).append(
            run.metadata.system_name
        )
    if len(contracts) > 1:
        detail = "; ".join(
            f"{version} ({', '.join(sorted(names))})"
            for version, names in sorted(contracts.items())
        )
        raise ContractDriftError(
            f"cannot compare runs scored under {len(contracts)} different contract "
            f"versions: {detail}",
            hint=(
                "the prompt, schema, or matcher differs between these runs; re-score "
                "the older ones under the current contract before tabling them"
            ),
        )

    names = [run.metadata.system_name for run in runs]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise AdapterError(
            f"duplicate system name(s) in the comparison: {', '.join(duplicates)}",
            hint="each row must name a distinct system; the same file was probably passed twice",
        )


def _sort_key(row: dict[str, Any]) -> tuple[int, float, float, str]:
    """Precision desc, then coverage desc, then name. Undefined sorts last."""
    precision = row["precision"]
    coverage = row["coverage"]
    return (
        1 if precision is None else 0,
        -(precision or 0.0),
        -(coverage or 0.0),
        row["system_name"],
    )


def build_leaderboard(
    runs: Iterable[RunFile],
    *,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Build a leaderboard from two or more comparable runs.

    Args:
        runs: The runs to table. Order is irrelevant; rows are ranked.
        generated_at: ISO-8601 timestamp to stamp. Defaults to now. Pass a
            fixed value for byte-reproducible output.

    Returns:
        A JSON-ready leaderboard: the shared suite and contract, one ranked
        row per run, and the metric definitions.

    Raises:
        ManifestError: If the runs used different case sets.
        ContractDriftError: If the runs were scored under different contracts.
        AdapterError: If no runs were given, or two share a system name.
    """
    materialised = list(runs)
    if not materialised:
        raise AdapterError("cannot build a leaderboard from zero runs")
    _check_comparable(materialised)

    rows: list[dict[str, Any]] = []
    for run in materialised:
        metrics = compute(run.case_scores())
        rows.append(
            {
                "system_name": run.metadata.system_name,
                "system_type": run.metadata.system_type,
                "cases_in_run": len(run),
                "valid_cases": metrics.valid_cases,
                "errors": metrics.errored_cases,
                "answered": metrics.answered,
                "correct_category": metrics.correct_category,
                "precision": metrics.precision,
                "coverage": metrics.coverage,
                "wrong_pct": metrics.wrong_pct,
                "subject_accuracy": metrics.subject_accuracy,
                "issue_class_accuracy": metrics.issue_class_accuracy,
                "run_created_at": run.metadata.created_at,
            }
        )

    rows.sort(key=_sort_key)
    for rank, row in enumerate(rows, start=1):
        row["rank"] = rank

    reference = materialised[0].metadata
    case_counts = {row["cases_in_run"] for row in rows}
    return {
        "leaderboard_version": LEADERBOARD_VERSION,
        "generated_at": generated_at or utc_now_iso(),
        "suite": {
            "manifest_id": reference.manifest_id,
            "manifest_sha256": reference.manifest_sha256,
        },
        "contract_version": reference.contract_version,
        "ranked_by": "precision",
        "systems": len(rows),
        "equal_case_counts": len(case_counts) == 1,
        "rows": [{"rank": row.pop("rank"), **row} for row in rows],
        "metric_definitions": dict(METRIC_DEFINITIONS),
    }


def render_csv(board: dict[str, Any]) -> str:
    """Render a leaderboard as CSV with :data:`ROW_FIELDS` columns."""
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=list(ROW_FIELDS), lineterminator="\n")
    writer.writeheader()
    for row in board["rows"]:
        writer.writerow({field: row.get(field, "") for field in ROW_FIELDS})
    return buffer.getvalue()


def render_markdown(board: dict[str, Any]) -> str:
    """Render a leaderboard as Markdown."""
    suite = board["suite"]
    lines: list[str] = [
        f"# Leaderboard — {suite['manifest_id']}",
        "",
        f"{board['systems']} systems · contract `{board['contract_version']}` · "
        f"manifest `{suite['manifest_sha256'][:16]}…` · generated {board['generated_at']}",
        "",
        "Ranked by precision. Coverage is beside it because precision alone is",
        "gameable by abstaining harder; read the pair.",
        "",
        "| rank | system | type | valid n | errors | precision | coverage | "
        "wrong of all | subject acc. | issue class acc. |",
        "| ---: | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in board["rows"]:
        lines.append(
            f"| {row['rank']} | {row['system_name']} | `{row['system_type']}` | "
            f"{row['valid_cases']} | {row['errors']} | "
            f"**{_pct(row['precision'])}** | {_pct(row['coverage'])} | "
            f"{_pct(row['wrong_pct'])} | {_pct(row['subject_accuracy'])} | "
            f"{_pct(row['issue_class_accuracy'])} |"
        )

    if not board["equal_case_counts"]:
        counts = ", ".join(
            f"{row['system_name']}: {row['cases_in_run']}" for row in board["rows"]
        )
        lines += [
            "",
            f"> **Partial runs in this table.** Case counts differ ({counts}). Rows are "
            "comparable only over the cases each system actually attempted; treat this "
            "as provisional.",
        ]

    lines += [
        "",
        "## Metric definitions",
        "",
        "| metric | definition |",
        "| --- | --- |",
    ]
    lines.extend(
        f"| `{name}` | {definition} |" for name, definition in board["metric_definitions"].items()
    )
    lines += [
        "",
        "`single_call` is one model answering the frozen prompt directly; `system` is a",
        "composite that may decline a case. Errors are excluded from every denominator",
        "and are never scored as wrong answers.",
        "",
    ]
    return "\n".join(lines)


def write_leaderboard(
    board: dict[str, Any],
    *,
    json_path: str | Path,
    csv_path: str | Path,
    md_path: str | Path,
) -> tuple[Path, Path, Path]:
    """Write a leaderboard to JSON, CSV, and Markdown.

    Returns:
        ``(json_path, csv_path, md_path)`` as written.
    """
    written_json = atomic_write_text(
        json_path, json.dumps(board, ensure_ascii=False, indent=2) + "\n"
    )
    written_csv = atomic_write_text(csv_path, render_csv(board))
    written_md = atomic_write_text(md_path, render_markdown(board))
    return written_json, written_csv, written_md


def compare_runs(
    runs: Iterable[RunFile],
    out_dir: str | Path,
    *,
    stem: str = "leaderboard",
    generated_at: str | None = None,
) -> tuple[Path, Path, Path]:
    """Build and write a leaderboard for several runs.

    Args:
        runs: The runs to compare.
        out_dir: Directory for the three artifacts; created if needed.
        stem: File stem shared by all three.
        generated_at: Fixed timestamp, for reproducible output.

    Returns:
        ``(json_path, csv_path, md_path)``.

    Raises:
        ManifestError: If the runs used different case sets.
        ContractDriftError: If the runs were scored under different contracts.
        AdapterError: If no runs were given, or two share a system name.
    """
    board = build_leaderboard(runs, generated_at=generated_at)
    base = Path(out_dir) / stem
    return write_leaderboard(
        board,
        json_path=base.with_suffix(".json"),
        csv_path=base.with_suffix(".csv"),
        md_path=base.with_suffix(".md"),
    )

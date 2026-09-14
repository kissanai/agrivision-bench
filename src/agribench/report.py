"""Single-run reports: JSON for machines, Markdown for people.

One run file in, two artifacts out. The JSON is the source of truth for
anything downstream; the Markdown is what a reader actually looks at, and it
is written to be safe to paste somewhere without a caption.

Three rules, inherited from ``docs/METRICS.md`` and enforced here rather than
left to the writer:

* **Precision never appears without coverage.** Both are in every table this
  module emits, adjacent, always. A precision figure alone is trivially gamed
  by abstaining harder and means nothing on its own.
* **Undefined is ``n/a``, not zero.** With nothing answered there is no
  precision to report; ``0.0%`` would read as "always wrong" and ``100%`` as
  "never wrong", and both are false.
* **Round only for display.** Every ratio is stored at full precision in the
  JSON and rounded to one decimal place at the moment it is rendered.

The breakdown is by ``issue_family`` — the coarse kind of problem (disease,
pest, …). It is the split that tells a reader where a system is strong, and it
is computed from ground truth, never from what the system predicted.

Stdlib only.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .contract.metrics import METRIC_DEFINITIONS, Metrics, compute, compute_by_group
from .manifest import Manifest
from .record import RunFile, atomic_write_text, utc_now_iso

__all__ = [
    "REPORT_VERSION",
    "build_report",
    "render_markdown",
    "report_run",
    "write_report",
]

#: Bumped only if the report JSON changes shape incompatibly.
REPORT_VERSION = 1

_FAMILY_UNKNOWN = "unknown"


def _pct(value: float | None) -> str:
    """Render a ratio for display: one decimal place, ``n/a`` when undefined."""
    return "n/a" if value is None else f"{value * 100:.1f}%"


def _family_of_result(result: Any, manifest: Manifest | None) -> str:
    """Ground-truth issue family for one result.

    Prefers what the run recorded, then the manifest, then the frozen
    matcher's derivation from the label. Never the prediction.
    """
    if result.issue_family:
        return result.issue_family
    if manifest is not None and result.case_id in manifest:
        family = manifest.get(result.case_id).issue_family
        if family:
            return family
    return result.family() or _FAMILY_UNKNOWN


def build_report(
    run: RunFile,
    *,
    manifest: Manifest | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Compute a single-run report.

    Args:
        run: The run to summarise.
        manifest: The suite it was produced against. Optional; when given it
            supplies issue families for runs that did not record them, and the
            report states how many of the suite's cases the run covers.
        generated_at: ISO-8601 timestamp to stamp. Defaults to now. Pass a
            fixed value for byte-reproducible output.

    Returns:
        A JSON-ready report: provenance, overall metrics, per-family metrics,
        and the metric definitions themselves, so the artifact always carries
        the meaning of the numbers it shows.

    Raises:
        ManifestError: If ``manifest`` is given and the run was produced
            against a different case set.
    """
    if manifest is not None:
        run.validate_against(manifest)

    scores = run.case_scores()
    overall: Metrics = compute(scores)
    by_family = compute_by_group(
        (_family_of_result(result, manifest), result.case_score()) for result in run
    )

    report: dict[str, Any] = {
        "report_version": REPORT_VERSION,
        "generated_at": generated_at or utc_now_iso(),
        "system": {
            "name": run.metadata.system_name,
            "type": run.metadata.system_type,
        },
        "suite": {
            "manifest_id": run.metadata.manifest_id,
            "manifest_sha256": run.metadata.manifest_sha256,
            "cases_in_run": len(run),
        },
        "contract_version": run.metadata.contract_version,
        "run_created_at": run.metadata.created_at,
        "overall": overall.to_dict(),
        "by_issue_family": {family: value.to_dict() for family, value in by_family.items()},
        "metric_definitions": dict(METRIC_DEFINITIONS),
    }
    if manifest is not None:
        report["suite"]["cases_in_suite"] = len(manifest)
        report["suite"]["complete"] = len(run) == len(manifest)
    if run.metadata.notes:
        report["notes"] = run.metadata.notes
    if run.metadata.extra:
        report["system_details"] = dict(run.metadata.extra)
    return report


def _metric_rows(metrics: dict[str, Any]) -> list[tuple[str, str]]:
    """Label/value pairs for the overall metrics table."""
    return [
        ("valid cases", str(metrics["valid_cases"])),
        ("errors", str(metrics["errored_cases"])),
        ("answered", str(metrics["answered"])),
        ("abstained", str(metrics["valid_cases"] - metrics["answered"])),
        ("precision", f"**{_pct(metrics['precision'])}**"),
        ("coverage", f"**{_pct(metrics['coverage'])}**"),
        ("wrong of all valid", _pct(metrics["wrong_pct"])),
        ("subject accuracy", _pct(metrics["subject_accuracy"])),
        ("issue class accuracy", _pct(metrics["issue_class_accuracy"])),
    ]


def render_markdown(report: dict[str, Any]) -> str:
    """Render a report built by :func:`build_report` as Markdown."""
    system = report["system"]
    suite = report["suite"]
    overall = report["overall"]

    lines: list[str] = [
        f"# {system['name']} — {suite['manifest_id']}",
        "",
        f"`{system['type']}` · {overall['valid_cases']} valid cases · "
        f"{overall['errored_cases']} errors",
        "",
        "| | |",
        "| --- | --- |",
        f"| system | {system['name']} |",
        f"| type | `{system['type']}` |",
        f"| suite | `{suite['manifest_id']}` |",
        f"| manifest sha256 | `{suite['manifest_sha256'][:16]}…` |",
        f"| contract version | `{report['contract_version']}` |",
        f"| cases in run | {suite['cases_in_run']}"
        + (f" of {suite['cases_in_suite']}" if "cases_in_suite" in suite else "")
        + " |",
        f"| run created | {report['run_created_at']} |",
        f"| report generated | {report['generated_at']} |",
        "",
        "## Overall",
        "",
        "| metric | value |",
        "| --- | ---: |",
    ]
    lines.extend(f"| {label} | {value} |" for label, value in _metric_rows(overall))

    lines += [
        "",
        "## By issue family",
        "",
        "Ground-truth families, from the manifest. Precision is over answered cases;",
        "every other column is over valid cases.",
        "",
        "| issue family | valid n | errors | answered | precision | coverage | "
        "wrong of all | subject acc. | issue class acc. |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for family, metrics in report["by_issue_family"].items():
        lines.append(
            f"| {family} | {metrics['valid_cases']} | {metrics['errored_cases']} | "
            f"{metrics['answered']} | **{_pct(metrics['precision'])}** | "
            f"{_pct(metrics['coverage'])} | {_pct(metrics['wrong_pct'])} | "
            f"{_pct(metrics['subject_accuracy'])} | "
            f"{_pct(metrics['issue_class_accuracy'])} |"
        )

    if report.get("notes"):
        lines += ["", "## Notes", "", report["notes"]]

    lines += [
        "",
        "## Metric definitions",
        "",
        "| metric | definition |",
        "| --- | --- |",
    ]
    lines.extend(
        f"| `{name}` | {definition} |" for name, definition in report["metric_definitions"].items()
    )
    lines += [
        "",
        "Errors are excluded from every denominator and never scored as wrong. An",
        "abstention is a valid case the system declined: it lowers coverage, not",
        "precision. Read precision and coverage together — neither means anything",
        "alone.",
        "",
    ]
    return "\n".join(lines)


def write_report(
    report: dict[str, Any],
    *,
    json_path: str | Path,
    md_path: str | Path,
) -> tuple[Path, Path]:
    """Write a report to JSON and Markdown. Returns the two paths written."""
    written_json = atomic_write_text(
        json_path, json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    )
    written_md = atomic_write_text(md_path, render_markdown(report))
    return written_json, written_md


def report_run(
    run: RunFile,
    out_dir: str | Path,
    *,
    stem: str | None = None,
    manifest: Manifest | None = None,
    generated_at: str | None = None,
) -> tuple[Path, Path]:
    """Build and write a report for one run.

    Args:
        run: The run to report on.
        out_dir: Directory for the two artifacts; created if needed.
        stem: File stem. Defaults to a slug of the system name.
        manifest: The suite, for family lookup and completeness.
        generated_at: Fixed timestamp, for reproducible output.

    Returns:
        ``(json_path, md_path)``.
    """
    report = build_report(run, manifest=manifest, generated_at=generated_at)
    base = Path(out_dir) / (stem or _slug(run.metadata.system_name))
    return write_report(
        report, json_path=base.with_suffix(".json"), md_path=base.with_suffix(".md")
    )


def _slug(name: str) -> str:
    """Filesystem-safe stem from a system name."""
    cleaned = [char.lower() if char.isalnum() else "-" for char in name.strip()]
    slug = "".join(cleaned).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug or "run"

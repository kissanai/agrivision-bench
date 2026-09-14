"""Command line interface.

Eight commands, each doing one thing::

    run                run a system over the manifest and score it
    score              recompute metrics from an existing run
    report             render one run as markdown, CSV, or JSON
    compare            rank several runs side by side
    verify-contract    check the frozen zone against contract.lock.json
    validate-manifest  check a dataset manifest for defects
    stage              materialise bundled images from a local directory
    fetch              download link-only images from their source URLs

``stage`` and ``fetch`` exist because this repository ships a manifest, not a
pile of photographs. Bundled cases carry a redistributable licence and are
staged from wherever you already have them; link-only cases carry a URL and are
downloaded from the origin by whoever runs the benchmark. Both verify every
file against its recorded SHA-256, so neither path can quietly score the wrong
bytes.

The shortest useful invocation needs no credentials and no dataset::

    python -m agribench run --limit 20

That uses the offline echo fixture, so it proves the plumbing works before
anyone spends a cent. Swap ``--adapter`` for a real system when ready.

Design notes
------------
``run`` writes as it goes and resumes by default, so Ctrl-C is safe and a
half-finished run is a valid artifact. ``score`` and ``report`` never call a
model; they read a run file. That separation is deliberate -- re-scoring frozen
predictions after a matcher change is how you tell "the model improved" from
"the ruler moved", and it costs nothing to re-run.

Exit codes: ``0`` success, ``1`` a checked failure (contract drift, an invalid
manifest, a run that produced nothing), ``2`` a usage error from argparse.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from agribench import __version__
from agribench.adapters import AdapterError, available_adapters, load_adapter
from agribench.contract import CONTRACT_VERSION, compute_metrics
from agribench.contract.matching import family_of
from agribench.errors import ManifestError
from agribench.manifest import parse_manifest
from agribench.runner import (
    MANIFEST_FIELDS,
    iter_records,
    load_manifest,
    portable_path,
    records_path_for,
    resolve_image_path,
    run_benchmark,
    score_record,
)

__all__ = ["main", "build_parser"]

DEFAULT_MANIFEST = Path("tracks/diagnosis/suites/field_v1/manifest.jsonl")
DEFAULT_OUT_DIR = Path("runs")

#: Column order shared by the report and compare tables.
METRIC_COLUMNS = (
    "n",
    "answered",
    "precision",
    "coverage",
    "wrong_pct",
    "subject_accuracy",
    "issue_class_accuracy",
    "errors",
)


# --------------------------------------------------------------------------
# argument parsing
# --------------------------------------------------------------------------


def _adapter_option(text: str) -> tuple[str, Any]:
    """Parse one ``key=value`` adapter option.

    The value is JSON-decoded when it parses as JSON, so numbers, booleans, and
    whole objects work (``-O max_retries=6``, ``-O map='{"subject":"crop"}'``).
    Anything else stays a string, so ``-O model=vendor/name`` needs no quoting
    gymnastics.
    """
    key, sep, raw = text.partition("=")
    if not sep or not key.strip():
        raise argparse.ArgumentTypeError(
            f"adapter option must be key=value, got {text!r}"
        )
    try:
        value: Any = json.loads(raw)
    except json.JSONDecodeError:
        value = raw
    return key.strip(), value


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level parser with every subcommand attached."""
    parser = argparse.ArgumentParser(
        prog="agribench",
        description=(
            "An open benchmark for agricultural image diagnosis systems, "
            "scored on precision under abstention."
        ),
        epilog=(
            "Start here, no credentials needed:\n"
            "  python -m agribench run --limit 20\n"
            "\n"
            "Then point it at a real system:\n"
            "  BENCH_BASE_URL=... BENCH_MODEL=... BENCH_API_KEY=... \\\n"
            "    python -m agribench run --adapter openai --out runs/mine.json\n"
            "  python -m agribench report --run runs/mine.json\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    # Both numbers matter and they move independently. The package version
    # tracks the tooling; the contract version tracks the prompt, schema,
    # matcher and metric formulas. Results are comparable across package
    # versions but never across contract versions, so a reported result should
    # always cite the contract.
    parser.add_argument(
        "--version",
        action="version",
        version=f"agribench {__version__} (contract v{CONTRACT_VERSION})",
    )
    subparsers = parser.add_subparsers(dest="command", metavar="<command>")

    _add_run(subparsers)
    _add_score(subparsers)
    _add_report(subparsers)
    _add_compare(subparsers)
    _add_verify_contract(subparsers)
    _add_validate_manifest(subparsers)
    _add_stage(subparsers)
    _add_fetch(subparsers)
    return parser


def _add_run(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "run",
        help="run a diagnosis system over the manifest and score it",
        description=(
            "Run an adapter over every case, scoring inline and saving "
            "incrementally. Safe to interrupt: re-run the same command to "
            "resume from where it stopped."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--adapter",
        default="echo",
        metavar="NAME|module:Class",
        help=f"built-in ({', '.join(available_adapters()[:3])}, ...) or import path",
    )
    parser.add_argument(
        "-O",
        "--adapter-option",
        dest="adapter_options",
        action="append",
        default=[],
        type=_adapter_option,
        metavar="KEY=VALUE",
        help="adapter constructor option; repeatable. Values may be JSON.",
    )
    parser.add_argument(
        "--manifest", type=Path, default=DEFAULT_MANIFEST, help="dataset manifest"
    )
    parser.add_argument(
        "--images",
        type=Path,
        default=None,
        metavar="DIR",
        help="image root (default: the manifest's directory)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        metavar="FILE",
        help="run JSON to write (default: runs/<adapter>.json)",
    )
    parser.add_argument("--workers", type=int, default=4, help="concurrent requests")
    parser.add_argument(
        "--limit", type=int, default=None, metavar="N", help="first N cases only"
    )
    parser.add_argument(
        "--label", default=None, help="display name in reports (default: adapter name)"
    )
    parser.add_argument(
        "--system-type",
        choices=("single_call", "system"),
        default=None,
        help="override how the run is classified in tables",
    )
    parser.add_argument(
        "--no-resume",
        dest="resume",
        action="store_false",
        help="ignore and overwrite any existing partial run",
    )
    parser.add_argument(
        "--no-retry-errors",
        dest="retry_errors",
        action="store_false",
        help="when resuming, keep stored errors instead of re-attempting them",
    )
    parser.add_argument(
        "--snapshot-every",
        type=int,
        default=25,
        metavar="N",
        help="rewrite the aggregate JSON every N cases",
    )
    parser.add_argument("--quiet", action="store_true", help="suppress progress output")
    parser.add_argument(
        "--allow-missing-images",
        action="store_true",
        help=(
            "run even though some case images are not staged. The result will "
            "not be a valid suite score; use only for harness debugging"
        ),
    )
    parser.set_defaults(func=cmd_run, resume=True, retry_errors=True)


def _add_score(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "score",
        help="recompute metrics from an existing run",
        description=(
            "Recompute metrics without calling any model. With --manifest, "
            "predictions are re-scored from scratch against ground truth using "
            "the current matcher -- the way to tell whether a number moved "
            "because the system changed or because the ruler did."
        ),
    )
    parser.add_argument("--run", required=True, type=Path, help="run JSON or records JSONL")
    parser.add_argument(
        "--manifest",
        type=Path,
        default=None,
        help="re-score stored predictions against this manifest",
    )
    parser.add_argument("--out", type=Path, default=None, help="write JSON here")
    parser.add_argument(
        "--json", action="store_true", help="print JSON instead of a table"
    )
    parser.set_defaults(func=cmd_score)


def _add_report(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "report",
        help="render one run as markdown, CSV, or JSON",
        description="Render a single run, optionally broken down by issue family.",
    )
    parser.add_argument("--run", required=True, type=Path, help="run JSON")
    parser.add_argument(
        "--format", choices=("md", "json", "csv"), default="md", help="output format"
    )
    parser.add_argument(
        "--by",
        choices=("none", "family", "entity"),
        default="family",
        help="add a per-group breakdown",
    )
    parser.add_argument("--out", type=Path, default=None, help="write here (default: stdout)")
    parser.set_defaults(func=cmd_report)


def _add_compare(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "compare",
        help="rank several runs side by side",
        description=(
            "Compare runs on the same benchmark. Precision is printed beside "
            "coverage and wrong_pct, always: a system that answers 3 cases "
            "perfectly outranks everything on precision alone, and that is not "
            "a result."
        ),
    )
    parser.add_argument(
        "--run",
        dest="runs",
        required=True,
        action="append",
        type=Path,
        metavar="FILE",
        help="run JSON; repeat for each system",
    )
    parser.add_argument(
        "--format", choices=("md", "json", "csv"), default="md", help="output format"
    )
    parser.add_argument(
        "--sort",
        choices=("precision", "coverage", "wrong_pct", "issue_class_accuracy", "label"),
        default="precision",
        help="ranking key",
    )
    parser.add_argument("--out", type=Path, default=None, help="write here (default: stdout)")
    parser.set_defaults(func=cmd_compare)


def _add_verify_contract(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "verify-contract",
        help="check the frozen zone against contract.lock.json",
        description=(
            "The prompt, schema, matcher, and metric formulas together define "
            "the task. If any byte changed, results from before and after are "
            "not comparable and must not share a table. Exit 1 on drift."
        ),
    )
    parser.add_argument(
        "--lock", type=Path, default=None, help="lock file (default: the packaged one)"
    )
    parser.set_defaults(func=cmd_verify_contract)


def _add_validate_manifest(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "validate-manifest",
        help="check a dataset manifest for defects",
        description=(
            "Structural checks always; image presence with --images; byte "
            "verification with --check-hashes. Also reports cases the matcher "
            "can never score, which are worth knowing about before quoting a "
            "number derived from them."
        ),
    )
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST, help="manifest")
    parser.add_argument(
        "--images", type=Path, default=None, metavar="DIR", help="verify images exist here"
    )
    parser.add_argument(
        "--check-hashes",
        action="store_true",
        help="also verify each image's sha256 (slow; implies --images)",
    )
    parser.add_argument(
        "--limit", type=int, default=None, metavar="N", help="first N rows only"
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="fail on warnings too, not just structural errors",
    )
    parser.add_argument("--json", action="store_true", help="print JSON instead of text")
    parser.set_defaults(func=cmd_validate_manifest)


def _add_stage(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "stage",
        help="materialise suite images from a local directory",
        description=(
            "This repository ships a manifest, not the images. Point --from at "
            "any directory that contains them and they are copied into the "
            "content-addressed layout the manifest expects. Files are matched "
            "by SHA-256, never by name, so a renamed or reorganised source "
            "tree works and a re-encoded or truncated file is rejected."
        ),
    )
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST, help="manifest")
    parser.add_argument(
        "--from", dest="source", type=Path, default=None, metavar="DIR",
        help="directory to search for the image bytes",
    )
    parser.add_argument(
        "--images", type=Path, default=None, metavar="DIR",
        help="destination (default: <manifest dir>/images)",
    )
    parser.add_argument(
        "--move", action="store_true", help="move instead of copy (destructive to --from)"
    )
    parser.add_argument(
        "--audit", action="store_true",
        help="report what is present without copying anything",
    )
    parser.add_argument(
        "--deep", action="store_true", help="re-hash files already staged"
    )
    parser.set_defaults(func=cmd_stage)


def _add_fetch(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "fetch",
        help="download link-only case images from their source URLs",
        description=(
            "Some cases reference an image by URL instead of shipping it, "
            "because the most realistic field photographs are usually the ones "
            "nobody has licensed for redistribution. This downloads them from "
            "the origin, under whatever terms that origin sets, and verifies "
            "each against its recorded digest. Use --pin when authoring new "
            "cases to record the digests for the first time."
        ),
    )
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST, help="manifest")
    parser.add_argument(
        "--images", type=Path, default=None, metavar="DIR",
        help="destination (default: <manifest dir>/images)",
    )
    parser.add_argument(
        "--pin", action="store_true",
        help="write observed digests back into the manifest for unpinned cases",
    )
    parser.add_argument(
        "--timeout", type=float, default=30.0, metavar="SEC", help="per-request timeout"
    )
    parser.add_argument(
        "--retries", type=int, default=2, metavar="N", help="attempts after a failure"
    )
    parser.add_argument(
        "--pause", type=float, default=0.5, metavar="SEC",
        help="delay between downloads; origins are often small institutional hosts",
    )
    parser.add_argument(
        "--recheck", action="store_true",
        help="re-verify images already on disk instead of skipping them",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="with --pin, report without writing"
    )
    parser.add_argument(
        "-y", "--yes", action="store_true",
        help="accept the link-only source disclaimer without prompting",
    )
    parser.set_defaults(func=cmd_fetch)


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------


def _confirm_link_only_download(count: int, *, assume_yes: bool) -> bool:
    """Ask the operator to accept third-party source terms before downloading.

    Link-only images are never redistributed by this repository -- see
    ``NOTICE``. ``fetch`` is the one command that reaches out to those
    origins, so this is the one place that needs explicit, per-invocation
    consent rather than a persisted flag: a stored "already agreed" marker
    would not reflect which manifest or which URLs are actually being fetched
    next time. ``--yes`` (or a non-interactive stdin, e.g. CI) skips the
    prompt but still logs which path was taken.
    """
    print(
        "============================================================\n"
        " LINK-ONLY IMAGE DOWNLOAD -- THIRD-PARTY CONTENT DISCLAIMER\n"
        "============================================================\n"
        f"This suite has {count} link-only case(s). Their images are NOT\n"
        "redistributed by this repository -- only a URL and a ground-truth\n"
        "label are shipped.\n"
        "\n"
        f"Running this command will download {count} image(s) directly from\n"
        "their original sources, under WHATEVER TERMS EACH ORIGIN SETS.\n"
        "\n"
        "  * This project holds no licence to these images and grants you\n"
        "    none.\n"
        "  * It makes no representation about your right to use them.\n"
        "  * You are responsible for reviewing and complying with each\n"
        "    origin's own terms before downloading.\n"
    )
    if assume_yes:
        print("disclaimer accepted (--yes)\n")
        return True
    if not sys.stdin.isatty():
        print(
            "disclaimer not accepted: no terminal to prompt and --yes was not "
            "given\n",
            file=sys.stderr,
        )
        return False
    try:
        reply = input("Proceed and download from these external sources? [y/N]: ")
    except EOFError:
        reply = ""
    accepted = reply.strip().lower() in ("y", "yes")
    print(f"disclaimer {'accepted' if accepted else 'declined'} (interactively)\n")
    return accepted


def cmd_fetch(args: argparse.Namespace) -> int:
    """Download link-only images and optionally pin their digests."""
    from agribench.datasets.fetch import fetch_images, pin_manifest

    rows = load_manifest(args.manifest)
    linked = [r for r in rows if r.get("image_url")]
    if not linked:
        print(f"agribench: {args.manifest} has no link-only cases", file=sys.stderr)
        return 0

    images_dir = args.images or Path(args.manifest).parent / "images"
    unpinned = sum(1 for r in linked if not r.get("image_sha256"))
    print(f"suite      {args.manifest}")
    print(f"link-only  {len(linked)} case(s), {unpinned} not yet pinned")
    print(f"images     {images_dir}\n")

    if not _confirm_link_only_download(len(linked), assume_yes=args.yes):
        print("aborted -- no images downloaded", file=sys.stderr)
        return 1

    result = fetch_images(
        linked,
        images_dir,
        timeout=args.timeout,
        retries=args.retries,
        pause=args.pause,
        verify_existing=args.recheck,
        only_missing=not args.recheck,
    )
    print(f"\nresult  {result.summary()}")

    for outcome in result.mismatched:
        print(f"  MISMATCH {outcome.case_id}: {outcome.detail}")
    for outcome in result.failed[:10]:
        print(f"  FAILED   {outcome.case_id}: {outcome.detail}")

    if args.pin:
        pinned, warnings = pin_manifest(args.manifest, result, dry_run=args.dry_run)
        verb = "would pin" if args.dry_run else "pinned"
        print(f"\n{verb} {pinned} digest(s) into {args.manifest}")
        for warning in warnings[:10]:
            print(f"  {warning}")

    if result.mismatched:
        print(
            "\nA digest mismatch means the origin now serves different bytes than "
            "the ground truth was written against. Do not re-pin it blindly: "
            "re-check the label against the new image first."
        )
    return 0 if result.complete else 1


def _is_offline(adapter: Any) -> bool:
    """Whether an adapter never opens the image file.

    An offline fixture must keep working before any dataset is staged, so it is
    exempt from the missing-image preflight. The flag is read from
    ``describe()`` because that is the adapter contract's own self-report; an
    ``offline`` attribute is honoured too if one is set.
    """
    if getattr(adapter, "offline", False):
        return True
    try:
        return bool(adapter.describe().get("offline", False))
    except Exception:  # a broken describe() must not block the run
        return False


def cmd_stage(args: argparse.Namespace) -> int:
    """Materialise or audit the images a suite needs."""
    from agribench.datasets.stage import audit_images, stage_images

    rows = load_manifest(args.manifest)
    images_dir = args.images or Path(args.manifest).parent / "images"

    if args.audit or args.source is None:
        result = audit_images(rows, images_dir, deep=args.deep)
        if args.source is None and not args.audit and not result.complete:
            print(
                "no --from given, so nothing was staged; showing what is present",
                file=sys.stderr,
            )
    else:
        result = stage_images(
            rows, args.source, images_dir, move=args.move, verify_existing=args.deep
        )

    print(f"suite   {args.manifest}")
    print(f"images  {images_dir}")
    print(f"cases   {len(rows)}")
    print(f"result  {result.summary()}")
    if result.corrupt:
        print(f"\n{len(result.corrupt)} file(s) do not match their recorded digest:")
        for path in result.corrupt[:5]:
            print(f"  {path}")
        print("  these were NOT used; re-stage them from a good source")
    if result.missing:
        print(f"\n{len(result.missing)} image(s) still missing, e.g.:")
        for digest in result.missing[:3]:
            print(f"  {digest}")
        print("\nThe suite cannot be run until these are staged.")
        return 1
    print("\nOK - every case image is present and verified")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    """Run an adapter over the manifest."""
    options = dict(args.adapter_options)
    if args.system_type:
        options["system_type"] = args.system_type

    try:
        adapter = load_adapter(args.adapter, **options)
    except AdapterError as exc:
        print(f"agribench: {exc}", file=sys.stderr)
        return 1

    try:
        rows = load_manifest(args.manifest, limit=args.limit)
    except (FileNotFoundError, ValueError) as exc:
        print(f"agribench: {exc}", file=sys.stderr)
        return 1
    if not rows:
        print(f"agribench: {args.manifest} contains no cases", file=sys.stderr)
        return 1

    images_root = args.images or Path(args.manifest).parent

    # Preflight. An adapter that reads pixels will fail on every case if the
    # images were never staged, and the run file it leaves behind looks like a
    # legitimate result -- a table of errors, or worse a table of confident
    # guesses from an adapter that tolerates a missing file. Refuse up front
    # rather than publish that. The offline fixture is exempt because its whole
    # purpose is to work before any dataset exists.
    if not _is_offline(adapter) and not args.allow_missing_images:
        from agribench.datasets.stage import audit_images

        audit = audit_images(rows, Path(images_root) / "images")
        if audit.missing:
            print(
                f"agribench: {len(audit.missing)} of {len(rows)} case images are "
                f"not staged under {images_root}.\n"
                f"  stage them first:  agribench stage --from <dir>\n"
                f"  or override:       --allow-missing-images",
                file=sys.stderr,
            )
            return 1

    out = args.out or DEFAULT_OUT_DIR / f"{_slug(args.label or args.adapter)}.json"

    progress = None if args.quiet else (lambda line: print(line, file=sys.stderr))
    try:
        result = run_benchmark(
            adapter,
            rows,
            images_root=images_root,
            out_path=out,
            manifest_path=args.manifest,
            workers=args.workers,
            resume=args.resume,
            retry_errors=args.retry_errors,
            snapshot_every=args.snapshot_every,
            label=args.label,
            progress=progress,
        )
    except AdapterError as exc:
        print(f"agribench: adapter setup failed: {exc}", file=sys.stderr)
        return 1

    print(_metrics_table([_row_for(result.run, result.metrics)]))
    print(f"\nrun      {portable_path(out)}")
    print(f"records  {portable_path(records_path_for(out))}")
    if result.interrupted:
        print("\ninterrupted -- re-run the same command to resume", file=sys.stderr)
        return 1
    return 0


def cmd_score(args: argparse.Namespace) -> int:
    """Recompute metrics from a stored run."""
    try:
        document = _load_run(args.run)
    except (FileNotFoundError, ValueError) as exc:
        print(f"agribench: {exc}", file=sys.stderr)
        return 1

    results = document["results"]
    rescored = False
    if args.manifest:
        try:
            rows = {str(row["case_id"]): row for row in load_manifest(args.manifest)}
        except (FileNotFoundError, ValueError) as exc:
            print(f"agribench: {exc}", file=sys.stderr)
            return 1
        results, missing = _rescore(results, rows)
        rescored = True
        if missing:
            print(
                f"agribench: {len(missing)} case(s) in the run are absent from "
                f"the manifest and were left as stored (first: {missing[0]})",
                file=sys.stderr,
            )

    metrics = compute_metrics(results)
    payload = {
        "run": document["run"],
        "metrics": metrics,
        "rescored_against_manifest": portable_path(args.manifest) if rescored else None,
        "contract_version": CONTRACT_VERSION,
    }
    if not metrics["n"]:
        print("agribench: no scorable cases in this run", file=sys.stderr)

    rendered = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    if args.out:
        _write(args.out, rendered)
    if args.json or args.out is None:
        print(rendered if args.json else _metrics_table([_row_for(document["run"], metrics)]))
    return 0 if metrics["n"] else 1


def cmd_report(args: argparse.Namespace) -> int:
    """Render a single run."""
    try:
        document = _load_run(args.run)
    except (FileNotFoundError, ValueError) as exc:
        print(f"agribench: {exc}", file=sys.stderr)
        return 1

    run, results = document["run"], document["results"]
    metrics = document.get("metrics") or compute_metrics(results)
    groups = _breakdown(results, args.by)

    if args.format == "json":
        rendered = (
            json.dumps(
                {"run": run, "metrics": metrics, "breakdown": groups},
                indent=2,
                ensure_ascii=False,
            )
            + "\n"
        )
    elif args.format == "csv":
        rendered = _csv([_row_for(run, metrics)])
    else:
        rendered = _report_markdown(run, metrics, groups, args.by)

    _emit(rendered, args.out)
    return 0


def cmd_compare(args: argparse.Namespace) -> int:
    """Rank several runs side by side."""
    rows: list[dict[str, Any]] = []
    benchmarks: set[str] = set()
    for path in args.runs:
        try:
            document = _load_run(path)
        except (FileNotFoundError, ValueError) as exc:
            print(f"agribench: {exc}", file=sys.stderr)
            return 1
        run = document["run"]
        metrics = document.get("metrics") or compute_metrics(document["results"])
        row = _row_for(run, metrics)
        row["source"] = portable_path(path)
        rows.append(row)
        benchmarks.add(_case_set_key(run))

    rows.sort(key=lambda item: _sort_key(item, args.sort), reverse=args.sort != "label")

    warnings = _comparability_warnings(rows, benchmarks)
    if args.format == "json":
        rendered = (
            json.dumps(
                {
                    "contract_version": CONTRACT_VERSION,
                    "sorted_by": args.sort,
                    "warnings": warnings,
                    "systems": rows,
                },
                indent=2,
                ensure_ascii=False,
            )
            + "\n"
        )
    elif args.format == "csv":
        rendered = _csv(rows)
    else:
        rendered = _compare_markdown(rows, warnings, args.sort)

    _emit(rendered, args.out)
    for warning in warnings:
        print(f"agribench: {warning}", file=sys.stderr)
    return 0


def cmd_verify_contract(args: argparse.Namespace) -> int:
    """Delegate to the contract's own verifier, which owns the lock format."""
    from agribench.contract import verify as contract_verify

    return contract_verify.main([str(args.lock)] if args.lock else [])


def cmd_validate_manifest(args: argparse.Namespace) -> int:
    """Check a manifest for structural defects and scoring hazards.

    Two tiers, kept apart on purpose.

    **Errors** mean the manifest is unusable: a missing field, a malformed
    digest, an image that is not there, bytes that do not match their hash.
    These fail the command.

    **Warnings** mean the manifest is usable but something about how it scores
    is worth knowing before quoting a number -- a ground-truth label the
    matcher can never match, or a declared ``issue_family`` the frozen matcher
    disagrees with. These do not fail the command, because the frozen zone
    cannot be edited to satisfy them and a dataset that cannot pass its own
    validator is a validator nobody runs. ``--strict`` promotes them.
    """
    try:
        rows = load_manifest(args.manifest, limit=args.limit)
    except (FileNotFoundError, ValueError) as exc:
        print(f"agribench: {exc}", file=sys.stderr)
        return 1

    # `run` uses the permissive loader above, so the checks below must never be
    # stricter than running. The distribution model -- which cases may be
    # bundled, which need a URL, which licences permit redistribution -- is an
    # authoring rule rather than a runtime one, and this is the only place it is
    # enforced. Skipped under --limit, where a partial read would report a
    # missing case as a defect.
    if args.limit is None:
        try:
            parse_manifest(
                Path(args.manifest).read_text(encoding="utf-8").splitlines(),
                source=str(args.manifest),
            )
        except ManifestError as exc:
            print(f"agribench: {exc}", file=sys.stderr)
            return 1

    images_root = args.images or (Path(args.manifest).parent if args.check_hashes else None)
    errors: list[str] = []
    warnings: list[str] = []
    seen_sha: dict[str, str] = {}
    unmatchable: list[str] = []
    family_mismatch: list[tuple[str, str, str]] = []
    missing_images: list[str] = []
    bad_hashes: list[str] = []

    for row in rows:
        case_id = str(row["case_id"])
        for field_name in MANIFEST_FIELDS:
            if not isinstance(row.get(field_name), str):
                errors.append(f"{case_id}: {field_name} must be a string")

        sha = str(row.get("image_sha256") or "")
        if len(sha) != 64 or not all(char in "0123456789abcdef" for char in sha.lower()):
            errors.append(f"{case_id}: image_sha256 is not a 64-hex-digit digest")
        elif sha in seen_sha and seen_sha[sha] != case_id:
            warnings.append(f"{case_id}: same image bytes as {seen_sha[sha]}")
        else:
            seen_sha[sha] = case_id

        # The manifest's issue_family is metadata; scoring derives the family
        # from the issue label with the frozen matcher. Where the two disagree,
        # the matcher wins and the manifest's own label is the misleading one.
        declared = str(row.get("issue_family") or "")
        derived = family_of(str(row["issue"]))
        if declared and derived != "unknown" and declared != derived:
            family_mismatch.append((case_id, declared, derived))

        # A ground-truth label whose tokens are all dropped can never be
        # matched by any prediction, however correct.
        if not _matchable(str(row["issue"]), str(row["entity"])):
            unmatchable.append(case_id)

        if images_root is not None:
            path = resolve_image_path(images_root, str(row["image"]))
            if not path.exists():
                missing_images.append(case_id)
            elif args.check_hashes and _sha256_file(path) != sha.lower():
                bad_hashes.append(case_id)

    errors.extend(f"{case_id}: image sha256 does not match the file" for case_id in bad_hashes)
    if missing_images:
        errors.append(
            f"{len(missing_images)} image(s) not found under {portable_path(images_root)} "
            f"(first: {missing_images[0]})"
        )

    if family_mismatch:
        examples = ", ".join(case_id for case_id, _, _ in family_mismatch[:3])
        pairs = sorted({f"{declared}->{derived}" for _, declared, derived in family_mismatch})
        warnings.append(
            f"{len(family_mismatch)} case(s) declare an issue_family the frozen "
            f"matcher disagrees with ({'; '.join(pairs)}; e.g. {examples}). Scoring "
            f"follows the matcher, so on these cases a system answering the "
            f"declared family is marked WRONG and one answering the derived "
            f"family is marked CORRECT"
        )
    if unmatchable:
        warnings.append(
            f"{len(unmatchable)} case(s) have a ground-truth issue the matcher can "
            f"never match, so no prediction can earn issue_class_ok on them and "
            f"issue_class_accuracy is depressed equally for every system "
            f"(first: {unmatchable[0]})"
        )

    failed = bool(errors) or (args.strict and bool(warnings))
    summary = {
        "manifest": portable_path(args.manifest),
        "cases": len(rows),
        "unique_images": len(seen_sha),
        "images_checked": images_root is not None,
        "hashes_checked": bool(args.check_hashes),
        "errors": errors,
        "warnings": warnings,
        "family_mismatch_cases": [case_id for case_id, _, _ in family_mismatch],
        "unmatchable_cases": unmatchable,
        "strict": bool(args.strict),
        "ok": not failed,
    }

    if args.json:
        print(json.dumps(summary, indent=2, ensure_ascii=False))
        return 0 if not failed else 1

    print(f"manifest  {summary['manifest']}")
    print(f"cases     {summary['cases']} ({summary['unique_images']} unique images)")
    for warning in warnings:
        print(f"WARN      {warning}")
    for error in errors[:20]:
        print(f"ERROR     {error}", file=sys.stderr)
    if len(errors) > 20:
        print(f"ERROR     ... and {len(errors) - 20} more", file=sys.stderr)
    if failed:
        print(f"FAILED ({len(errors)} error(s), {len(warnings)} warning(s))")
    else:
        print(f"OK ({len(warnings)} warning(s))" if warnings else "OK")
    return 0 if not failed else 1


# --------------------------------------------------------------------------
# shared helpers
# --------------------------------------------------------------------------


def _load_run(path: str | Path) -> dict[str, Any]:
    """Load a run from either the aggregate JSON or the JSONL sidecar."""
    source = Path(path)
    if not source.exists():
        raise FileNotFoundError(f"run file not found: {source}")

    if source.suffix == ".jsonl":
        # The sidecar is append-only, so a resumed run legitimately contains an
        # earlier error record and its later retry for the same case. Last
        # write wins, exactly as the runner's own resume logic reads it --
        # otherwise a retried case would be counted twice and its stale error
        # would inflate the error count.
        deduped: dict[str, dict[str, Any]] = {}
        for record in iter_records(source):
            deduped[str(record["case_id"])] = record
        return {
            "run": {"label": source.stem, "system_type": "unknown"},
            "results": list(deduped.values()),
        }

    try:
        document = json.loads(source.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{source.name}: not valid JSON ({exc})") from exc
    if not isinstance(document, dict) or not isinstance(document.get("results"), list):
        raise ValueError(f"{source.name}: not an agribench run file (no 'results' list)")
    document.setdefault("run", {"label": source.stem, "system_type": "unknown"})
    return document


def _rescore(
    results: Sequence[dict[str, Any]], rows: dict[str, dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[str]]:
    """Re-derive correctness flags from stored predictions and ground truth."""
    from agribench.adapters import Prediction

    rescored: list[dict[str, Any]] = []
    missing: list[str] = []
    for record in results:
        case_id = str(record.get("case_id") or "")
        row = rows.get(case_id)
        if record.get("error") or row is None:
            if row is None and not record.get("error"):
                missing.append(case_id)
            rescored.append(dict(record))
            continue
        prediction = Prediction.from_payload(record, answered=bool(record.get("answered")))
        fresh = score_record(row, prediction)
        fresh["latency_ms"] = record.get("latency_ms")
        if "structured_mode" in record:
            fresh["structured_mode"] = record["structured_mode"]
        rescored.append(fresh)
    return rescored, missing


def _row_for(run: dict[str, Any], metrics: dict[str, Any]) -> dict[str, Any]:
    """Flatten one run's identity plus metrics into a table row."""
    return {
        "label": run.get("label") or "unnamed",
        "system_type": run.get("system_type") or "unknown",
        **{key: metrics.get(key) for key in METRIC_COLUMNS},
    }


def _sort_key(row: dict[str, Any], key: str) -> Any:
    if key == "label":
        return str(row.get("label") or "")
    value = row.get(key)
    return float("-inf") if value is None else float(value)


def _breakdown(results: Sequence[dict[str, Any]], by: str) -> list[dict[str, Any]]:
    """Group results and compute the same metric set per group."""
    if by == "none":
        return []
    key = "gt_category" if by == "family" else "entity"
    buckets: dict[str, list[dict[str, Any]]] = {}
    for record in results:
        buckets.setdefault(str(record.get(key) or "?"), []).append(record)
    groups = [{"group": name, **compute_metrics(rows)} for name, rows in buckets.items()]
    groups.sort(key=lambda item: (-item["n"], item["group"]))
    return groups


def _matchable(issue: str, entity: str) -> bool:
    """Whether any prediction could match this ground-truth label."""
    from agribench.contract.matching import tokens

    return bool(tokens(issue, entity))


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _case_set_key(run: Mapping[str, Any]) -> str:
    """Identity of the case set a run was scored on.

    Two runs are comparable when they were scored on the same cases, and the
    thing that says so is ``manifest_sha256`` -- a digest over case ids and
    image digests. The manifest's *path* is where the file happened to live
    when the run was made; it changes when a suite directory moves and says
    nothing about the cases. An earlier version keyed on the path and declared
    two runs on byte-identical case sets "not comparable" after a reorganisation.
    """
    digest = str(run.get("manifest_sha256") or "").strip()
    if digest:
        return f"{run.get('manifest_id') or 'suite'}@{digest[:16]}"
    # Very old run files carried no fingerprint; the path is the best we have.
    return str(run.get("manifest") or "unknown")


def _comparability_warnings(rows: Sequence[dict[str, Any]], benchmarks: set[str]) -> list[str]:
    """Flag the ways a comparison table can quietly lie."""
    warnings: list[str] = []
    if len(benchmarks) > 1:
        warnings.append(
            f"runs were scored on {len(benchmarks)} different case sets "
            f"({', '.join(sorted(benchmarks))}); these numbers are not comparable"
        )
    sizes = {row["n"] for row in rows if row.get("n")}
    if len(sizes) > 1:
        warnings.append(
            f"valid-case counts differ across runs ({sorted(sizes)}); precision "
            f"is only comparable over the same cases"
        )
    for row in rows:
        coverage = row.get("coverage")
        if coverage is not None and coverage < 0.5:
            warnings.append(
                f"{row['label']} answered only {coverage * 100:.1f}% of cases -- "
                f"its precision is computed over that subset, not the benchmark"
            )
    return warnings


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------


def _percent(value: Any) -> str:
    return "n/a" if value is None else f"{float(value) * 100:.1f}%"


def _metrics_table(rows: Sequence[dict[str, Any]]) -> str:
    """Fixed-width table for the terminal."""
    header = (
        f"{'system':<26} {'type':<12} {'n':>5} {'ans':>5} {'prec':>8} "
        f"{'cover':>8} {'wrong':>8} {'subj':>8} {'class':>8} {'err':>5}"
    )
    lines = [header, "-" * len(header)]
    for row in rows:
        lines.append(
            f"{str(row['label'])[:26]:<26} {str(row['system_type'])[:12]:<12} "
            f"{row.get('n') or 0:>5} {row.get('answered') or 0:>5} "
            f"{_percent(row.get('precision')):>8} {_percent(row.get('coverage')):>8} "
            f"{_percent(row.get('wrong_pct')):>8} {_percent(row.get('subject_accuracy')):>8} "
            f"{_percent(row.get('issue_class_accuracy')):>8} {row.get('errors') or 0:>5}"
        )
    return "\n".join(lines)


def _csv(rows: Sequence[dict[str, Any]]) -> str:
    buffer = io.StringIO()
    fieldnames = ["rank", "label", "system_type", *METRIC_COLUMNS, "source"]
    writer = csv.DictWriter(buffer, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for rank, row in enumerate(rows, start=1):
        writer.writerow({"rank": rank, **row})
    return buffer.getvalue()


_METRIC_NOTE = (
    "`precision` is over ANSWERED cases; every other rate is over VALID cases "
    "(errors excluded from both). Read precision and coverage together -- a "
    "system that answers 3 cases and gets all 3 right reports 100% precision."
)


def _report_markdown(
    run: dict[str, Any],
    metrics: dict[str, Any],
    groups: Sequence[dict[str, Any]],
    by: str,
) -> str:
    label = run.get("label") or "unnamed"
    lines = [
        f"# {label}",
        "",
        f"- system type: `{run.get('system_type', 'unknown')}`",
        f"- contract version: `{run.get('contract_version', CONTRACT_VERSION)}`",
        f"- manifest: `{run.get('manifest') or 'unknown'}`",
        f"- cases: {metrics.get('n', 0)} valid, {metrics.get('errors', 0)} error(s)",
        f"- finished: {run.get('finished_at_utc') or 'in progress'}",
        "",
        "| metric | value |",
        "|---|---:|",
        f"| precision (of answered) | **{_percent(metrics.get('precision'))}** |",
        f"| coverage | {_percent(metrics.get('coverage'))} |",
        f"| wrong of all valid | {_percent(metrics.get('wrong_pct'))} |",
        f"| subject accuracy | {_percent(metrics.get('subject_accuracy'))} |",
        f"| issue class accuracy | {_percent(metrics.get('issue_class_accuracy'))} |",
        f"| answered | {metrics.get('answered', 0)} / {metrics.get('n', 0)} |",
        "",
        _METRIC_NOTE,
        "",
    ]
    if groups:
        heading = "issue family" if by == "family" else "entity"
        lines += [
            f"## By {heading}",
            "",
            f"| {heading} | n | answered | precision | coverage | wrong | class acc |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
        lines += [
            f"| {group['group']} | {group['n']} | {group['answered']} | "
            f"{_percent(group['precision'])} | {_percent(group['coverage'])} | "
            f"{_percent(group['wrong_pct'])} | {_percent(group['issue_class_accuracy'])} |"
            for group in groups
        ]
        lines.append("")

    adapter = run.get("adapter")
    if isinstance(adapter, dict) and adapter:
        lines += ["## Run configuration", "", "```json", json.dumps(adapter, indent=2), "```", ""]
    return "\n".join(lines)


def _compare_markdown(
    rows: Sequence[dict[str, Any]], warnings: Sequence[str], sort: str
) -> str:
    lines = [
        "# System comparison",
        "",
        f"Sorted by `{sort}`. Contract version `{CONTRACT_VERSION}`.",
        "",
        "| rank | system | type | valid n | precision | coverage | wrong of all "
        "| subject acc | class acc | errors |",
        "|---:|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for rank, row in enumerate(rows, start=1):
        lines.append(
            f"| {rank} | {row['label']} | `{row['system_type']}` | {row.get('n') or 0} | "
            f"**{_percent(row.get('precision'))}** | {_percent(row.get('coverage'))} | "
            f"{_percent(row.get('wrong_pct'))} | {_percent(row.get('subject_accuracy'))} | "
            f"{_percent(row.get('issue_class_accuracy'))} | {row.get('errors') or 0} |"
        )
    lines += ["", "## How to read this", "", _METRIC_NOTE, ""]
    if warnings:
        lines += ["## Comparability warnings", ""]
        lines += [f"- {warning}" for warning in warnings]
        lines.append("")
    return "\n".join(lines)


def _slug(text: str) -> str:
    cleaned = "".join(char if char.isalnum() or char in "-_." else "-" for char in text)
    return cleaned.strip("-") or "run"


def _write(path: str | Path, text: str) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def _emit(text: str, out: Path | None) -> None:
    if out is None:
        print(text)
    else:
        _write(out, text if text.endswith("\n") else text + "\n")
        print(f"wrote {portable_path(out)}", file=sys.stderr)


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point. Returns a process exit code."""
    parser = build_parser()
    args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130
    except BrokenPipeError:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())

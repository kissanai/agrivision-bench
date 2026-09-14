"""Verify the frozen contract against ``contract.lock.json``.

Run it::

    python -m agribench.contract.verify

Exit status 0 means every hash in the lock still matches what is on disk and
what is loaded in memory. Exit status 1 means the frozen zone drifted, and the
report names exactly what changed.

Why this exists: the prompt, the response schema, the matcher, and the metric
formulas together define the task. If any of them changes, results produced
before the change and after the change are measuring different things and must
not be placed in the same table. Comparability is not a convention here, it is
a checked invariant — CI runs this, and a single changed byte fails the build.

Drift is not a bug to be silenced. When this fails legitimately (an
intentional contract change), the fix is to bump ``contract_version``,
regenerate the lock, and re-run every published result under the new version.
The fix is never to quietly paste in the new hash.

One failure mode reports itself differently: if a pinned module is *deleted*,
``python -m`` imports ``agribench.contract`` before this module's code runs,
so the run dies with a ``ModuleNotFoundError`` naming the missing file instead
of the formatted report below. That is unavoidable — the package cannot import
itself without its own parts — and it is still a non-zero exit naming the file
that changed. The ``pinned by the lock but missing on disk`` check below still
covers any pinned file the package does not import eagerly.

Stdlib only.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

from . import CONTRACT_VERSION
from .prompt import PROMPT

__all__ = ["LOCK_PATH", "load_lock", "verify", "main"]

CONTRACT_DIR = Path(__file__).resolve().parent
LOCK_PATH = CONTRACT_DIR / "contract.lock.json"

# Files inside the frozen zone that are deliberately not hash-pinned.
_UNPINNED = {"__pycache__"}


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_lock(lock_path: Path = LOCK_PATH) -> dict:
    """Read and minimally validate the lock file."""
    if not lock_path.exists():
        raise FileNotFoundError(f"contract lock missing: {lock_path.name}")
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    missing = [
        key
        for key in (
            "contract_version",
            "prompt_sha256",
            "prompt_len",
            "matching_sha256",
            "files",
        )
        if key not in lock
    ]
    if missing:
        raise ValueError(
            f"contract lock is missing required keys: {', '.join(missing)}"
        )
    return lock


def verify(lock_path: Path = LOCK_PATH) -> list[str]:
    """Recompute every hash in the lock. Return a list of drift messages.

    An empty list means the contract is intact.
    """
    problems: list[str] = []
    lock = load_lock(lock_path)

    # --- contract version -------------------------------------------------
    locked_version = str(lock["contract_version"])
    if locked_version != CONTRACT_VERSION:
        problems.append(
            "contract_version: package says "
            f"{CONTRACT_VERSION!r}, lock says {locked_version!r}"
        )

    # --- prompt (hash of the string, not the file) ------------------------
    actual_prompt_sha = sha256_bytes(PROMPT.encode("utf-8"))
    if actual_prompt_sha != lock["prompt_sha256"]:
        problems.append(
            "PROMPT text changed\n"
            f"    expected sha256 {lock['prompt_sha256']}\n"
            f"    actual   sha256 {actual_prompt_sha}"
        )

    actual_prompt_len = len(PROMPT)
    if actual_prompt_len != lock["prompt_len"]:
        problems.append(
            "PROMPT length changed: "
            f"expected {lock['prompt_len']} chars, "
            f"actual {actual_prompt_len} chars "
            f"({actual_prompt_len - lock['prompt_len']:+d})"
        )

    # --- matcher (hash of the vendored source file) -----------------------
    matching_path = CONTRACT_DIR / "matching.py"
    if not matching_path.exists():
        problems.append("matching.py is missing from the contract package")
    else:
        actual_matching_sha = sha256_file(matching_path)
        if actual_matching_sha != lock["matching_sha256"]:
            problems.append(
                "matching.py changed (the vendored matcher is frozen)\n"
                f"    expected sha256 {lock['matching_sha256']}\n"
                f"    actual   sha256 {actual_matching_sha}"
            )
        locked_in_files = lock["files"].get("matching.py")
        if locked_in_files and locked_in_files != lock["matching_sha256"]:
            problems.append(
                "lock is self-inconsistent: matching_sha256 "
                f"({lock['matching_sha256']}) != files['matching.py'] "
                f"({locked_in_files})"
            )

    # --- every pinned file ------------------------------------------------
    for name in sorted(lock["files"]):
        expected = lock["files"][name]
        path = CONTRACT_DIR / name
        if not path.exists():
            problems.append(f"{name}: pinned by the lock but missing on disk")
            continue
        actual = sha256_file(path)
        if actual != expected:
            problems.append(
                f"{name} changed\n"
                f"    expected sha256 {expected}\n"
                f"    actual   sha256 {actual}"
            )

    # --- files that appeared without being pinned -------------------------
    on_disk = {
        entry.name
        for entry in CONTRACT_DIR.iterdir()
        if entry.is_file()
        and entry.suffix == ".py"
        and entry.name not in _UNPINNED
    }
    for name in sorted(on_disk - set(lock["files"])):
        problems.append(
            f"{name}: present in the frozen zone but not pinned by the lock"
        )

    return problems


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    lock_path = Path(argv[0]).resolve() if argv else LOCK_PATH

    try:
        problems = verify(lock_path)
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        print(f"CONTRACT VERIFY FAILED: {exc}", file=sys.stderr)
        return 1

    lock = load_lock(lock_path)
    if problems:
        print(
            f"CONTRACT DRIFT: {len(problems)} problem(s) "
            f"against contract v{lock['contract_version']}",
            file=sys.stderr,
        )
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        print(
            "\nResults produced before and after this change are not "
            "comparable.\nIf the change is intentional: bump "
            "contract_version, regenerate the lock,\nand re-run every "
            "published result. Do not hand-edit a hash.",
            file=sys.stderr,
        )
        return 1

    print(
        f"contract v{lock['contract_version']} OK "
        f"({len(lock['files'])} files pinned, "
        f"prompt {lock['prompt_len']} chars)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

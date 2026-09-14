"""Typed errors for agribench, each with a distinct process exit code.

Command-line entry points are expected to catch :class:`AgriBenchError` and
exit with ``exc.exit_code`` so that automation can tell *why* a run failed
without parsing text:

===============================  =========  ==================================
error                            exit code  meaning
===============================  =========  ==================================
(success)                        0          nothing went wrong
:class:`AgriBenchError`          1          generic agribench failure
(argument parsing)               2          reserved for argparse
:class:`AdapterError`            3          a system/adapter produced no usable
                                            result, or a run file is unusable
:class:`ManifestError`           4          the bench manifest is invalid, or a
                                            run does not line up with it
:class:`ContractDriftError`      5          the frozen prompt/matcher/metric
                                            contract changed or disagrees
===============================  =========  ==================================

Exit code 2 is deliberately left free because :mod:`argparse` uses it for
usage errors.

Stdlib only: this module is imported by the scoring path.
"""

from __future__ import annotations

__all__ = [
    "EXIT_OK",
    "EXIT_ERROR",
    "EXIT_ADAPTER",
    "EXIT_MANIFEST",
    "EXIT_CONTRACT_DRIFT",
    "AgriBenchError",
    "AdapterError",
    "ManifestError",
    "ContractDriftError",
    "exit_code_for",
]

EXIT_OK = 0
EXIT_ERROR = 1
# 2 is reserved for argparse usage errors.
EXIT_ADAPTER = 3
EXIT_MANIFEST = 4
EXIT_CONTRACT_DRIFT = 5


class AgriBenchError(Exception):
    """Base class for every error agribench raises on purpose.

    ``hint`` carries an optional operator-facing remediation line; it is
    appended to the message so a bare ``print(exc)`` stays useful.
    """

    exit_code: int = EXIT_ERROR

    def __init__(self, message: str, *, hint: str | None = None) -> None:
        self.message = message
        self.hint = hint
        super().__init__(f"{message}\nhint: {hint}" if hint else message)


class AdapterError(AgriBenchError):
    """A diagnosis system, or the run file describing one, is unusable.

    Raised when an adapter cannot produce a result at all (transport failure,
    unparseable payload, schema violation) and when a run file on disk is
    malformed, mistyped, or self-inconsistent.
    """

    exit_code = EXIT_ADAPTER


class ManifestError(AgriBenchError):
    """The bench manifest is invalid, unreadable, or does not match a run.

    Covers malformed JSON Lines, missing/blank required fields, duplicate
    case ids, unsafe image paths, missing or corrupted image files, and runs
    whose cases do not belong to the manifest they claim.
    """

    exit_code = EXIT_MANIFEST


class ContractDriftError(AgriBenchError):
    """The frozen scoring contract changed, or two artifacts disagree on it.

    Raised when a locked artifact (prompt, matcher, metric definitions) no
    longer hashes to its pinned value, and when results that are being
    compared were produced under different contract versions.
    """

    exit_code = EXIT_CONTRACT_DRIFT


def exit_code_for(exc: BaseException) -> int:
    """Return the process exit code that corresponds to ``exc``."""
    return getattr(exc, "exit_code", EXIT_ERROR) if isinstance(exc, Exception) else EXIT_ERROR

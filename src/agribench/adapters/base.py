"""The adapter contract: how any diagnosis system plugs into the benchmark.

A benchmark is only as portable as its plug. Everything above this file --
the case loop, the scoring, the report tables -- is written against the three
objects defined here, and nothing else. To benchmark a system that does not
exist yet, you implement :class:`Adapter` and nothing in the runner changes.

The plug has exactly three parts:

:class:`Case`
    What the benchmark hands you: an id, a path to an image on local disk, and
    the sha256 of that image's bytes. No ground truth, ever. An adapter that
    could see the label could cheat, so it cannot see the label.

:class:`Prediction`
    What you hand back: the frozen response fields, plus one extra bit --
    ``answered``.

:meth:`Adapter.predict`
    One case in, one prediction out. Called concurrently from a thread pool,
    so implementations must be thread-safe.

===========================================================================
ABSTENTION  --  the bit that makes this benchmark different
===========================================================================

A system MAY return ``answered=False`` to abstain, and abstention is a
first-class outcome here, not a failure mode. A real deployment that tells a
farmer "I don't know, ask an agronomist" is behaving correctly; a benchmark
that scores that as a wrong answer is measuring the wrong thing.

The arithmetic that follows from it::

    precision = correct_category_among_answered / answered
    coverage  = answered / valid_cases

Abstaining removes the case from the *precision* denominator and from the
numerator of *coverage*. So:

* **Abstention lowers coverage but not precision.** Declining a case you would
  have got wrong raises precision. Declining a case you would have got right
  lowers coverage and leaves precision unchanged in expectation.
* **It is therefore trivially gameable in one direction only.** A system that
  answers 3 cases out of 564 and gets all 3 right reports 100% precision at
  0.5% coverage. That is why no table in this repository ever prints precision
  without coverage beside it, and why ``wrong_pct`` -- answered-and-wrong over
  all valid cases -- is reported too.

Do not abstain on the benchmark's behalf. If a model returns a usable
diagnosis, report ``answered=True`` even when you suspect it is wrong; letting
an adapter filter its own model's weak answers turns a model comparison into an
adapter comparison. The one honest exception is a system that genuinely has an
internal confidence gate in production -- then the gate is part of the system
under test, and it should run exactly as it ships.

Three outcomes, kept strictly distinct
--------------------------------------

===================  ==========================================  ==============
outcome              how to signal it                            counted as
===================  ==========================================  ==============
answered             ``Prediction(answered=True, ...)``           in ``n``, in ``answered``
abstained            ``Prediction.abstain()``                     in ``n``, not answered
failed               raise :class:`AdapterError`                  excluded from ``n``
===================  ==========================================  ==============

The third row is the one adapters most often get wrong. A timeout, a 500, a
refusal, an unparseable payload: **raise**, do not return an abstention. An
infrastructure failure is not a decision the system made, and laundering it
into an abstention silently inflates precision. The runner catches the
exception, records the case as an error, and the contract's metrics drop it
from the valid-case denominator entirely.

Stdlib only. Adapters that need a third-party client are welcome to import one
*inside their own module*, but nothing on this path may.
"""

from __future__ import annotations

import json
import mimetypes
from abc import ABC, abstractmethod
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agribench.contract.metrics import DECLINED_CATEGORIES

__all__ = [
    "Adapter",
    "AdapterConfigError",
    "AdapterError",
    "AdapterResponseError",
    "Case",
    "Prediction",
    "TransientAdapterError",
    "is_answered_category",
]


# --------------------------------------------------------------------------
# errors
# --------------------------------------------------------------------------


class AdapterError(RuntimeError):
    """A case could not be scored because the system under test failed.

    Raise this (or a subclass) from :meth:`Adapter.predict` for transport
    failures, timeouts, refusals, and unparseable payloads. The runner records
    the case as an error and the contract excludes it from ``valid_cases``, so
    an outage can never be laundered into an accuracy claim in either
    direction.

    Never raise it to mean "the model was unsure" -- that is an abstention, and
    it is expressed by returning ``Prediction.abstain()``.
    """


class AdapterConfigError(AdapterError):
    """The adapter is misconfigured; retrying will not help.

    Missing base URL, missing model name, a rejected API key, an unreadable
    image. The runner surfaces these immediately rather than burning the whole
    manifest against a wall.
    """


class TransientAdapterError(AdapterError):
    """A failure worth retrying: timeout, connection reset, 429, 5xx."""


class AdapterResponseError(AdapterError):
    """The system replied, but the reply was not a usable diagnosis.

    Unparseable JSON, a missing required field, a provider refusal. Distinct
    from an abstention: the system did not decline, it malfunctioned.
    """


# --------------------------------------------------------------------------
# value objects
# --------------------------------------------------------------------------


def is_answered_category(category: str | None) -> bool:
    """Derive ``answered`` from a ``primary_category`` value.

    The frozen rule, shared with :mod:`agribench.contract.metrics`: a category
    that is missing, empty, or ``"unknown"`` means the system declined. Every
    other enum value means it answered.

    Args:
        category: The ``primary_category`` field of a response, or ``None``.

    Returns:
        ``True`` if this counts as a usable, answered diagnosis.
    """
    if category is None:
        return False
    return str(category).strip().lower() not in DECLINED_CATEGORIES


@dataclass(frozen=True, slots=True)
class Case:
    """One benchmark case as presented to an adapter.

    Carries no ground truth by construction. The runner holds the labels and
    scores the returned :class:`Prediction` itself, so an adapter cannot see
    the answer it is being graded against.

    Attributes:
        case_id: Stable identifier from the manifest, e.g. ``"of1-0000"``.
            Unique within a benchmark and safe to use as a cache key.
        image_path: Absolute or relative path to the image on local disk, as a
            string. The benchmark never asks an adapter to fetch a URL.
        image_sha256: Hex sha256 of the image bytes, from the manifest. Use it
            to key a cache, or verify you were handed the file you expected.
    """

    case_id: str
    image_path: str
    image_sha256: str = ""

    @property
    def path(self) -> Path:
        """The image path as a :class:`~pathlib.Path`."""
        return Path(self.image_path)

    def read_bytes(self) -> bytes:
        """Read the image bytes.

        Raises:
            AdapterConfigError: If the file is missing or unreadable. This is a
                harness problem, not a model failure, and is non-retryable.
        """
        try:
            return self.path.read_bytes()
        except OSError as exc:
            raise AdapterConfigError(
                f"cannot read image for case {self.case_id}: {exc.strerror or exc}"
            ) from exc

    def media_type(self) -> str:
        """Best-effort MIME type for the image, defaulting to ``image/jpeg``."""
        guessed, _ = mimetypes.guess_type(self.image_path)
        if guessed and guessed.startswith("image/"):
            return guessed
        return "image/jpeg"


@dataclass(frozen=True, slots=True)
class Prediction:
    """What a diagnosis system returned for one case.

    The five response fields mirror the frozen ``SCHEMA`` exactly; ``answered``
    is the benchmark's own bit and is not part of the response schema.

    Only ``subject``, ``primary_issue``, and ``primary_category`` are consumed
    by the scoring path. ``subject_type`` and ``secondary_issues`` are captured
    for auditability -- they cost nothing to record and a reader who disputes a
    score needs them.

    Attributes:
        answered: ``True`` if the system stood behind a usable diagnosis.
            ``False`` is a deliberate abstention: it lowers coverage but not
            precision. See the module docstring before setting this by hand.
        subject: Crop, animal, or weed species named by the system.
        subject_type: One of ``crop``, ``animal``, ``weed``, ``insect``,
            ``other``. Recorded, not scored.
        primary_issue: The single most prominent problem, named as specifically
            as the system can, or ``"healthy"``.
        primary_category: One of ``disease``, ``pest``, ``weed``,
            ``nutrient_deficiency``, ``healthy``, ``abiotic``, ``unknown``.
        secondary_issues: Every other distinct problem visible, as
            ``{"issue": ..., "category": ...}`` mappings. Empty when there is
            only one finding. Recorded, not scored.
    """

    answered: bool
    subject: str = ""
    subject_type: str = ""
    primary_issue: str = ""
    primary_category: str = ""
    secondary_issues: list[dict[str, str]] = field(default_factory=list)

    # -- constructors ------------------------------------------------------

    @classmethod
    def abstain(cls, **fields: Any) -> Prediction:
        """Build a declined prediction.

        Any response fields the system did produce may still be passed and will
        be recorded -- a model that named the crop but would not name the
        disease should report the crop, since ``subject_accuracy`` is measured
        over all valid cases, answered or not.
        """
        fields.pop("answered", None)
        return cls(answered=False, **fields)

    @classmethod
    def from_payload(
        cls,
        payload: Mapping[str, Any],
        *,
        answered: bool | None = None,
    ) -> Prediction:
        """Normalise a raw response object into a :class:`Prediction`.

        Accepts any mapping shaped like the frozen ``SCHEMA``: extra keys are
        ignored, missing keys become empty strings, ``None`` becomes ``""``.
        Strings are stripped; ``subject_type`` and ``primary_category`` are
        lowercased, because the scoring path compares the category by exact
        string equality and a stray ``"Disease"`` should not read as a miss.

        Args:
            payload: The decoded response object.
            answered: Override the answered bit. When ``None`` (the default) it
                is derived from ``primary_category`` via
                :func:`is_answered_category`, which is how every ``single_call``
                run decides. Pass an explicit value only for a system with a
                real internal answer/decline gate.

        Returns:
            A normalised prediction.

        Raises:
            AdapterResponseError: If ``payload`` is not a mapping.
        """
        if not isinstance(payload, Mapping):
            raise AdapterResponseError(
                f"expected a JSON object, got {type(payload).__name__}"
            )
        category = _clean(payload.get("primary_category")).lower()
        return cls(
            answered=(
                is_answered_category(category) if answered is None else bool(answered)
            ),
            subject=_clean(payload.get("subject")),
            subject_type=_clean(payload.get("subject_type")).lower(),
            primary_issue=_clean(payload.get("primary_issue")),
            primary_category=category,
            secondary_issues=_clean_secondary(payload.get("secondary_issues")),
        )

    # -- serialisation -----------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Flat dict of the six fields, ready to merge into a result record."""
        return {
            "answered": self.answered,
            "subject": self.subject,
            "subject_type": self.subject_type,
            "primary_issue": self.primary_issue,
            "primary_category": self.primary_category,
            "secondary_issues": [dict(item) for item in self.secondary_issues],
        }


def _clean(value: Any) -> str:
    """Coerce a response field to a stripped string. ``None`` becomes ``""``."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


#: Field names other services commonly use for the two secondary-issue keys.
_ISSUE_ALIASES = ("issue", "label", "name", "diagnosis")
_CATEGORY_ALIASES = ("category", "kind", "type", "family")


def _clean_secondary(value: Any) -> list[dict[str, str]]:
    """Normalise ``secondary_issues`` into a list of issue/category mappings.

    Deliberately tolerant. This field is recorded, never scored, so nothing a
    number depends on rests on the guessing below -- and a provider that
    returns a bare list of strings should not fail a case over a field nothing
    grades.

    Three shapes are handled: the frozen ``{"issue", "category"}`` mapping;
    a mapping using a common alias for either key (``label``/``kind`` and
    friends); and a bare string. Anything else is preserved as compact JSON in
    the ``issue`` slot rather than discarded -- the point of capturing this
    field is that a reader disputing a score can see what the system actually
    said, and silently dropping an unrecognised shape defeats that.
    """
    if not isinstance(value, Iterable) or isinstance(value, (str, bytes, Mapping)):
        return []
    cleaned: list[dict[str, str]] = []
    for item in value:
        if isinstance(item, Mapping):
            issue = _first_of(item, _ISSUE_ALIASES)
            category = _first_of(item, _CATEGORY_ALIASES).lower()
            if not issue and not category:
                issue = _compact_json(item)
        else:
            issue, category = _clean(item), ""
        if issue or category:
            cleaned.append({"issue": issue, "category": category})
    return cleaned


def _first_of(item: Mapping[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = _clean(item.get(key))
        if value:
            return value
    return ""


def _compact_json(item: Mapping[str, Any]) -> str:
    try:
        return json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError):
        return str(item)


# --------------------------------------------------------------------------
# the plug
# --------------------------------------------------------------------------


class Adapter(ABC):
    """Base class for anything that can be benchmarked.

    Subclasses implement :meth:`predict` and, if they hold a connection or a
    process, override :meth:`close`. Everything else has a working default.

    Thread safety
        The runner calls :meth:`predict` from a thread pool. Implementations
        must be safe to call concurrently: guard mutable state with a lock, or
        keep per-call state on the stack.

    Reporting
        :attr:`system_type` is stamped into every run record and every
        comparison table. ``"single_call"`` means one model call answers the
        frozen prompt directly. ``"system"`` means a pipeline -- routing,
        retrieval, multiple models, a confidence gate -- is under test. Mixing
        the two in one table is fine and interesting; hiding which is which is
        not.

    Example::

        class MyAdapter(Adapter):
            name = "my-system"
            system_type = "system"

            def predict(self, case: Case) -> Prediction:
                payload = my_client.diagnose(case.image_path)   # your call
                if payload["confidence"] < 0.55:
                    return Prediction.abstain(subject=payload["crop"])
                return Prediction.from_payload(payload, answered=True)
    """

    #: Short human-readable name; used as the default run label.
    name: str = "adapter"

    #: ``"single_call"`` or ``"system"``. See the class docstring.
    system_type: str = "single_call"

    @abstractmethod
    def predict(self, case: Case) -> Prediction:
        """Diagnose one case.

        Args:
            case: The case to diagnose. Contains no ground truth.

        Returns:
            A :class:`Prediction`. Return ``Prediction.abstain()`` to decline;
            abstention lowers coverage but not precision.

        Raises:
            AdapterError: If the case could not be attempted or the reply was
                unusable. The runner records the case as an error and the
                contract drops it from the valid-case denominator. Do not
                return an abstention to paper over a failure -- the two mean
                different things and are reported separately.
        """

    # -- lifecycle ---------------------------------------------------------

    def setup(self) -> None:  # noqa: B027 - optional hook, not abstract
        """Prepare the adapter. Called once before the case loop.

        Fail loudly here on missing credentials or an unreachable endpoint --
        one clear error beats 564 identical ones.
        """

    def close(self) -> None:  # noqa: B027 - optional hook, not abstract
        """Release resources. Called once after the case loop, even on error."""

    def __enter__(self) -> Adapter:
        self.setup()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # -- metadata ----------------------------------------------------------

    def describe(self) -> dict[str, Any]:
        """Redacted metadata stamped into the run record.

        Override to report what a reader needs to reproduce the run: model id,
        endpoint host, decoding parameters, pipeline version.

        **Never return a secret.** The returned dict is written to disk and is
        meant to be published alongside results. Report that a key was present,
        never what it was.
        """
        return {"adapter": self.name, "system_type": self.system_type}

    @classmethod
    def from_options(cls, **options: Any) -> Adapter:
        """Construct from CLI ``--adapter-option key=value`` pairs.

        The default forwards every option to ``__init__``. Override to accept
        aliases or to read a config file.

        Raises:
            AdapterConfigError: If an option is unknown or invalid, rewritten
                from the ``TypeError`` so the CLI can report it cleanly.
        """
        try:
            return cls(**options)
        except TypeError as exc:
            raise AdapterConfigError(f"{cls.__name__}: {exc}") from exc

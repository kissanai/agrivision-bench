"""Frozen response schema for the diagnosis task.

FROZEN ZONE. This module is part of the measurement contract: the prompt
(``prompt.py``), the response schema (this file), and the label matcher
(``matching.py``). Changing a single key, enum value, insertion order entry,
or ``required`` element of ``SCHEMA`` invalidates comparability with every
published result, because a model asked for a different output shape is not
answering the same question.

``SCHEMA`` is reproduced verbatim from the schema used to produce the
published numbers -- same keys, same enum values, same insertion order, same
``required`` list. Do not "clean it up", do not sort the keys, do not add
``description`` strings, and do not relax ``additionalProperties``. If a new
axis is genuinely needed, cut a new benchmark version instead of editing this
one.

Notes for runner authors:

* ``additionalProperties`` is ``False`` at the top level and inside each
  ``secondary_issues`` item. Providers whose structured-output mode rejects
  strict schemas must be recorded as such rather than silently relaxed.
* ``secondary_issues`` is in ``required`` but its items are not constrained to
  a non-empty array: a model with a single finding must return ``[]``.
* ``primary_category`` is a closed enum; ``secondary_issues[].category`` is a
  free string. That asymmetry is intentional and is part of the frozen shape.
* Only ``subject``, ``primary_issue``, and ``primary_category`` are consumed by
  the scoring path (see ``scoring.py``). The remaining fields are captured for
  auditability.

Stdlib only. This module must never import a third-party package.
"""

from __future__ import annotations

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "subject": {
            "type": "string",
            "description": "crop/animal/weed species name, or 'unknown'",
        },
        "subject_type": {
            "type": "string",
            "enum": ["crop", "animal", "weed", "insect", "other"],
        },
        "primary_issue": {
            "type": "string",
            "description": (
                "most specific name of the main problem, or 'healthy'"
            ),
        },
        "primary_category": {
            "type": "string",
            "enum": [
                "disease",
                "pest",
                "weed",
                "nutrient_deficiency",
                "healthy",
                "abiotic",
                "unknown",
            ],
        },
        "secondary_issues": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "issue": {"type": "string"},
                    "category": {"type": "string"},
                },
                "required": ["issue", "category"],
            },
        },
    },
    "required": [
        "subject",
        "subject_type",
        "primary_issue",
        "primary_category",
        "secondary_issues",
    ],
}

__all__ = ["SCHEMA"]

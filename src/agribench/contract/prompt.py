"""Frozen task prompt.

FROZEN ZONE. Changing a single byte of PROMPT invalidates comparability with
every published result. The contract lock in contract.lock.json pins its
sha256; tests/test_contract_lock.py fails the build on any drift.
"""

from __future__ import annotations

PROMPT = (
    "You are an expert agronomist and veterinarian. Examine this agricultural image and identify, as "
    "SPECIFICALLY as you can: (1) subject — the crop, animal, or weed species; (2) subject_type; "
    "(3) primary_issue — the single most prominent problem, named specifically (the exact disease, pest, "
    "insect, weed species, or nutrient deficiency), or 'healthy' if none; (4) primary_category; "
    "(5) secondary_issues — EVERY other distinct problem also visible (a second disease, an insect, a weed), "
    "empty if only one. Name diseases/pests specifically, not generically."
)

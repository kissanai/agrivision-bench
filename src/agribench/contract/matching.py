"""Fair label matching for eval: token overlap + agronomic synonyms (different sources
in the dataset name the same pest/disease differently, e.g. Helicoverpa = American
bollworm). Shared by score_eval and tuning so measurement is consistent.
"""
from __future__ import annotations

import re

_STOP = {"disease", "leaf", "plant", "spot", "and", "the", "of", "rice"}

# each set = genuinely-equivalent names (any member matches any other)
_SYNONYMS: list[set[str]] = [
    {"american_bollworm", "helicoverpa", "gram_pod_borer", "cotton_bollworm", "pod_borer", "armigera"},
    {"fall_armyworm", "spodoptera_frugiperda", "frugiperda"},
    {"tobacco_caterpillar", "spodoptera_litura", "litura"},
    {"whitefly", "whiteflies"},
    {"pink_bollworm", "pectinophora"},
    {"spotted_bollworm", "earias"},
    {"leaf_curl", "leaf_curl_virus", "ylcv", "yellow_leaf_curl_virus"},
    {"yellow_stripe_rust", "stripe_rust", "yellow_rust"},
    {"aphid", "aphids"},
    {"thrip", "thrips"},
    {"jassid", "jassids", "leafhopper"},
]


def tokens(s: str, crop: str | None = None) -> set[str]:
    s = re.sub(r"[^a-z0-9]+", " ", (s or "").lower())
    return {t for t in s.split() if len(t) > 2 and t != crop and t not in _STOP}


def _syn_hit(toks: set[str], group: set[str]) -> bool:
    joined = "_".join(sorted(toks))
    for member in group:
        if member in joined or set(member.split("_")) & toks:
            return True
    return False


_PEST = {"borer", "worm", "aphid", "aphids", "mite", "mites", "hopper", "planthopper", "thrip", "thrips",
         "whitefly", "whiteflies", "hispa", "beetle", "caterpillar", "bollworm", "mealybug", "jassid",
         "pyrilla", "weevil", "fly", "flies", "miner", "webber", "looper", "scale", "bug", "helicoverpa",
         "armyworm", "stemborer", "leafroller", "leaffolder", "spodoptera", "earias", "midge", "semilooper",
         "ectoparasite", "lice", "louse"}  # external animal parasites (visible bugs)
_DIS = {"blight", "blast", "spot", "rot", "smut", "rust", "mosaic", "virus", "mildew", "wilt", "canker",
        "anthracnose", "scald", "streak", "curl", "tungro", "sigatoka", "sheath", "cercospora", "alternaria",
        "fusarium", "bacterial", "panama", "moko", "downy", "powdery", "scab", "gummosis", "dieback",
        "boeng", "pokkah", "necrosis", "dampingoff",
        # animal/livestock health (coarse 'disease' category)
        "disease", "syndrome", "infection", "influenza", "coccidiosis", "newcastle", "mastitis",
        "lumpy", "brucellosis", "ulcerative", "septicemia", "septicaemia", "fever", "pox", "marek",
        "gumboro", "bronchitis", "fowl", "salmonella", "dermatitis", "ketosis", "foot", "mouth",
        # more livestock/poultry/aquaculture conditions (were mapping to 'unknown' -> no commit)
        "coryza", "salmonellosis", "bumblefoot", "ringworm", "mange", "peste", "ecthyma", "orf",
        "aeromoniasis", "saprolegniasis", "anemia", "anaemia", "dermatophilosis", "scour", "scours"}
_NUT = {"deficiency", "deficient", "chlorosis", "nitrogen", "potassium", "phosphorus", "zinc", "sulfur",
        "boron", "magnesium", "iron", "manganese", "nutrient"}


def family_of(label: str | None) -> str:
    """Map a specific class label to its issue family (disease/pest/nutrient/healthy)."""
    if not label:
        return "unknown"
    low = label.lower()
    t = set(re.sub(r"[^a-z]+", " ", low).split())
    if "healthy" in t:
        return "healthy"
    if "weed" in t:  # weed-species + weed-pressure classes (the v2 weed axis)
        return "weed"
    if t & _NUT:
        return "nutrient_deficiency"
    if t & _PEST:
        return "pest"
    if t & _DIS:
        return "disease"
    return "unknown"


def matches(truth: str, label: str, crop: str | None = None) -> bool:
    tt, dt = tokens(truth, crop), tokens(label, crop)
    if not tt or not dt:
        return False
    if tt <= dt or dt <= tt or len(tt & dt) / len(tt | dt) >= 0.5:
        return True
    for group in _SYNONYMS:  # synonym equivalence
        if _syn_hit(tt, group) and _syn_hit(dt, group):
            return True
    return False

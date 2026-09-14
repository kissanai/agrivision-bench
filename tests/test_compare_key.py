"""`compare` must judge comparability by case set, not by file path.

Run files record the manifest path they were scored against. That path moves
when a suite directory is reorganised; the cases do not. Two runs on
byte-identical case sets were once flagged "not comparable" purely because one
recorded `examples/clean_v1/...` and the other `tracks/diagnosis/suites/clean_v1/...`.
"""

from __future__ import annotations

from agribench.cli import _case_set_key, _comparability_warnings

DIGEST = "8a5cd2f2edcdd10e28f32443c00cac468987481bb59385eb04480fc5e548704d"


def test_same_fingerprint_different_path_is_one_case_set() -> None:
    old = {"manifest": "examples/clean_v1/manifest.jsonl", "manifest_id": "clean_v1", "manifest_sha256": DIGEST}
    new = {"manifest": "tracks/diagnosis/suites/clean_v1/manifest.jsonl", "manifest_id": "clean_v1", "manifest_sha256": DIGEST}
    keys = {_case_set_key(old), _case_set_key(new)}
    assert len(keys) == 1
    assert not [w for w in _comparability_warnings([], keys) if "different case sets" in w]


def test_different_fingerprints_are_flagged() -> None:
    a = {"manifest_id": "clean_v1", "manifest_sha256": DIGEST}
    b = {"manifest_id": "field_v1", "manifest_sha256": "f" * 64}
    keys = {_case_set_key(a), _case_set_key(b)}
    assert any("different case sets" in w for w in _comparability_warnings([], keys))


def test_fingerprintless_run_falls_back_to_path() -> None:
    assert _case_set_key({"manifest": "bench/old/manifest.jsonl"}) == "bench/old/manifest.jsonl"

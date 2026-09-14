# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Three version numbers are in play and they move independently:

| Version | What it covers | Current |
| --- | --- | --- |
| Package | the `agribench` harness — CLI, adapters, runner | `0.1.0` |
| Track release | a track's suites and leaderboard | `diagnosis-v1.0` |
| Evaluation contract | prompt, schema, matcher, metric formulas | `1.0` |

A package release never silently changes what a published score means. A change to the contract
ships as a new contract version with its own results table, and old numbers are never restated
under it.

## [diagnosis-v1.0] — 2026-09-13

Initial public release of **AgriVision Bench** and its first track, **Diagnosis**.

### The evaluation contract, frozen at `1.0`

The task prompt, response schema, label matcher, scoring functions and metric definitions live
under `src/agribench/contract/` and are pinned by sha256 in `contract.lock.json` (7 files). The
prompt is 552 characters, sha256
`bc0b0f252f45d34f57a0dbef75e8f9933696513a58ed76ac8eee3984179af603`. `agribench verify-contract`
re-hashes the zone and exits non-zero on any drift. The contract was frozen on 2026-08-07 and has
not moved since; every result published here was produced under it.

Known-imperfect matcher behaviour is documented in `docs/LIMITATIONS.md` rather than fixed, because
a scorer that drifts between runs is worth less than one that is imperfect in a published, measured
way. The matcher is a fork of an upstream scoring matcher with one unused production-only helper
removed; both pre- and post-fork digests and a note describing the change are recorded in
`contract.lock.json` so the fork can be audited without access to the original.

### Metrics, and the two-denominator rule

```
precision            = correct_category_among_answered / answered
coverage             = answered / valid_cases
wrong_pct            = (answered - correct_category_among_answered) / valid_cases
subject_accuracy     = subject_ok / valid_cases
issue_class_accuracy = exact_issue_ok / valid_cases
```

`precision` is the only metric measured over `answered`; everything else is measured over
`valid_cases`. Abstention is a first-class outcome — not scored as a wrong answer, not scored as a
right one. Errors are removed from every denominator and reported separately as a count, because a
provider timeout is a missing measurement rather than a bad diagnosis. Every ratio is `None`, never
`0.0` or `1.0`, when its denominator is zero.

### Three suites

- **`field_v1`** — 755 cases. Smallholder phone photos (99.9% India) sent to Digital Green's
  Farmer.Chat and reviewed by agronomists. Every case is `link-only` with a public **CC-BY-4.0**
  `image_url`, so the suite downloads and digest-verifies with one `agribench fetch`. It is the
  focus-crop subset of the upstream 989-photo release (774 images entered the pool; 215 photos of
  out-of-scope crops did not). Built from an 802-row source manifest: 12 rows dropped as
  unscoreable, 35 as byte-identical duplicates, and 24 kept rows carry secondary diagnoses in
  `extra_gold` (recorded, not scored). Every image's
  SHA-256 was checked against the ≈4.0M-file KissanAI training corpus — zero byte-identical
  overlaps.
- **`clean_v1`** — 159 cases. The controlled "proper photo" counterpart, curated from **112
  distinct upstream datasets**. Per-case provenance, upstream licence and dataset URL are recorded
  in `SOURCES.csv`. This suite cannot be fetched in one command and four of its cases cannot be
  obtained by a third party at all; both facts are stated in its README rather than smoothed over.
- **`openfield_v1`** — 554 cases across 79 crops and 277 crop × condition pairs, exactly two images
  per pair, all under redistributable licences (CC-BY 421, CC0 88, MIT 31, Apache-2.0 14).
  Per-image provenance in `ATTRIBUTION.csv`. No baselines yet.

`field_v1` and `clean_v1` pool to the 914-case set that published Dhenu Vision 1.0 figures are
measured over. Suites are never mixed into a single score.

Ten cases were removed from `openfield_v1` before release as unwinnable — 8 whose declared family
conflicts with the family the frozen matcher derives from the label text, and 2 whose ground-truth
label cannot match even itself because every token is on the matcher's stop list. Each is kept with
its reason in `EXCLUDED.jsonl` rather than deleted, and the matcher was not changed to accommodate
them.

### Baselines

Six frontier vision-language models scored on `field_v1` and `clean_v1` under the frozen prompt and
schema, via OpenRouter — Gemini 3.7 Flash, Gemini 3.5 Flash, Claude Opus 4.8, Muse Spark 1.3, Kimi
K3 and GPT-5.6 Sol — plus **Dhenu Vision 1.0** in each of its two shipped modes, imported from its
sealed release evaluation with per-case records unchanged and metrics recomputed under contract
v1.0. Raw per-case run files are in `tracks/diagnosis/results/` and every row re-scores offline
with `agribench score`, without downloading a single image.

The Dhenu rows carry a disclosure on the leaderboard: KissanAI builds both the benchmark and the
system, the system ran as shipped without the frozen prompt (its API takes none), and the rows are
a re-scorable self-report rather than an independent evaluation.

The headline finding: on controlled images the single-call models score 61–84% precision; on real
farmer photos, 49–54%. They essentially never decline, so each hands out a confidently wrong
diagnosis on roughly half of all field photos. The abstaining system, in precision mode, is wrong
on 3.8% of controlled cases and 17.5% of field cases at 67% and 57% coverage respectively.

### Tooling

- **Eight CLI commands** — `run`, `score`, `report`, `compare`, `verify-contract`,
  `validate-manifest`, `stage`, `fetch`. `score` and `report` never call a model.
- **Adapter interface and three adapters** — `echo` (offline stub, exercises the whole path with no
  API key and can abstain or fail at a given rate), `openai-compatible` (`single_call`) and `http`
  (`system`). Credentials are read from environment variables only.
- **Three distribution modes with SHA-256 pinning** — `bundled` (redistributable licence, staged
  from a local copy by content digest), `link-only` (manifest carries an `image_url`; the operator
  downloads from the origin under that origin's terms) and `source-only` (neither a redistribution
  grant nor a per-image URL; the manifest names the upstream dataset and the operator stages from
  it). Link-only cases may be authored unpinned and pinned afterwards with `agribench fetch --pin`,
  which never rewrites an existing digest.
- **`agribench.legacy`** — a read-only module reproducing pre-1.0 metric formulas bit-for-bit, for
  auditing historical numbers. Those formulas used different denominators for different system
  types and cannot share a table with anything here.
- **Tests and CI** — 83 test functions covering the contract lock, metric formulas, legacy
  reproduction, suite integrity, staging, link-only handling and the counts quoted in the docs. CI
  runs on Linux, macOS and Windows against Python 3.11–3.13 and executes contract verification, the
  test suite, manifest validation, a check that the scoring path imports nothing outside the
  standard library, and a privacy guard that fails the build on absolute developer paths, secret
  patterns, internal names, attributed TODOs, or image binaries committed anywhere.

### Known gaps

- **`clean_v1` is not one-command reproducible.** Its 159 images come from 112 upstream datasets;
  four cases are not obtainable at all. Published numbers on this suite remain *verifiable* — the
  raw run files re-score offline — but re-running a new system on it is a manual reassembly.
- **`openfield_v1` has no baselines.** The suite is shipped, licensed and validated; nothing has
  been scored on it.
- **The matcher is known-generous in specific, measured ways.** Token-overlap matching accepts a
  bare family word against many labels and the synonym path over-merges. Counts are in
  `docs/LIMITATIONS.md`. Read it before quoting any figure.
- **`openfield_v1` has no negative controls.** Every case has a problem in it — no healthy,
  nutrient, abiotic or weed cases — so a system that assumes every image shows a problem is never
  penalised there. `field_v1` and `clean_v1` do contain healthy cases.
- **Contamination is likely and unquantified** for suites drawn from public datasets that have been
  mirrored for years. Cross-system deltas deserve more confidence than absolute levels.

[diagnosis-v1.0]: https://github.com/kissanai/agrivision-bench/releases/tag/diagnosis-v1.0

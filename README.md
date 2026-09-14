# AgriVision Bench

**Open benchmarks for agricultural vision systems — scored on what the system actually tells the
farmer, including when it says "I don't know."**

[![ci](https://github.com/kissanai/agrivision-bench/actions/workflows/ci.yml/badge.svg)](https://github.com/kissanai/agrivision-bench/actions/workflows/ci.yml)
[![licence](https://img.shields.io/badge/code-Apache--2.0-blue.svg)](LICENSE)
[![labels](https://img.shields.io/badge/labels-CC--BY--4.0-blue.svg)](NOTICE)
[![python](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-blue.svg)](pyproject.toml)

A photo of a diseased leaf goes in. A diagnosis is supposed to come out. This benchmark measures
whether that diagnosis was right, how often the system was willing to give one at all, and — the
number that matters most in the field — how often it confidently said something wrong.

## Tracks

AgriVision Bench is a programme of tracks. Each track is a distinct agricultural vision task with
its own suites, its own frozen scoring contract, and its own leaderboard.

| Track | Task | Status |
| --- | --- | --- |
| **[Diagnosis](tracks/diagnosis/)** | Name the crop and what is wrong with it, from one photo | **v1.0 — available** |
| Grading | Grade harvested produce for quality and market class | planned |
| Counting | Count fruit, tillers, or pests in frame | planned |

Planned tracks have no release date and nothing is published for them yet. See
[ROADMAP.md](ROADMAP.md).

## Why precision under abstention

An agricultural advisory that is wrong 30% of the time is worse than useless — it is actively
expensive. A farmer who sprays the wrong fungicide on a bacterial infection loses the input cost,
loses the spray window, and loses trust. The cost of a confident wrong answer is not symmetric with
the cost of "I'm not sure, send a clearer photo."

So the headline metric is **precision among answered cases**, always published next to **coverage**:

```
precision = correct_category_among_answered / answered       <- denominator: answered
coverage  = answered / valid_cases                           <- denominator: valid_cases
wrong_pct = (answered - correct_among_answered) / valid_cases
```

**Precision is the only metric measured over `answered`. Every other metric is measured over
`valid_cases`.** Mixing the two is how benchmarks accidentally lie: dividing correct answers by
`valid_cases` quietly punishes abstention, and dividing wrong answers by `answered` quietly hides
how much of the field a system refused to play. Neither number means anything alone, so we never
publish one without the other and never rank on precision alone.

Full definitions and worked arithmetic: **[docs/METRICS.md](docs/METRICS.md)**.

## Quickstart — no API key, no images

The `echo` adapter is an offline stub that never opens an image file, so it works on a fresh clone
with nothing staged. Use it to confirm your install before spending a cent on inference.

```bash
pip install -e .
python -m agribench.contract.verify      # frozen contract still hashes to its pinned values
agribench run --adapter echo --limit 20
```

`--adapter-option answer_rate=0.6` makes the stub abstain on 40% of cases, so you can watch coverage
fall while precision holds — the central trade this benchmark measures — before running anything
real.

## Running a real system

Implement one class, anywhere on your `PYTHONPATH` — it does not need to live inside this
repository. It receives a `Case` — a case id, a local image path, that image's sha256, and
deliberately **no ground truth** — and returns a `Prediction`.

```python
# my_bench.py
from agribench.adapters.base import Adapter, Case, Prediction, TransientAdapterError
from agribench.contract.prompt import PROMPT


class MySystemAdapter(Adapter):
    name = "my-system"              # the label that appears in results tables
    system_type = "system"          # or "single_call"

    def predict(self, case: Case) -> Prediction:
        try:
            payload = my_pipeline.run(case.image_path, prompt=PROMPT)
        except TimeoutError as exc:                  # infrastructure failed
            raise TransientAdapterError(str(exc)) from exc

        if payload["confidence"] < 0.55:             # the system declined
            return Prediction.abstain(subject=payload["crop"])

        return Prediction.from_payload(payload, answered=True)
```

Then fetch a suite's images and run it, passing the adapter as an **import path** —
`module:Class`:

```bash
agribench fetch --manifest tracks/diagnosis/suites/field_v1/manifest.jsonl --yes
agribench run --adapter my_bench:MySystemAdapter \
    --manifest tracks/diagnosis/suites/field_v1/manifest.jsonl
```

The built-in names (`echo`, `openai`, `http`, and aliases such as `openrouter`, `vllm`, `ollama`)
are the only bare names `--adapter` accepts; everything else is an import path.

Three outcomes are kept strictly distinct — answered, abstained (`Prediction.abstain`, counted in
`valid_cases` but not in `answered`), and failed (`raise AdapterError`, excluded from
`valid_cases`). A timeout, a 429, a 5xx or an unparseable payload is a **failure, not an
abstention**: returning an abstention to paper over an infrastructure error quietly inflates
precision and is treated as a falsified result. The full adapter contract is in
[tracks/diagnosis/README.md](tracks/diagnosis/README.md#plugging-in-your-own-system).

## The Diagnosis track at a glance

Three suites, one frozen contract, 1,468 cases:

| Suite | Cases | What it is | Getting the images |
| --- | ---: | --- | --- |
| [`field_v1`](tracks/diagnosis/suites/field_v1/) | 755 | Smallholder phone photos, agronomist-labelled | `agribench fetch` — one public CC-BY-4.0 dataset |
| [`clean_v1`](tracks/diagnosis/suites/clean_v1/) | 159 | Controlled "proper photo" counterpart | Reassembled from 112 upstream datasets — see its README |
| [`openfield_v1`](tracks/diagnosis/suites/openfield_v1/) | 554 | Open-field photos, all redistributably licensed | `agribench stage` from a local copy |

`field_v1` and `clean_v1` pool to the **914**-case set that published Dhenu Vision 1.0 figures are
measured on. Suites are never mixed in a single score; a result names its suite.

## This repository ships no image bytes

Not for any suite, with no exception. What it ships is the case manifests, the ground-truth labels,
the attribution records, and the evaluation tooling. Every case names its image by SHA-256, and
whoever runs the benchmark puts the bytes on disk themselves — either downloaded from the origin
(`agribench fetch`) or copied from a local dataset copy (`agribench stage`, matched by content
digest, never by filename). CI fails the build if any image binary is committed anywhere.

That digest is also what makes a run comparable: an image set that has drifted is rejected rather
than silently benchmarked.

## Verifying published numbers

You do not need the images to check a published result. Every leaderboard row ships with its raw
per-case run file, and re-scoring reads that file:

```bash
agribench score --run tracks/diagnosis/results/clean-v1-google_gemini-3-7-flash.json
# openai-compatible  single_call  158  158  84.2%  100.0%  15.8%  67.7%  62.7%  1
```

Any number attributed to this benchmark that is not backed by a file in a track's `results/` did not
come from here.

## What guarantees comparability

- **The contract is frozen and hashed.** The prompt, response schema, matcher and metric formulas
  live under `src/agribench/contract/`, and `contract.lock.json` pins the sha256 of every file.
  `python -m agribench.contract.verify` exits non-zero on any drift.
- **The images are pinned.** Every case pins `image_sha256`; every fetched image is verified against
  it before use. A link that starts serving different bytes is rejected, not accepted as an update.
- **The scoring path is stdlib-only.** A published number is reproducible from a bare CPython
  install, with no dependency resolution standing between a reader and the result.
- **CI enforces all of it** — contract verification, the test suite, manifest validation, and a
  privacy guard that rejects leaked paths, secrets and stray image bytes — on three operating
  systems and three Python versions.

## Documentation

| Read this | For |
| --- | --- |
| [tracks/diagnosis/README.md](tracks/diagnosis/README.md) | the Diagnosis track: suites, leaderboard, how to submit |
| [docs/METHODOLOGY.md](docs/METHODOLOGY.md) | the authoritative method — task, contract, scoring, comparability |
| [docs/METRICS.md](docs/METRICS.md) | exact metric definitions and worked arithmetic |
| [docs/LIMITATIONS.md](docs/LIMITATIONS.md) | measured defects in the matcher and the case sets — read before quoting a number |
| [AGENTS.md](AGENTS.md) | operating rules for AI coding agents working in this repository |

**Before citing any number, read [docs/LIMITATIONS.md](docs/LIMITATIONS.md).** It documents, with
counts from the actual manifests, where the matcher is too generous, where it produces false
negatives, the empty-subject scoring quirk, known label noise, and the fact that image sources are
third-party. None of these are hypothetical and all of them affect the headline number.

## Citing

See [CITATION.cff](CITATION.cff). Quote `precision` and `coverage` together, with `valid n` and the
error count, and name the suite explicitly.

## Licence

Code and tooling: Apache-2.0 ([LICENSE](LICENSE)). Ground-truth labels and manifests: CC BY 4.0.
Benchmark **images are covered by neither** — each remains under its own upstream licence, recorded
per case in each suite's attribution file. See [NOTICE](NOTICE) for the full position, including
what to do if you hold rights to a referenced image.

# AgriVision Bench — Diagnosis track

**`diagnosis-v1.0` · evaluation contract `v1.0` · three suites · 1,468 cases**

One photo goes in. The system must name **the crop** and **what is wrong with it**, in free text, or
decline to answer. There is no fixed label set given at inference time and no multiple choice.
Scoring happens afterwards, against the ground-truth label, with a frozen matcher.

> The authoritative description of the task, the contract, and the scoring path is
> [docs/METHODOLOGY.md](../../docs/METHODOLOGY.md). This page is the track's summary and its
> leaderboard. Do not cite this file for a definition; cite that one.

## What is under test

Most image benchmarks score a **model** on a **forced choice**: here are 38 classes, pick one, we
compute top-1 accuracy. That tells you almost nothing about a deployed advisory product, because a
deployed product is not a classifier. It is a pipeline — preprocessing, one or more inference calls,
retrieval, confidence gating, fallback logic, and a final decision about whether to answer at all.

**This track scores the delivered diagnosis of a whole system.** The unit under test is whatever
sits behind the adapter interface: a single API call, an ensemble, a retrieval-augmented chain, a
rules engine, a human-in-the-loop queue. We do not inspect it and we do not care how it works.

Every entry is tagged so readers can compare like with like:

| `system_type` | meaning |
| --- | --- |
| `single_call` | one model, one call, one structured response — the baseline bar |
| `system` | anything more: multiple calls, gating, retrieval, fallbacks, abstention policy |

A `system` beating a `single_call` is not surprising and is not the point. The point is that both
are measured on the same images, with the same prompt, the same output schema and the same matcher,
so the delta is attributable to the system rather than to the evaluation.

**Abstention is a first-class outcome.** A system returning `unknown` has not answered. That is
recorded — not punished as a wrong answer, and not rewarded as a right one.

## Suites

| Suite | Cases | Distribution | Images obtainable by |
| --- | ---: | --- | --- |
| [`field_v1`](suites/field_v1/) | 755 | Smallholder phone photos (99.9% India), agronomist-labelled | **`agribench fetch`** — every case links to one public CC-BY-4.0 dataset |
| [`clean_v1`](suites/clean_v1/) | 159 | Controlled, VLM-verified "proper photo" counterpart | Reassembly from 112 upstream datasets — see [SOURCES.csv](suites/clean_v1/SOURCES.csv) |
| [`openfield_v1`](suites/openfield_v1/) | 554 | Open-field photos, 79 crops, exactly 2 images per crop × condition | `agribench stage` from a local dataset copy; all licences redistributable |

`field_v1` and `clean_v1` pool to a **914**-case set — the denominator behind published Dhenu Vision
1.0 figures. They are deliberate opposites: same frozen contract, opposite image distribution, so a
claim can always be quoted with both a controlled number and a field number. **Suites are never
mixed into a single score.** A result names its suite.

`openfield_v1` has no baselines yet.

## Leaderboard

Six frontier vision-language models under the frozen prompt and response schema, via OpenRouter,
and Dhenu Vision 1.0 in each of its two shipped modes. Every row is backed by its raw per-case run
file in [`results/`](results/) and can be re-scored with `agribench score` **without downloading
any images**. Rows are sorted by precision; read precision and coverage together, always.

**`clean_v1` — 159 controlled images**

| system | valid n | precision | coverage | wrong of all | subject acc. | class acc. | err |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Dhenu Vision 1.0 · precision mode † | 159 | **94.4%** | 67.3% | **3.8%** | 73.0% | 50.9% | 0 |
| Dhenu Vision 1.0 · coverage mode † | 159 | 84.7% | 98.7% | 15.1% | 73.0% | 65.4% | 0 |
| Gemini 3.7 Flash | 158 | 84.2% | 100.0% | 15.8% | 67.7% | 62.7% | 1 |
| Gemini 3.5 Flash | 159 | 81.1% | 100.0% | 18.9% | 54.7% | 58.5% | 0 |
| Claude Opus 4.8 | 159 | 79.9% | 100.0% | 20.1% | 57.9% | 47.2% | 0 |
| Muse Spark 1.3 | 159 | 79.9% | 100.0% | 20.1% | 62.9% | 56.6% | 0 |
| Kimi K3 | 159 | 76.7% | 100.0% | 23.3% | 54.7% | 45.3% | 0 |
| GPT-5.6 Sol | 159 | 61.0% | 100.0% | 39.0% | 55.3% | 37.1% | 0 |

**`field_v1` — 755 farmer photos**

| system | valid n | precision | coverage | wrong of all | subject acc. | class acc. | err |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Dhenu Vision 1.0 · precision mode † | 755 | **69.3%** | 57.0% | **17.5%** | 87.5% | 32.7% | 0 |
| Dhenu Vision 1.0 · coverage mode † | 755 | 56.6% | 99.7% | 43.3% | 87.5% | 39.9% | 0 |
| Muse Spark 1.3 | 755 | 53.9% | 100.0% | 46.1% | 76.2% | 39.6% | 0 |
| Gemini 3.5 Flash | 755 | 52.3% | 100.0% | 47.7% | 65.8% | 35.5% | 0 |
| Gemini 3.7 Flash | 750 | 52.3% | 100.0% | 47.7% | 78.5% | 34.3% | 5 |
| Claude Opus 4.8 | 755 | 50.1% | 100.0% | 49.9% | 81.1% | 36.6% | 0 |
| Kimi K3 | 747 | 49.9% | 99.9% | 50.1% | 87.3% | 30.9% | 8 |
| GPT-5.6 Sol | 755 | 48.7% | 100.0% | 51.3% | 87.8% | 29.4% | 0 |

† **Read this before comparing the Dhenu rows with the others.** KissanAI builds both this benchmark
and Dhenu Vision, so treat those rows as a vendor's self-report that is fully re-scorable from the
committed records — not as an independent evaluation. Two further asymmetries are recorded in the
run files rather than hidden: Dhenu Vision is a `system` entrant run exactly as it ships, and its
API takes no prompt, so **the frozen `PROMPT` was not delivered to it** (`frozen_prompt_sent:
false`); every other row received it verbatim. *Precision mode* and *coverage mode* are the same
model under the product's two shipped commit policies — they differ only in when the system is
willing to name a diagnosis.

The two tables are the whole argument for this track. The six single-call rows **essentially never
decline** — one abstention across 4,500 field cases, by Kimi K3 — so on controlled images they
score 61–84% precision, and on real farmer photos 49–54%, handing out a confidently wrong
diagnosis on roughly half of all field photos. The abstaining system makes the trade visible in
the same table: in precision mode it answers 67% of controlled cases and is wrong on 3.8% of all
of them, against 15.8% for the best single-call row; on field photos it answers 57% and is wrong
on 17.5%, against 46.1%. Switch it to coverage mode and it answers nearly everything at roughly
single-call precision. **That dial — coverage bought with wrong answers — is what this benchmark
measures, and it is invisible to any accuracy-only score.**

## Plugging in your own system

Implement `Adapter.predict`, returning a `Prediction`, and pass the class to `--adapter` as an
import path — `my_bench:MySystemAdapter` — from anywhere on your `PYTHONPATH`. Only the built-in
adapters (`echo`, `openai`, `http` and their aliases) are addressable by bare name. Three outcomes,
kept strictly distinct — this is the part adapters most often get wrong:

| outcome | how you signal it | how it is counted |
| --- | --- | --- |
| answered | `Prediction(answered=True, ...)` | in `valid_cases`, in `answered` |
| abstained | `Prediction.abstain(...)` | in `valid_cases`, **not** in `answered` |
| failed | `raise AdapterError` (or a subclass) | **excluded from `valid_cases`** |

And three rules:

1. **Use `PROMPT` verbatim.** Import it from `agribench.contract.prompt`; do not retype it, append
   hints, or translate it. It is 552 characters, sha256
   `bc0b0f252f45d34f57a0dbef75e8f9933696513a58ed76ac8eee3984179af603`. A different prompt is a
   different benchmark.
2. **Do not touch `src/agribench/contract/`.** It is frozen. Changing a byte invalidates
   comparability with every published result.
3. **Do not abstain on the benchmark's behalf.** If your model returned a usable diagnosis, report it
   as answered even when you suspect it is wrong. Filtering weak answers inside the adapter turns a
   system comparison into an adapter comparison. The honest exception is a system that genuinely
   ships a confidence gate — then the gate is part of what is under test and should run exactly as
   it ships.

A timeout, a 429, a 5xx or an unparseable payload: **raise**. Never return an abstention to paper
over an infrastructure failure — that inflates precision and is treated as a falsified result.

## Reproducing a leaderboard row

```bash
pip install -e .
python -m agribench.contract.verify

# images: field_v1 downloads from its public origin, digest-verified
agribench fetch --manifest tracks/diagnosis/suites/field_v1/manifest.jsonl --yes

BENCH_BASE_URL=https://openrouter.ai/api/v1 \
BENCH_MODEL=google/gemini-3.7-flash \
BENCH_API_KEY=... \
  agribench run --adapter openai \
    --manifest tracks/diagnosis/suites/field_v1/manifest.jsonl \
    --workers 6 --out runs/mine.json

agribench compare --run runs/mine.json \
    --run tracks/diagnosis/results/field-v1-google_gemini-3-7-flash.json
```

`run` writes as it goes and resumes by default, so Ctrl-C is safe and a half-finished run is a valid
artifact.

## Submitting a result

Open a pull request adding **the raw run file**, not a number. A row goes on the leaderboard only
when it is backed by a committed per-case run file that anyone can re-score. Rows that cannot be
reproduced from a raw file are not listed.

Your run file must carry a `contract_version` of `1.0` and the suite's own `manifest_sha256`; both
are recorded automatically by `agribench run`. State in the PR which system was tested, whether it
is `single_call` or `system`, and any deviation from the frozen prompt — a deviation does not
disqualify a row, but an undisclosed one does.

## Numbers published before contract v1.0

Pre-1.0 result tables used **different denominators for different kinds of system**:
`issue_class_accuracy` was divided by answered cases for abstaining systems and by valid cases for
single-call ones, and `wrong_pct` charged single-call systems for cases they never answered.
Contract v1.0 applies one rule to every system, which is the only basis on which a public
leaderboard can rank systems against each other.

Those older numbers are **not comparable with anything above and must never share a table with
them**. `src/agribench/legacy.py` reproduces the old arithmetic bit-exactly so historical results
stay auditable. Use it to verify a past result, never to produce a new one.

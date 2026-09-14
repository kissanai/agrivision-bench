# `field_v1` — real farmer photos, expert labels

**755 cases · CC-BY-4.0 · downloadable with `agribench fetch`**

Photos taken by smallholder farmers on their own phones (99.9% India) and sent to
[Farmer.Chat](https://farmerchat.digitalgreen.org/), an advisory service run by
[Digital Green](https://www.digitalgreen.org/); each was then reviewed by an agronomist. This is the
distribution a deployed advisory system actually faces — motion blur, distance, partial views, mixed
symptoms, whatever the light was doing that afternoon — and it is deliberately the hard counterpart
to a curated "clean image" benchmark.

## Getting the images

Every case is `link-only` and carries an `image_url` pointing at the public source dataset, so the
images download and verify in one command:

```bash
agribench fetch --manifest tracks/diagnosis/suites/field_v1/manifest.jsonl --yes
```

Each file is checked against the `image_sha256` in the manifest as it lands. A URL that starts
serving different bytes is rejected rather than silently scored.

The upstream dataset is
[**`DigiGreen/Crop_Disease_Images`**](https://huggingface.co/datasets/DigiGreen/Crop_Disease_Images)
— 989 photographs with 1,026 expert annotations, published under **CC-BY-4.0**. If you would rather
pull it wholesale and stage from a local copy, that works too, because staging matches by content
digest rather than filename:

```bash
huggingface-cli download DigiGreen/Crop_Disease_Images --repo-type dataset --local-dir dg
agribench stage --manifest tracks/diagnosis/suites/field_v1/manifest.jsonl --from dg
```

As with every suite here, **this repository ships no image bytes**. It ships the case list, the
ground truth, and the digests.

## Why this suite exists

Curated benchmarks — the Kaggle and Roboflow lineage — share a distribution with most agricultural
training corpora, so they systematically overstate what any trained system will do in the field.
Every headline number quoted from this suite is a *field* number, and for every system measured so
far it is lower than that system's clean-image number. That gap is the point.

The frontier VLM baselines make it concrete: on [`clean_v1`](../clean_v1/) the same six models score
61–84% precision, and here they score 49–54%. See the
[track leaderboard](../../README.md#leaderboard).

## Provenance and contamination check

- **Source:** the Digital Green `Crop_Disease_Images` expert set (989 photos, agronomist-reviewed),
  CC-BY-4.0. Nothing scraped, staged, or lab-photographed.
- **Leakage check:** every image's SHA-256 was compared against the content fingerprints of the
  entire KissanAI training corpus (≈4.0M files). **Zero byte-identical overlaps.** The check matches
  exact bytes, so a re-encoded near-duplicate would evade it — we have no indication any exist, but
  the limitation is stated rather than hidden.
- Because the upstream licence is permissive, this check is a claim about *our* systems only. If you
  are benchmarking a model trained on a broad web crawl, treat contamination as possible and
  unquantified, and trust cross-system deltas more than absolute levels.

## Case construction

**This suite is a subset of the upstream set, not all of it.** The upstream release holds 989
photographs (1,026 annotations). The candidate pool was built from a fixed list of focus crops
chosen for the evaluation programme, and photographs of crops outside that list were not carried
in — **774 upstream images (802 annotation rows) entered the pool; 215 did not.** The largest
groups left out are papaya (44 photos), bean (32), tobacco (22), guava (17) and pumpkin (15). This
is a scope decision, not a quality judgement on those photos, and it means a system strong on
papaya gets no credit for it here. 25 crops appear in the final suite.

From those 802 rows:

- **12 rows dropped** — the expert diagnosis tokenises to nothing under the frozen matcher, so the
  case is unscoreable by any system.
- **35 rows dropped** — byte-identical duplicate images; the first occurrence is kept.
- **24 kept rows carry more than one expert diagnosis.** The *primary* diagnosis is the scored
  `issue`; the rest are recorded in `extra_gold` and are **not** scored. This is a disclosed
  simplification: a system naming a secondary condition is marked wrong.

All 47 dropped rows are recorded with a reason each in [`EXCLUDED.jsonl`](EXCLUDED.jsonl) rather
than deleted. Issue families are derived by the frozen matcher (`family_of`), so declared and scored
families always agree.

## Files

| File | Purpose |
| --- | --- |
| `manifest.jsonl` | 755 cases: entity, expert issue, family, image digest, source URL, extra gold |
| `EXCLUDED.jsonl` | 47 dropped rows, one reason each |
| `images/` | *not committed* — populated by `agribench fetch` |

## Licence and attribution

Images are **CC-BY-4.0**, © Digital Green / Farmer.Chat contributors. Any use must preserve that
attribution; the per-case `credit` and `source_page` fields carry it. The ground-truth labels and
this manifest are CC BY 4.0 as part of this repository; the evaluation tooling is Apache-2.0.

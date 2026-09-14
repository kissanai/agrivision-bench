# `clean_v1` — the controlled-image counterpart

**159 cases · 75 entities · mixed upstream licences · reassembled from 112 source datasets**

159 curated images, each verified during curation as clearly showing its subject and issue: the
"proper photo" a diagnosis system hopes to receive. This suite exists as the deliberate counterpart
to [`field_v1`](../field_v1/) — same frozen contract, opposite image distribution — so a claim can
always be quoted with both a controlled number and a field number.

| | |
| --- | --- |
| Cases | 159 |
| Entities | 75 |
| Subject kinds | crop 147, animal 8, weed 4 |
| Issue families | disease 104, healthy 43, nutrient deficiency 6, unknown 6 |
| Excluded rows | 3, with reasons, in [`EXCLUDED.jsonl`](EXCLUDED.jsonl) |

## Read this before quoting a number

These images come from the same curated distribution most agricultural training corpora draw on,
so **absolute numbers here flatter every trained system.** Treat this suite as an upper bound and
`field_v1` as the realistic one; the honest way to quote either is next to the other.

Each case carries `held_out_from_training`, marking the **21 of 159** cases provably outside the
KissanAI training corpus. If you are benchmarking your own system, scoring those 21 separately
against the other 138 is a cheap leakage sanity check — a large gap between the two means your
result is measuring memorisation.

Pest close-ups are absent by the original curation protocol's design, so this suite says nothing
about pest identification.

## Getting the images — read this first

**This suite cannot be downloaded with a single command, and it is the only suite here that cannot.**
Its 159 images were curated from **112 distinct upstream datasets**, and reassembling it means
obtaining them from their original publishers:

| Platform | Cases | Notes |
| --- | ---: | --- |
| Kaggle | 84 | archive download; needs a Kaggle account and per-dataset terms acceptance |
| Mendeley Data | 34 | direct dataset download, no account |
| Roboflow Universe | 15 | export download; 2 projects are private |
| iNaturalist | 9 | per-photo pages |
| HuggingFace | 8 | some store images inside parquet shards, not as files |
| Bugwood, Zenodo, GBIF, PMC, KrishiKosh, ICRISAT OAR | 7 | one or two each |
| local — no external source | 2 | **not obtainable** |

[**`SOURCES.csv`**](SOURCES.csv) is the per-case record: `case_id`, platform, dataset key, dataset
URL where one resolves, upstream id, the licence recorded at curation, and an `obtainable` column
with three values — `record_url` (a landing page for the item or dataset resolves; **not** a URL
that returns the image bytes), `dataset` (download the whole upstream dataset and stage by digest),
and `none`.

**Four cases cannot be obtained by a third party at all** — `cv1-0002` and `cv1-0149` (no external
source) and `cv1-0034` and `cv1-0050` (private Roboflow projects). They are listed here rather than
quietly removed, because they are part of the 159 that published figures were measured over.

### Why `agribench fetch` does not work here

`fetch` needs a URL that returns *the exact bytes* recorded in `image_sha256`. Neither condition
holds for most of this suite:

1. **Most sources have no per-image URL.** Kaggle, Roboflow, Mendeley and several HuggingFace
   datasets distribute archives or parquet shards. There is no address that returns one image.
2. **Where per-image URLs do exist, the bytes differ.** All 9 iNaturalist cases were tested against
   their photo URLs: **0 of 9** were byte-identical, because the images were re-encoded during
   curation. `fetch` correctly rejects those as digest mismatches — that check is the whole point of
   pinning a digest, and it is not something to work around.

So the reproducible path is per-source download plus `agribench stage`, which matches by content
digest rather than filename and therefore does not care how the archive is laid out:

```bash
agribench stage --manifest tracks/diagnosis/suites/clean_v1/manifest.jsonl --from <dir> --audit
```

`--audit` reports what is present and copies nothing, so you can work through `SOURCES.csv` one
source at a time and watch the coverage climb.

## Verifying published numbers without the images

You do not need any of this to check a published result. Every leaderboard row on this suite ships
its raw per-case run file, and re-scoring reads that file:

```bash
agribench score --run tracks/diagnosis/results/clean-v1-google_gemini-3-7-flash.json
```

The images are needed only to run a **new** system on the suite.

## Licences

The licence recorded at curation is in `SOURCES.csv` per case. The distribution is mixed —
CC-BY-4.0, CC0, CC-BY, MIT and Apache-2.0 on most of the set, with a tail of non-commercial,
share-alike, ODbL, and 30 cases whose upstream licence was never established (`unknown`).

**This repository redistributes none of these images and claims no licence over any of them.** Each
remains under whatever terms its publisher set, and it is on you to review those terms for the
sources you download. Where a case's licence is recorded as `unknown` or `unverified`, treat it as
all-rights-reserved.

If you hold rights to an image referenced here and it should not be referenced, open an issue. The
case will be removed and the suite version bumped rather than silently edited, so that previously
published results stay auditable.

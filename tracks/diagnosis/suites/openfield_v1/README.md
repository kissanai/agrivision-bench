# `openfield_v1` — open-field photographs, all redistributably licensed

**554 cases · 79 crops · 277 crop × condition pairs, exactly 2 images each · no baselines yet**

Open-field photographs, not lab plates: variable lighting, cluttered backgrounds, multiple leaves in
frame, occasional co-occurring problems. That is deliberate, and it is also the source of most of
the label noise described in [`docs/LIMITATIONS.md`](../../../../docs/LIMITATIONS.md).

| | |
| --- | --- |
| Cases | 554 |
| Crops / subjects | 79 |
| Distinct crop × condition pairs | 277, with exactly 2 images each |
| Distinct condition labels | 201 |
| Issue families | disease 404, pest 150 |
| Licences | CC-BY 421, CC0 88, MIT 31, Apache-2.0 14 |
| Excluded rows | 10, with reasons, in [`EXCLUDED.jsonl`](EXCLUDED.jsonl) |

**Crop pest and disease only.** There are no animal cases and no weed cases in this suite, and no
healthy, nutrient-deficiency or abiotic cases either. A system that assumes every image shows a
problem is never penalised here, so the field-relevant false-positive rate is invisible to this
suite — see [`docs/LIMITATIONS.md`](../../../../docs/LIMITATIONS.md) §6 and §8 for what that blind
spot costs you. [`field_v1`](../field_v1/) and [`clean_v1`](../clean_v1/) do carry healthy cases.

## What makes this suite distinctive

It is the only suite here where **every image carries a licence that permits redistribution**, and
the only one built to a strict pairing design: exactly two images for every crop × condition pair.
That design forbids some analyses — you cannot compute a meaningful per-condition accuracy from
n=2 — and enables others, since no condition can dominate the score by sheer frequency.

Per-image provenance and licence are in [`ATTRIBUTION.csv`](ATTRIBUTION.csv). Sources are Kaggle
(212), Roboflow (52), iNaturalist (4), Bugwood (2) and assorted others (284).

## Getting the images

Every case is `bundled`: the licence permits redistribution, so the bytes can be staged from a local
copy of the upstream datasets. **This repository still ships no image bytes** — that rule has no
exceptions.

```bash
agribench stage --manifest tracks/diagnosis/suites/openfield_v1/manifest.jsonl --from <dir>
agribench stage --manifest tracks/diagnosis/suites/openfield_v1/manifest.jsonl --audit
```

`--from` can point at anything that happens to contain the images — a dataset checkout, an extracted
archive, a scratch download folder. Files are matched **by content digest, never by filename**, so a
source tree that has renamed or reorganised everything still works, and a file that has been
re-encoded or truncated is rejected rather than silently benchmarked. `stage` never touches the
network: obtaining the bytes is out of scope on purpose, because each image carries its own licence
and copying it is your call to make.

Confirm the suite is runnable before spending anything on inference:

```bash
agribench validate-manifest --manifest tracks/diagnosis/suites/openfield_v1/manifest.jsonl \
    --images tracks/diagnosis/suites/openfield_v1 --check-hashes
```

## Ten cases were removed as unwinnable

The suite is 554 rather than 564 because 10 cases could not be scored fairly by the frozen matcher:

- **8 cases** whose declared `issue_family` conflicts with the family the frozen matcher derives
  from the label text. A system giving the manifest's own answer would have received no credit.
- **2 cases** whose ground-truth label is composed entirely of stop words, so it cannot match even
  itself.

They are recorded in [`EXCLUDED.jsonl`](EXCLUDED.jsonl) with a reason on each row rather than
deleted. **The matcher was not changed to accommodate them** — see
[`AGENTS.md`](../../../../AGENTS.md) §1 for why a frozen scorer that is imperfect in a published,
measured way beats one that drifts.

## Status

No system has been scored on this suite. When a row appears on the
[track leaderboard](../../README.md#leaderboard) it will be backed by a committed raw run file, like
every other row.

## Licence

Images remain under their original upstream licences — CC-BY, CC0, MIT or Apache-2.0 — recorded per
case in `ATTRIBUTION.csv`, and any redistribution must preserve that attribution. Licences are as
declared upstream; we relied on the publisher's declaration and did not independently verify that
each publisher held the rights it claimed. Ground truth and manifest are CC BY 4.0; tooling is
Apache-2.0.

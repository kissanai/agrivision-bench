# Limitations

Read this before quoting any number from this repository.

**Scope.** Sections 1–5 describe the *frozen matcher*, so their mechanisms apply to every suite in
the Diagnosis track; the counts they quote are openfield_v1's. Sections 6 and 8 describe *case-set
composition*, and their counts are measured against
`tracks/diagnosis/suites/openfield_v1/manifest.jsonl` specifically — where a composition claim does
not hold for `field_v1` or `clean_v1`, it says so inline. Every claim is measured against the
committed manifest and the frozen matcher in `src/agribench/contract/matching.py`, and counts of the
form *n* / 554 are openfield_v1 counts. They are reproducible:

```bash
python -m agribench.contract.verify        # confirms the matcher is the one described here
```

None of these limitations are being fixed in `openfield_v1`. A benchmark whose scorer changes
between runs is worth less than one whose scorer is imperfect in a **published, stable, measured**
way. Corrections ship as a new suite version with its own results table, never as a retro-edit.

---

## 1. The matcher is generous, and it rewards vagueness

`exact_issue_ok` is a token-overlap test, not a taxonomy lookup. It accepts a prediction when the
token sets are a subset in either direction, when Jaccard overlap is ≥ 0.5, or when both labels hit
the same curated synonym group. Three consequences, all measured:

**Condition labels are not mutually exclusive under the matcher.** Of the 201 distinct condition
labels in the suite, **157 (78.1%)** are scored as an exact match for at least one *other* label in
the same suite. Those labels cover **442 of 554 cases (79.8%)**. There are 950 ordered label pairs
that cross-match.

**A vaguer answer often scores better than a specific wrong one.** Because a subset match passes in
either direction, a bare family word is accepted against every label containing it:

| prediction | scored exactly correct against | cases |
| --- | ---: | ---: |
| `"blight"` | 21 of 204 labels | 54 |
| `"rot"` | 22 of 204 labels | 46 |
| `"virus"` | 20 of 204 labels | 44 |
| `"rust"` | 13 of 204 labels | 28 |

Meanwhile `matches("Late blight", "Early blight")` is correctly **False**. A system that commits to
the wrong specific disease is penalised; a system that says only `"blight"` is not. The prompt asks
for specificity and the matcher does not enforce it. Treat `issue_class_accuracy` as an upper bound
on genuine species-level identification, and be suspicious of any system whose outputs are
systematically short.

**The synonym path leaks badly.** The synonym test fires when a label shares *any single word* with
*any member* of a group, and group members are multi-word keys. So one shared word pulls two
unrelated labels into the same equivalence class. **516 of the 956 collisions (54%) come from this
path alone**, affecting 53 labels across 112 cases. Real examples from the committed set:

```python
matches("American bollworm",        "Pink rot")   # True — both share one word with "pink_bollworm"
matches("Yellow leaf spot syndrome","Beet rust")  # True — both share one word with "yellow_rust"
matches("Cotton leaf curl virus",   "Pod borer")  # True
```

This is a real defect. It is documented rather than patched because patching it would silently
change every previously published number.

---

## 2. The empty-subject quirk

`score_subject` tests substring containment in both directions. The empty string is a substring of
everything, so:

```python
score_subject("aloe_vera", "")    # True
score_subject("wheat", "")        # True
score_subject("wheat", " ")       # False  — only the truly empty string passes
```

**A system that returns no subject at all scores `subject_ok` on every case.** This affects
`subject_accuracy` only; it cannot inflate `precision`, `coverage`, `wrong_pct`, or
`issue_class_accuracy`.

If a published `subject_accuracy` looks implausibly high, count blank `subject` fields in the raw
run file in `results/` before believing it. Adapters that return an empty subject on failure — a
common default — will trigger this. Treat `subject_accuracy` as the softest number on any table.

---

## 3. Stop-word false negatives

The tokenizer discards a fixed stop list (`disease`, `leaf`, `plant`, `spot`, `and`, `the`, `of`,
`rice`) and every token of two characters or fewer. **240 of 554 cases (43.3%)** carry a
ground-truth label containing at least one of those words, and **44 of 201 labels (21.9%)** reduce
to a single scoring token, which makes them maximally collision-prone in both directions.

The failure is symmetric with §1 — sometimes too loose, sometimes too tight:

```python
tokens("Leaf spot syndrome")                              # {'syndrome'}
matches("Leaf spot syndrome", "Cercospora leaf spot")     # False
```

A clinically reasonable answer is scored wrong because everything distinguishing it was discarded
as a stop word and the only surviving truth token was `syndrome`.

Two further asymmetries worth knowing:

- **`rice` is hard-coded into the stop list; no other crop name is.** `tokens("Rice blast")` →
  `{'blast'}` but `tokens("Wheat rust")` → `{'wheat', 'rust'}`. Rice cases are therefore judged on
  strictly less information than other crops, in both directions. This is an artifact inherited
  from the matcher's origin, not a design choice for this suite.
- **Crop-token suppression only works for single-word crop ids.** The matcher removes the crop name
  from a label by comparing each token against the whole entity string, so a multi-word id never
  matches a single token: `tokens("Aloe vera rot", crop="aloe_vera")` → `{'aloe', 'vera', 'rot'}`.
  The crop name is suppressed for `wheat`, not for `aloe_vera`, so labels for multi-word crops get
  extra free tokens that raise their collision rate.

---

## 4. Family credit is unavailable for 11% of cases

`correct_category` gives credit when the predicted `primary_category` equals the family derived
from the ground-truth condition label. That derivation is keyword-based and fails on **62 of 554
cases (11.2%)**, spanning 28 distinct labels — for example `Mealybugs`, `Stem borers`,
`Leafhopper jassids`, `Black scorch`, `Durian psyllid`. On those cases the family is `unknown`, the
"at least the family" path is disabled, and **only an exact class match can score at all**. Those
cases are systematically harder than the other 89% and nothing on the results table says so.

Worse, on **8 cases (2 labels)** the derived family *contradicts* the manifest:

| label | manifest `issue_family` | family the matcher derives |
| --- | --- | --- |
| `Insect feeding damage syndrome` | `pest` | `disease` |
| `Pest feeding damage syndrome` | `pest` | `disease` |

The word `syndrome` is on the disease keyword list and outranks `insect`/`pest`. On those 8 cases a
system that correctly answers `pest` receives **no** category credit, and a system that answers
`disease` receives it. The manifest's `issue_family` field is *not* what scoring uses; scoring
re-derives the family from the label text.

---

## 5. Single image, no context

Each case is one photograph and nothing else. The system does not get:

crop stage or planting date · location or agro-climatic zone · season or weather history ·
previous sprays or inputs · soil or tissue test results · the farmer's own description ·
a second photo from another angle · a whole-plant or whole-field view · time-series progression

Many conditions in this suite are **not visually separable from a single leaf image at this
resolution**, even by a trained pathologist. Bacterial and fungal leaf spots, several rusts, and
early-stage nutrient disorders routinely require a hand lens, a culture, or field context. A
perfect score on `openfield_v1` is not achievable and would indicate label leakage rather than
diagnostic skill.

This cuts both ways when generalising:

- It **understates** a deployed advisory that has GPS, season, crop calendar, and a conversation
  with the farmer. Such a system will do better in the field than here.
- It **overstates** for the same reason a photo dataset always does: images in public datasets are
  disproportionately clear, well-lit, centred, and symptomatic. Real submissions from a phone in a
  field at midday are not.

---

## 6. Label noise and set composition

**Two images per condition. Exactly.** The suite is 277 crop × condition pairs with exactly 2 cases
each (277 × 2 = 554). Consequences:

- **Do not compute per-condition accuracy.** With n=2, a per-condition rate can only be 0%, 50%, or
  100%, and any ranking built on it is noise. Per-family rates (disease n=404, pest n=150) are the
  finest slice that means anything.
- Crop coverage is uneven: median 6 cases per crop, range 2–32, and **21 of 79 crops have 3 cases
  or fewer**. Per-crop comparisons are not supported by this set.

**Labels are inherited, not re-annotated.** Ground-truth condition names come from the upstream
source datasets. No plant pathologist re-examined the images for this benchmark. Naming is
heterogeneous across sources — the same condition appears under different names, and some labels
are coarse damage descriptions (`Unspecified pest damage`, `Insect feeding damage syndrome`) rather
than diagnoses. Some fraction of the ground truth is simply wrong, and we have not measured that
fraction. A system's remaining errors at the top of the table are partly the set's errors.

**Only two issue families are represented.** Every case is `disease` (406) or `pest` (158). There
are **zero healthy cases**, zero nutrient-deficiency cases, zero weed cases, and zero abiotic
cases, even though the response schema accepts all of those categories. Two things follow:

- Those categories are **completely untested**. A system could route every nutrient deficiency to
  the wrong answer and score identically here.
- **There are no negative controls *in this suite*.** A system that assumes every image contains a
  problem is never penalised by `openfield_v1`, because every one of its images does. The
  real-world false-positive rate — telling a farmer to spray a healthy crop — is invisible here.
  **This is the one limitation in this section that the other two suites do fix:** `field_v1`
  carries 218 healthy cases and `clean_v1` carries 43, so a system that cries wolf is penalised
  there. Quote a false-positive claim from those suites, never from this one.

---

## 7. The images are third-party

We did not photograph these plants. Every image comes from a public dataset or repository and is
redistributed under the license its publisher declared.

| license | cases | | source | cases |
| --- | ---: | --- | --- | ---: |
| CC-BY | 431 | | other | 288 |
| CC0 | 88 | | Kaggle | 212 |
| MIT | 31 | | Roboflow | 58 |
| Apache-2.0 | 14 | | iNaturalist | 4 |
| | | | Bugwood | 2 |

Four honest caveats:

1. **Provenance is coarse.** The largest `source` bucket is `other` (288 cases, 51%). Per-case
   detail is in `ATTRIBUTION.csv`, but "other" is an aggregation of smaller public collections and
   is less traceable than we would like.
2. **Licenses are as declared upstream.** We relied on the publisher's declaration. We did not
   independently verify that each upstream publisher held the rights they claimed. If you are the
   rights holder for an image here and it should not be, open an issue: the case will be removed
   and the suite version bumped rather than silently edited.
3. **Training-data contamination is likely and unquantified.** These are public datasets. Several
   are well-known and have been mirrored widely for years. Any system trained on a broad web crawl
   may have seen these exact images, or near-duplicates, during training. **This inflates absolute
   scores by an unknown amount, and it does not inflate them evenly** — a system trained on public
   agricultural corpora benefits more than one trained on proprietary field photography. Treat
   cross-system deltas with more confidence than absolute levels, and treat both with less
   confidence than you would for a held-out private set.
4. **The images are not redistributed under this repository's license.** Apache-2.0 covers the code
   and tooling only. Image reuse is governed per-case by `ATTRIBUTION.csv` and must preserve
   attribution.

---

## 8. What this benchmark does not measure at all

Not flaws — just scope, and this section applies to the whole Diagnosis track. Nothing here tells
you about:

- **Latency and cost.** A system may be slow or expensive; the table looks the same either way.
- **Severity, staging, and incidence.** "Is it late blight?" is scored. "How bad is it, and how
  fast is it spreading?" is not.
- **Treatment quality.** The actual advisory output — what to apply, at what rate, with what
  pre-harvest interval — is never evaluated. A correct diagnosis followed by a dangerous
  recommendation scores perfectly.
- **Localisation.** No bounding boxes, no segmentation, no lesion counting.
- **Calibration beyond the binary.** Abstention is one bit. A well-calibrated probability is not
  rewarded over a threshold that happens to sit in the right place.
- **Robustness.** No blur, occlusion, adversarial, or off-distribution splits. No multilingual
  prompts.
- **Geographic fairness.** The set is not balanced by region or cropping system, and no per-region
  breakdown is published because the counts would not support one.

---

## 9. How to state a result honestly

- Quote `precision` **and** `coverage` together, always, plus `valid n` and the error count.
- Say `openfield_v1` explicitly. Numbers from different suite versions never share a table.
- Prefer deltas between systems measured in the same run window over absolute levels (see §7.3).
- Do not report per-condition or per-crop breakdowns from this suite (see §6).
- Link this page whenever you publish a figure from this benchmark. If a claim in it stops being
  true, update the count here and show the measurement — do not quietly delete the entry.

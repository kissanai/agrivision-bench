# Methodology

How the **AgriVision Bench Diagnosis track** works, and why it is built this way.

This is the long document. It is written for someone who has been handed a number from this
benchmark and wants to decide whether to believe it — or who is thinking about running their own
system against it and wants to know exactly what they are signing up to be measured on. Every
mechanism described here is grounded in a specific file in this repository, named inline so you can
check the claim against the code rather than against this prose.

- Track: **Diagnosis**, release `diagnosis-v1.0` ([`tracks/diagnosis/`](../tracks/diagnosis/))
- Contract version: **1.0** (`src/agribench/contract/__init__.py`, pinned in `contract.lock.json`)
- Package version: **0.1.0**
- Suites: **`field_v1`** (755), **`clean_v1`** (159), **`openfield_v1`** (554) — all under
  `tracks/diagnosis/suites/`
- Results: six frontier VLM baselines on `field_v1` and `clean_v1`, in
  [`tracks/diagnosis/results/`](../tracks/diagnosis/results/). `openfield_v1` has none yet.

Everything in sections 1–5 and 7–10 is **suite-agnostic**: it describes the task, the frozen
contract, the scoring path and the metrics, which are identical for every suite in this track.
Section 6 works through the construction of one suite, `openfield_v1`, as the detailed example; the
other two suites document their own construction in their READMEs
([`field_v1`](../tracks/diagnosis/suites/field_v1/README.md),
[`clean_v1`](../tracks/diagnosis/suites/clean_v1/README.md)).

Three companion documents, deliberately not duplicated here:

| Document | Covers |
| --- | --- |
| [`README.md`](../README.md) | What the benchmark is for, and how to run it in three commands |
| [`docs/METRICS.md`](METRICS.md) | The metric definitions on their own, with independent worked arithmetic |
| [`docs/LIMITATIONS.md`](LIMITATIONS.md) | Everything measurably wrong with the benchmark, with counts |

If this page and `LIMITATIONS.md` appear to disagree about how good a number is, `LIMITATIONS.md`
is the one to trust. It is the pessimistic document by design.

---

## Table of contents

1. [What is measured](#1-what-is-measured)
2. [The task contract](#2-the-task-contract)
3. [Scoring, step by step](#3-scoring-step-by-step)
4. [The metrics and the two-denominator rule](#4-the-metrics-and-the-two-denominator-rule)
5. [Abstention as a first-class behaviour](#5-abstention-as-a-first-class-behaviour)
6. [Suite construction: `openfield_v1` as the worked example](#6-suite-construction-openfield_v1-as-the-worked-example)
7. [Data distribution: bundled, link-only and source-only cases](#7-data-distribution-bundled-link-only-and-source-only-cases)
8. [Reproducibility](#8-reproducibility)
9. [Known limitations](#9-known-limitations)
10. [How to submit a result](#10-how-to-submit-a-result)

---

## 1. What is measured

### 1.1 The delivered diagnosis, not a component

A farmer photographs a leaf and receives one answer. That answer is the product. Whether it came
from a single model call, from an ensemble with a retrieval step and a confidence gate, or from a
model that escalated to a human on a queue is invisible to the farmer and irrelevant to whether the
advice was right.

`agri-diagnosis-bench` scores **the answer that gets delivered**. Not a component of the pipeline,
not an intermediate representation, not top-1 accuracy over a fixed label set the system was
handed. The unit under test is whatever sits behind the adapter interface in
`src/agribench/adapters/base.py`, and the benchmark never looks inside it.

This is a deliberate departure from the usual image-benchmark shape, and it costs something. A
forced-choice classification benchmark gives you a clean, decomposable number: this many classes,
this much accuracy, this confusion matrix. It also measures something no deployed advisory product
actually does. Deployed products do not receive a candidate list. They receive a photograph and
have to decide, among other things, whether they are willing to answer at all. A benchmark that
hands the system its own answer set has quietly removed the two hardest parts of the job: open-set
recognition, and knowing when to stop.

Three consequences follow, and they are the whole design:

**No label set at inference time.** The frozen prompt (§2.1) asks an open question. The system must
name the crop and the condition in free text. Nothing tells it the suite contains 79 crops, and
nothing tells it which conditions are in scope. Scoring happens afterwards, offline, against the
ground-truth label, using a frozen matcher (§3).

**Internals are out of scope.** The benchmark does not inspect architecture, weights, prompts
beyond the frozen one, retrieval corpora, or routing logic. It records what a system self-reports
in `describe()` — model id, endpoint host, decoding parameters — and stamps that into the run file
for the reader's benefit, not for scoring.

**Abstention is a measured behaviour, not a scoring artifact.** A system that declines has produced
an output; that output is recorded as an abstention and is neither credited as correct nor charged
as wrong. See §5.

### 1.2 What counts as a "system"

Anything you can wrap in an `Adapter` subclass and call once per image. The contract in
`src/agribench/adapters/base.py` is three objects — `Case` in, `Prediction` out, `predict()`
between them — and nothing above that file knows or cares what happens in between.

| Shape | How it plugs in | `system_type` |
| --- | --- | --- |
| Hosted model behind a vendor API | One call in `predict()`, structured output requested against the frozen `SCHEMA` | `single_call` |
| Self-hosted HTTP endpoint | Same, pointed at your own host | `single_call` or `system` |
| Local model, in-process | Load once in `setup()`, call in `predict()`; `predict()` must be thread-safe | `single_call` |
| Multi-stage pipeline | Preprocessing, several calls, retrieval, arbitration, all inside `predict()` | `system` |
| Confidence-gated product | The gate runs exactly as it ships; a gated-off case returns `Prediction.abstain()` | `system` |
| Human-in-the-loop | `predict()` blocks on the queue, or the run is executed offline and the run file assembled afterwards | `system` |
| Rules engine, lookup table, ensemble of any of the above | However you like | `system` |

Only two `system_type` values exist (`src/agribench/record.py`, `SYSTEM_TYPES`):

- **`single_call`** — one model, one call, one structured response. This is the baseline bar. It is
  what a reader should compare a new model against.
- **`system`** — anything more. Multiple calls, gating, retrieval, fallbacks, an abstention policy,
  a person.

A `system` beating a `single_call` is expected and is not an interesting finding on its own. The
value of the tag is that a reader can compare like with like, and can tell whether a strong
precision figure came from a better model or from a confidence gate in front of the same model. It
is never inferred; it is declared on the adapter class and stamped into the run file metadata, and
`agribench compare` prints it in every row.

### 1.3 What the system receives, and what it does not

Per case, exactly this (`Case` in `src/agribench/adapters/base.py`):

```python
Case(
    case_id="of1-0000",                       # stable manifest id, safe as a cache key
    image_path="tracks/diagnosis/suites/openfield_v1/images/f988f096….jpg",   # local file, already staged
    image_sha256="f988f096…",                 # the bytes you were handed, verifiable
)
```

That is the complete input. The system does not get the crop, the condition, the issue family, the
licence, the source dataset, the season, the location, a second photo, or a text description from
the farmer. `Case` carries no ground truth **by construction** — there is no field for it, so an
adapter cannot read it even accidentally.

Reading the manifest from inside an adapter, keying behaviour off `case_id`, or inferring the label
from the directory layout is cheating. It is not technically prevented (the manifest is a file on
your disk; nothing can stop you opening it), which is exactly why published rows require a raw run
file that a third party can inspect and re-score.

The single-image, no-context constraint is a real limitation on what a score here means for field
performance, in both directions. It is documented in
[`LIMITATIONS.md` §5](LIMITATIONS.md#5-single-image-no-context) and you should read that before
generalising any number from this suite to a deployment.

### 1.4 What a "correct answer" is allowed to look like

The benchmark is **lenient about specificity and strict about being wrong**. That asymmetry is the
central design decision and it recurs in every part of §3 and §4.

Concretely: a system that answers "some fungal blight" on a case of Alternaria blight has not given
a good answer, but it has not sent the farmer to the wrong shelf in the agri-input shop. A system
that answers "aphids" on that same case has. The first is credited at the family level and denied
class-level credit; the second is charged as a wrong answer.

The cost of this leniency is real and measured: the matcher accepts vague answers more readily than
it should, and a system whose outputs are systematically short will score better than it deserves.
See [`LIMITATIONS.md` §1](LIMITATIONS.md#1-the-matcher-is-generous-and-it-rewards-vagueness).

---

## 2. The task contract

The contract is four things, pinned together in `src/agribench/contract/`: the **prompt**, the
**response schema**, the **matcher**, and the **metric formulas**. All four are hash-pinned in
`contract.lock.json` and re-verified by `python -m agribench.contract.verify`, which CI runs on
every push. A single changed byte fails the build.

### 2.1 The frozen prompt

Every system receives this string, verbatim, alongside the image. It is 552 characters, one line,
no trailing newline.

```
You are an expert agronomist and veterinarian. Examine this agricultural image and identify, as SPECIFICALLY as you can: (1) subject — the crop, animal, or weed species; (2) subject_type; (3) primary_issue — the single most prominent problem, named specifically (the exact disease, pest, insect, weed species, or nutrient deficiency), or 'healthy' if none; (4) primary_category; (5) secondary_issues — EVERY other distinct problem also visible (a second disease, an insect, a weed), empty if only one. Name diseases/pests specifically, not generically.
```

| Property | Value |
| --- | --- |
| Length | 552 characters |
| SHA-256 of the UTF-8 bytes | `bc0b0f252f45d34f57a0dbef75e8f9933696513a58ed76ac8eee3984179af603` |
| Defined in | `src/agribench/contract/prompt.py` |
| Pinned as | `prompt_sha256` and `prompt_len` in `contract.lock.json` |

Any line wrapping you see above is your viewer's, not the string's. The two `—` characters are
U+2014 EM DASH. Do not retype the prompt; import it:

```python
from agribench.contract.prompt import PROMPT
```

Note that the prompt mentions animals and weeds. `openfield_v1` contains neither (§6.2) — the
prompt is broader than this suite because it is the contract for the task, not for the case set,
and narrowing it for one suite would make results from different suites incomparable for no gain.

**Why byte-for-byte.** Prompt sensitivity is not a rounding error in vision-language systems. A
persona prefix, an added "think step by step", a translated instruction, or a softened
"as specifically as you can" moves absolute scores by amounts comparable to the differences between
systems being compared. If two entries in one table ran under two prompts, the table is measuring
prompt engineering, not diagnosis. So the prompt is frozen, hashed, and checked, and the rule for
adapter authors is absolute:

> Import `PROMPT`. Do not retype it, do not append hints, do not prefix a persona, do not translate
> it, do not "clean up" its punctuation. A modified prompt is a different benchmark and the result
> is unpublishable.

### 2.2 The response schema

`SCHEMA` in `src/agribench/contract/schema.py` is a strict JSON Schema. Reproduced structurally:

| Field | Type | Constraint | Required | Scored |
| --- | --- | --- | :---: | :---: |
| `subject` | string | free text; crop/animal/weed species name, or `"unknown"` | yes | **yes** |
| `subject_type` | string | enum: `crop`, `animal`, `weed`, `insect`, `other` | yes | no |
| `primary_issue` | string | free text; most specific name of the main problem, or `"healthy"` | yes | **yes** |
| `primary_category` | string | enum: `disease`, `pest`, `weed`, `nutrient_deficiency`, `healthy`, `abiotic`, `unknown` | yes | **yes** |
| `secondary_issues` | array of objects | each item `{"issue": string, "category": string}`, both required; may be `[]` | yes | no |

Structural properties that are part of the frozen shape and are load-bearing:

- `additionalProperties` is `false` at the top level **and** inside each `secondary_issues` item.
- `secondary_issues` is in `required`, but is not constrained to be non-empty. A system with a
  single finding must return `[]`, not omit the key.
- `primary_category` is a **closed enum**; `secondary_issues[].category` is a **free string**. That
  asymmetry is intentional. Only `primary_category` is scored, so only it needs to be closed;
  constraining the secondary categories would reject honest output over a field nothing grades.
- Key insertion order and the `required` list are reproduced verbatim from the schema that produced
  the historical numbers. Do not sort the keys, do not add `description` strings, do not relax
  `additionalProperties`.

Two enum values deserve a warning:

- **`unknown` as `primary_category` is the abstention signal.** It is not "I answered, and the
  category is unknown". See §5.1.
- **`insect` is a valid `subject_type` but is not a valid `primary_category`.** A system that emits
  `"insect"` where `"pest"` was expected receives no category credit, because the category
  comparison is exact string equality with no normalisation (§3.7). This is disclosed in the
  `scoring.py` docstring and is not going to be fixed in `openfield_v1`.

Providers whose structured-output mode cannot honour a strict schema (`additionalProperties: false`
in particular) are common. Two honest options: relax nothing and let the case fail as an adapter
error, or relax the provider-side schema and **record that fact** in `describe()` so it lands in
the published run file. Silently relaxing it and not saying so is falsification.

### 2.3 Which fields the scoring path actually consumes

Only three: `subject`, `primary_issue`, `primary_category`. `subject_type` and `secondary_issues`
are recorded verbatim into the run file and never scored.

They are captured anyway because they cost nothing to store and a reader who disputes a score needs
them. If a published row looks wrong to you, the raw run file contains what the system actually
said, including the fields the matcher ignored.

### 2.4 Why the contract is frozen rather than maintained

The matcher in `src/agribench/contract/matching.py` has known defects. They are enumerated with
counts in `LIMITATIONS.md`. They are not being fixed.

The argument is short: a benchmark whose scorer changes between runs is worth less than a benchmark
whose scorer is imperfect in a **published, stable, measured** way. If the matcher gains a synonym
in March, every number published in February silently becomes non-comparable, and the damage is
undetectable by reading a diff six months later. Nobody re-runs their February results; they just
sit in the table, and in third-party papers, meaning something slightly different from the rows
below them.

So corrections ship as a **new suite version** with its own frozen contract and its own results
table, never as a retro-edit. The operating rule for contributors, human and automated, is in
[`AGENTS.md` §1](../AGENTS.md).

The matcher's own provenance is recorded rather than hidden. It is **forked** from an upstream
scoring matcher; the fork removed one unused production-only helper that played no part in scoring.
`contract.lock.json` records three things about it, so the fork is auditable rather than implied:

| Lock key | What it records |
| --- | --- |
| `matching_sha256` | The digest of the matcher as it exists here |
| `matching_derived_from_sha256` | The digest of the upstream file it was forked from |
| `matching_fork_note` | Plain-English statement of the one difference, and the fact that scoring behaviour is unchanged and was verified against all published v0.2 results |

The removed helper was never on the scoring path — it was used only in production code — so the
fork changes what the file contains and not what it decides. A reader who wants to confirm that can
hash both digests and diff the two files.

A benchmark whose scorer is a fork of a vendor's internal scorer should say so on the tin. This one
does.

---

## 3. Scoring, step by step

All of this lives in two files: `src/agribench/contract/matching.py` (tokenisation, the label
matcher, the family classifier) and `src/agribench/contract/scoring.py` (the two scoring
functions). Both are stdlib-only, both are hash-pinned, and `scoring.py` imports exactly three
names from `matching.py`: `family_of`, `matches`, `tokens`.

Scoring is entirely offline and entirely deterministic. It never sees an image; it compares strings.

### 3.1 Tokenisation

```python
_STOP = {"disease", "leaf", "plant", "spot", "and", "the", "of", "rice"}

def tokens(s: str, crop: str | None = None) -> set[str]:
    s = re.sub(r"[^a-z0-9]+", " ", (s or "").lower())
    return {t for t in s.split() if len(t) > 2 and t != crop and t not in _STOP}
```

In order:

1. **Lowercase.** Capitalisation never matters.
2. **Replace every run of non-alphanumeric characters with a space.** Hyphens, parentheses,
   apostrophes, underscores, and punctuation all become separators. `"Anthracnose (Colletotrichum)"`
   becomes `anthracnose colletotrichum`.
3. **Split on whitespace, producing a set.** Word order never matters, and a repeated word counts
   once.
4. **Drop tokens of two characters or fewer.** `"of"` would have gone anyway; this also removes
   things like `"sp"` and stray digits.
5. **Drop any token equal to the `crop` argument**, when one is supplied. This is what stops a
   prediction of `"tomato"` scoring against a ground truth of `"tomato late blight"` on the
   strength of the word `tomato`.
6. **Drop the stop list.**

Two properties of step 5 are worth knowing before you interpret a number. The comparison is
`t != crop`, a token against the whole entity string. So for a single-word entity like `wheat` the
crop name is suppressed, but for a multi-word entity id like `aloe_vera` no single token ever
equals the string `"aloe_vera"`, and both `aloe` and `vera` survive as scoring tokens. Multi-word
crops therefore carry extra free tokens and collide more often. See
[`LIMITATIONS.md` §3](LIMITATIONS.md#3-stop-word-false-negatives).

### 3.2 The stop list, and why `rice` is on it

`{"disease", "leaf", "plant", "spot", "and", "the", "of", "rice"}`.

Four of these are ordinary function words. Four are not: `disease`, `leaf`, `plant`, and `spot` are
agronomically meaningful words that appear inside real condition names, and `rice` is a crop.

The effect is asymmetric and both directions are real:

- **It prevents false positives.** Without it, `"Bacterial leaf blight"` and `"Bacterial leaf spot"`
  would share `leaf` and be pulled closer together than they should be.
- **It creates false negatives.** `tokens("Leaf spot syndrome")` is `{'syndrome'}` — everything that
  distinguished the label was discarded. A clinically reasonable prediction of
  `"Cercospora leaf spot"` reduces to `{'cercospora'}` and scores **False**.
- **It can empty a label entirely.** If every token of the ground truth is dropped, `matches()`
  short-circuits to `False` for *any* prediction, including a perfect one. A ground truth of
  `"leaf spot"` is unmatchable. So is `"Raspberry leaf spot"` when the entity is `raspberry`:
  `raspberry` is dropped as the crop, `leaf` and `spot` as stop words, leaving nothing. Cases like
  that are unwinnable, and `openfield_v1` removes them rather than scoring them (§6.4).

`rice` is hard-coded and no other crop name is. `tokens("Rice blast")` is `{'blast'}` while
`tokens("Wheat rust")` is `{'wheat', 'rust'}`. Rice cases are judged on strictly less information
than other crops, in both directions. This is an artifact inherited from the matcher's origin, not
a design choice for this suite, and it is documented rather than patched for the reason in §2.4.

### 3.3 `matches()` — the three acceptance rules

```python
def matches(truth: str, label: str, crop: str | None = None) -> bool:
    tt, dt = tokens(truth, crop), tokens(label, crop)
    if not tt or not dt:
        return False
    if tt <= dt or dt <= tt or len(tt & dt) / len(tt | dt) >= 0.5:
        return True
    for group in _SYNONYMS:
        if _syn_hit(tt, group) and _syn_hit(dt, group):
            return True
    return False
```

**Guard first.** If either side tokenises to the empty set, the answer is `False`. Not `True` —
an empty prediction never matches a non-empty truth, and a truth that empties out cannot be matched
by anything.

Then, in order:

| # | Rule | Test | Accepts |
| --- | --- | --- | --- |
| 1 | Subset, truth ⊆ prediction | `tt <= dt` | A prediction that says everything the truth says, plus more: `"Anthracnose"` vs `"Anthracnose (Colletotrichum)"` |
| 2 | Subset, prediction ⊆ truth | `dt <= tt` | A prediction that is a strict abbreviation of the truth: `"blight"` vs `"Late blight"` |
| 3 | Jaccard ≥ 0.5 | `len(tt & dt) / len(tt \| dt) >= 0.5` | Partial overlap where at least half the combined vocabulary is shared |
| 4 | Same synonym group | `_syn_hit(tt, g) and _syn_hit(dt, g)` for some `g` | Different names for the same organism |

Rule 2 is the one that makes the matcher generous in a way you should hold against it. Because a
subset match passes in *either* direction, a bare family word is accepted against every label
containing it: a prediction of `"blight"` matches `"Late blight"` and `"Sheath blight"` and
everything else with `blight` in it. Meanwhile `matches("Late Blight", "Early Blight")` is correctly
`False` — the token sets are `{late, blight}` and `{early, blight}`, Jaccard 1/3, no subset either
way. **A system that commits to the wrong specific disease is penalised; a system that says only
"blight" is not.** The prompt asks for specificity and the matcher does not enforce it.
`LIMITATIONS.md` §1 quantifies how many labels each bare family word sweeps up.

### 3.4 Synonym groups

Different source datasets name the same organism differently. Eleven curated groups reconcile a
fixed list of those, and only those (`_SYNONYMS` in `matching.py`):

```
{american_bollworm, helicoverpa, gram_pod_borer, cotton_bollworm, pod_borer, armigera}
{fall_armyworm, spodoptera_frugiperda, frugiperda}
{tobacco_caterpillar, spodoptera_litura, litura}
{whitefly, whiteflies}
{pink_bollworm, pectinophora}
{spotted_bollworm, earias}
{leaf_curl, leaf_curl_virus, ylcv, yellow_leaf_curl_virus}
{yellow_stripe_rust, stripe_rust, yellow_rust}
{aphid, aphids}
{thrip, thrips}
{jassid, jassids, leafhopper}
```

The membership test is `_syn_hit`, and its mechanics matter:

```python
def _syn_hit(toks: set[str], group: set[str]) -> bool:
    joined = "_".join(sorted(toks))
    for member in group:
        if member in joined or set(member.split("_")) & toks:
            return True
    return False
```

A token set hits a group if **either** some member appears as a substring of the underscore-joined,
alphabetically sorted tokens, **or** some member shares at least one word with the token set. Two
labels match under rule 4 when both independently hit the same group.

The second half of that disjunction — *one shared word with any member* — is where the matcher
leaks. Group members are multi-word keys, so one shared word pulls unrelated labels into the same
equivalence class:

```python
matches("American bollworm", "Pink rot")   # True
```

`{american, bollworm}` hits the group containing `pink_bollworm` because it shares the word
`bollworm`; `{pink, rot}` hits the same group because it shares the word `pink`. Both hit, so they
match. This is a real defect, it is responsible for a majority of the matcher's spurious
collisions, and it is documented with counts in `LIMITATIONS.md` §1 rather than patched, for the
reason in §2.4.

### 3.5 `family_of()` — deriving the issue family from a label

```python
def family_of(label: str | None) -> str: ...
```

Keyword classification over four word lists, in a fixed precedence order:

1. `"healthy"` present → `healthy`
2. `"weed"` present → `weed`
3. any nutrient keyword (`deficiency`, `chlorosis`, `nitrogen`, `zinc`, …) → `nutrient_deficiency`
4. any pest keyword (`borer`, `worm`, `aphid`, `mite`, `bollworm`, `caterpillar`, `weevil`, …) →
   `pest`
5. any disease keyword (`blight`, `blast`, `rot`, `smut`, `rust`, `mosaic`, `virus`, `mildew`,
   `wilt`, `anthracnose`, `syndrome`, `infection`, …) → `disease`
6. otherwise → `unknown`

Two consequences, both live:

**Precedence is not semantic.** Pest is tested before disease, so a label containing both a pest
word and a disease word is classified `pest`. But a label containing *only* a disease keyword is
classified `disease` even when it plainly describes insect damage: `syndrome` is on the disease
list, so `family_of("Insect feeding damage syndrome")` is `disease`, contradicting a manifest that
declares that case `pest`. `openfield_v1` removes the cases where that happens (§6.4).

**`unknown` disables the family-credit path.** `family_of("Mealybugs")` is `unknown`, because
`mealybug` is on the pest list but `mealybugs` is not a token that matches it and no other keyword
fires. On any case whose ground-truth label resolves to `unknown`, `score_issue` will not grant
category credit at all (§3.7) — only an exact class match can score. Those cases are systematically
harder than the rest and nothing on the results table says so. Counts are in
[`LIMITATIONS.md` §4](LIMITATIONS.md#4-family-credit-is-unavailable-for-11-of-cases).

`gt_category()` in `scoring.py` is a thin wrapper that renders `unknown` as `"?"` so report tables
show an explicit hole rather than a plausible-looking category.

The manifest's own `issue_family` field is **metadata for report breakdowns and is never used for
scoring**. Scoring always re-derives the family from the label text. Where the two disagree, the
matcher wins; `agribench validate-manifest` reports the disagreement as a warning.

### 3.6 `score_subject()` — did it name the right crop?

```python
def score_subject(gt_entity: str, subject: str) -> bool:
    predicted = (subject or "").lower()
    expected = gt_entity.lower()
    return bool(
        matches(expected.replace("_", " "), predicted)
        or expected in predicted
        or predicted in expected
        or (tokens(expected) & tokens(predicted))
    )
```

Four independent tests; any one passing is enough. Deliberately loose: `"tomato"`, `"tomato plant"`,
and `"Solanum lycopersicum tomato"` all score against a ground truth of `tomato`, because naming
conventions differ across source datasets and the benchmark is not testing botanical phrasing.
`score_subject("rice_paddy", "rice")` is `True` via the token-intersection test.

**The disclosed quirk.** `predicted in expected` is satisfied by the empty string for every ground
truth, so:

```python
score_subject("aloe_vera", "")     # True
score_subject("wheat", "")         # True
score_subject("wheat", " ")        # False  — only the truly empty string passes
```

A system that returns no `subject` at all scores `subject_ok` on **every** case. This affects
`subject_accuracy` only; it cannot touch `precision`, `coverage`, `wrong_pct`, or
`issue_class_accuracy`. `subject_accuracy` is therefore an upper bound for any system that
sometimes leaves the field blank, and it is the softest number on any table here. If a published
`subject_accuracy` looks implausibly high, count the blank `subject` fields in the raw run file
before believing it. Adapters that return an empty subject on a parse failure — a common default —
will trigger this.

### 3.7 `score_issue()` — did it name the right condition?

```python
def score_issue(gt_issue, gt_entity, issue, category) -> tuple[bool, bool]:
    class_ok = matches(gt_issue, issue or "", gt_entity)
    expected_family = family_of(gt_issue)
    category_ok = bool(
        expected_family != "unknown"
        and expected_family == (category or "").lower()
    )
    return class_ok, class_ok or category_ok
```

Two judgements, returned together:

- **`class_ok`** — the predicted `primary_issue` matches the ground-truth `issue` under `matches()`,
  with `gt_entity` passed as the `crop` argument so that repeating the crop name earns no credit.
  Drives `issue_class_accuracy`.
- **the second element** — `class_ok or category_ok`, where `category_ok` is exact string equality
  between the system's `primary_category` and `family_of(gt_issue)`. An exact class match implies
  category correctness, so the second element is the union. Drives `precision` and `wrong_pct`.

Three properties that are easy to get wrong when reading a result table:

1. **The category comparison is exact string equality, with no normalisation.** `"pest"` scores
   against a `pest` ground truth; `"insect"` does not, even though `insect` is a legal
   `subject_type` in the same schema. `"Disease"` would fail as written, but adapters lowercase
   `primary_category` on the way in (`Prediction.from_payload`), so casing is not in practice a
   trap.
2. **When `family_of(gt_issue)` is `unknown`, the family path is disabled, not granted.** Only an
   exact class match can score on those cases.
3. **If `gt_issue` tokenises to nothing, `class_ok` is `False` for every possible prediction** —
   stop words, short tokens, or tokens equal to `gt_entity` can empty it out. Such cases depress
   `issue_class_accuracy` uniformly across all systems: the comparison stays fair, the absolute
   number is pessimistic. `openfield_v1` excludes the cases where this happens (§6.4), but the
   mechanism is general and applies to any suite built on this contract.

### 3.8 Worked examples

All six are reproducible in a Python shell against the frozen matcher.

---

**Example 1 — a clean pass.**

Ground truth: entity `potato`, issue `Late Blight`. System returns:

```json
{"subject": "potato", "subject_type": "crop",
 "primary_issue": "late blight of potato", "primary_category": "disease",
 "secondary_issues": []}
```

Scoring:

```
tokens("Late Blight", crop="potato")            -> {late, blight}
tokens("late blight of potato", crop="potato")  -> {late, blight}
                                                   ("of" too short, "potato" == crop)
rule 1: tt <= dt  ->  True                      -> class_ok = True
family_of("Late Blight") = "disease" == "disease" -> category_ok = True
score_issue(...) -> (True, True)
score_subject("potato", "potato") -> True
```

Result: `answered=True`, `subject_ok=True`, `issue_class_ok=True`, `issue_cat_ok=True`. Counts
toward `precision`, `coverage`, `subject_accuracy`, and `issue_class_accuracy`.

---

**Example 2 — the interesting middle: right family, wrong disease.**

Same ground truth. System returns `primary_issue: "early blight"`, `primary_category: "disease"`.

```
tt = {late, blight}, dt = {early, blight}
rule 1: {late,blight} <= {early,blight}   -> False
rule 2: {early,blight} <= {late,blight}   -> False
rule 3: |tt & dt| / |tt | dt| = 1/3 = 0.333 < 0.5  -> False
rule 4: no synonym group contains either  -> False
                                             class_ok = False
family_of("Late Blight") = "disease" == "disease"  -> category_ok = True
score_issue(...) -> (False, True)
```

Result: `issue_class_ok=False`, `issue_cat_ok=True`. This case is **counted as correct for
precision** and **not counted for `issue_class_accuracy`**. It is exactly the leniency described in
§1.4: the farmer is sent to the fungicide shelf, which is the right shelf, on the wrong product.

---

**Example 3 — a fail that costs.**

Same ground truth. System returns `primary_issue: "aphids"`, `primary_category: "pest"`.

```
tt = {late, blight}, dt = {aphids}
rules 1-3 fail; rule 4: {aphids} hits the {aphid, aphids} group,
                        {late, blight} hits nothing  -> False
                                             class_ok = False
family_of("Late Blight") = "disease" != "pest"     -> category_ok = False
score_issue(...) -> (False, False)
```

Result: answered, and wrong. This case lands in the numerator of `wrong_pct`. It is the outcome the
whole benchmark is built to make visible.

---

**Example 4 — a synonym pass.**

Ground truth: entity `cotton`, issue `American Bollworm`. System returns
`primary_issue: "Helicoverpa armigera"`, `primary_category: "pest"`.

```
tt = {american, bollworm}, dt = {armigera, helicoverpa}
rules 1-3 all fail — no shared tokens at all.
rule 4, group {american_bollworm, helicoverpa, gram_pod_borer, cotton_bollworm, pod_borer, armigera}:
    _syn_hit(tt, g): joined = "american_bollworm"; member "american_bollworm" is a
                     substring of it -> hit
    _syn_hit(dt, g): joined = "armigera_helicoverpa"; member "helicoverpa" is a
                     substring of it -> hit
    both hit -> class_ok = True
```

Result: correct, and correctly so. This is what the synonym list is for.

---

**Example 5 — a false negative the matcher will not forgive.**

Ground truth: entity `maize`, issue `Leaf spot syndrome`. System returns
`primary_issue: "Cercospora leaf spot"` — a clinically reasonable, arguably more precise answer.

```
tokens("Leaf spot syndrome")     -> {syndrome}     ("leaf", "spot" are stop words)
tokens("Cercospora leaf spot")   -> {cercospora}
no subset, Jaccard 0/2 = 0, no synonym group -> class_ok = False
family_of("Leaf spot syndrome") = "disease"   ("syndrome" is a disease keyword)
if the system said primary_category "disease"  -> category_ok = True
score_issue(...) -> (False, True)
```

Result: the system gets family credit and is denied class credit for an answer a pathologist would
accept. Everything that distinguished the ground-truth label was discarded as a stop word.
`issue_class_accuracy` is depressed by cases like this for every system equally.

---

**Example 6 — an unwinnable case, which is why it is not in the suite.**

Ground truth: entity `raspberry`, issue `Raspberry leaf spot`.

```
tokens("Raspberry leaf spot", crop="raspberry")
    "raspberry" == crop  -> dropped
    "leaf", "spot"       -> stop words
                         -> set()
matches(...) guard: `if not tt: return False`
```

No prediction, however perfect, can score `class_ok` here. Two such cases existed in the candidate
set and both were removed; see §6.4 and `tracks/diagnosis/suites/openfield_v1/EXCLUDED.jsonl`.

### 3.9 From judgements to a scored case

`score_record()` in `src/agribench/runner.py` assembles the flat record that the metrics consume.
Note what it computes and when:

- `subject_ok` and `issue_class_ok` are computed for **declined cases too**. Their denominator is
  valid cases, not answered cases, so a system that names the crop and then declines to name the
  disease still gets credit for the crop.
- `issue_category_ok` is computed for every case but **counted only on answered rows** by
  `compute_metrics`. A system cannot bank precision credit for a case it declined, even if the
  category it recorded happened to be right.

Each row reduces to a `CaseScore` (`src/agribench/contract/metrics.py`) — five booleans:
`answered`, `subject_ok`, `issue_class_ok`, `issue_cat_ok`, `errored`. That is the entire input to
the metric arithmetic. Nothing else about the case reaches §4.

---

## 4. The metrics and the two-denominator rule

Defined in `src/agribench/contract/metrics.py`. `docs/METRICS.md` covers the same ground with a
different worked example; this section works the arithmetic at the real suite size.

### 4.1 Three populations

| Population | Definition |
| --- | --- |
| `attempted` | Cases handed to the system. For a complete run, 554. |
| `valid_cases` | `attempted` minus rows the system failed on — a timeout, a 429, a 5xx, an unparseable payload. |
| `answered` | Valid cases where the system returned a **usable diagnosis** rather than declining. |

```
valid_cases = answered + abstained
attempted   = valid_cases + errors
```

An **abstention** is a valid case that is not answered: a well-formed response whose
`primary_category` is missing, empty, or `"unknown"` (`DECLINED_CATEGORIES` in `metrics.py`). Every
other enum value counts as answered, **including `"healthy"`** — declaring a plant healthy is an
answer, not a refusal.

**Errors are missing measurements, not wrong answers.** Scoring a provider timeout as a wrong
diagnosis would conflate infrastructure reliability with diagnostic quality and would let a flaky
network make a good system look dangerous. Errored cases are removed from every denominator and
reported separately as an absolute count. That creates an obvious incentive to launder hard cases
into errors, so: returning an error for a case the system simply found hard is falsification, not
abstention, and a run with a high error count is a provisional result whose error count must be
published next to it.

### 4.2 The formulas

```
precision            = correct_category_among_answered / answered
coverage             = answered / valid_cases
wrong_pct            = (answered - correct_category_among_answered) / valid_cases
subject_accuracy     = subject_ok / valid_cases
issue_class_accuracy = exact_issue_ok / valid_cases
```

| Metric | Denominator | Plain-English question |
| --- | --- | --- |
| `precision` | **`answered`** | When it speaks, how often is it right? |
| `coverage` | `valid_cases` | How often does it speak at all? |
| `wrong_pct` | `valid_cases` | How often does a farmer receive a confidently wrong answer? |
| `subject_accuracy` | `valid_cases` | How often does it identify the crop? |
| `issue_class_accuracy` | `valid_cases` | How often does it name the exact condition? |

`correct_category_among_answered` counts the **second** element of `score_issue` on answered rows.
`exact_issue_ok` counts the **first** element, on all valid rows. `subject_ok` counts
`score_subject`, on all valid rows.

### 4.3 The rule

> **`precision` is the only metric measured over `answered`. Every other metric is measured over
> `valid_cases`.**

This is the single most important thing to internalise before reading any number in this
repository, and mixing the two denominators is how benchmarks accidentally lie.

- Dividing correct answers by `valid_cases` and calling it "precision" **punishes abstention**: a
  system that declines a case it would have got wrong is scored as though it got it wrong, so the
  rational strategy becomes always guessing.
- Dividing wrong answers by `answered` **hides refusal**: a system that answers 5% of cases and gets
  95% of those right looks flawless, and the 95% of the field it walked away from vanishes from the
  number.

`wrong_pct` exists specifically to keep the second failure visible. It is the only metric that puts
wrong answers over the full valid population, and it is the number an agronomist should read first.

Both denominators are published for every entry, plus the error count.

### 4.4 Worked arithmetic: what abstention buys, and what it costs

Three illustrative systems on the full suite. **These are invented round numbers chosen to make the
arithmetic checkable by hand. They are not results** — published baselines are in
`tracks/diagnosis/results/`.
Each ran all 554 cases with zero errors, so `valid_cases = 554` throughout.

**System P — answers everything.** Never declines; guesses when unsure.

```
valid_cases = 554
answered    = 554     (abstained 0)
correct     = 355

precision = 355 / 554 = 0.640794 =  64.1%
coverage  = 554 / 554 = 1.000000 = 100.0%
wrong_pct = (554 - 355) / 554 = 199 / 554 = 0.359206 = 35.9%
correct_rate = 355 / 554 = 64.1%
wrong answers actually delivered: 199
```

**System Q — the same perception, with a confidence gate in front.**

```
valid_cases = 554
answered    = 332     (abstained 222)
correct     = 289

precision = 289 / 332 = 0.870482 = 87.0%
coverage  = 332 / 554 = 0.599278 = 59.9%
wrong_pct = (332 - 289) / 554 = 43 / 554 = 0.077617 =  7.8%
correct_rate = 289 / 554 = 52.2%
wrong answers actually delivered: 43
```

**System R — declines almost everything.**

```
valid_cases = 554
answered    =  55     (abstained 499)
correct     =  53

precision =  53 /  55 = 0.963636 = 96.4%
coverage  =  55 / 554 = 0.099278 =  9.9%
wrong_pct = ( 55 -  53) / 554 = 2 / 554 = 0.003610 =  0.4%
correct_rate = 53 / 554 = 9.6%
wrong answers actually delivered: 2
```

Side by side:

| System | precision | coverage | wrong_pct | correct_rate | wrong answers delivered | correct answers delivered |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| P — answers everything | 64.1% | 100.0% | 35.9% | 64.1% | 199 | 355 |
| Q — gated | **87.0%** | 59.9% | 7.8% | 52.2% | 43 | 289 |
| R — over-cautious | **96.4%** | 9.9% | 0.4% | 9.6% | 2 | 53 |

**Read the table left to right and the story inverts.** Ranked on precision alone, R is the best
system on the page and P is the worst. Ranked on useful output, P produced 355 correct diagnoses
and R produced 53.

What is actually true:

- **Precision rises monotonically with abstention, and it is not free.** Q posts a precision 22.9
  points above P while delivering **4.6× fewer wrong diagnoses** (43 versus 199). Over 554 farmer
  queries that is 156 avoided bad recommendations. That is a real improvement and it is exactly
  what a confidence gate is for.
- **Q also produced 66 fewer correct diagnoses than P** (289 versus 355). The gate traded correct
  answers away to avoid wrong ones, at roughly 1 correct given up per 2.4 wrong avoided. Whether
  that trade is worth making depends on the relative cost of a wrong spray versus a missed
  diagnosis — a question this benchmark deliberately does not answer for you.
- **R is the degenerate corner.** Its 96.4% precision is the best number on the page and it answered
  55 of 554 questions. This is why coverage is mandatory next to precision, in every table, chart
  title, commit message, and headline. **A precision-only leaderboard is trivially gamed by
  abstaining harder, and its winner would be a system that answers once.**

There is no single ranking of P, Q, and R. There is a frontier. The benchmark's job is to publish
the coordinates honestly, not to collapse them into one number, which is why nothing here computes
a composite score and nothing here ranks on precision alone.

### 4.5 Two identities worth memorising

```
wrong_pct    = coverage × (1 − precision)
correct_rate = coverage × precision          # correct diagnoses per valid case
coverage     = correct_rate + wrong_pct
```

Check on Q: `0.599278 × (1 − 0.870482) = 0.599278 × 0.129518 = 0.077617` = `wrong_pct`. ✔

`correct_rate` is not a published headline, but it is what you should compute in your head when you
want to know how much useful output a system produced per photograph. Precision alone cannot tell
you that, and neither can coverage alone. That is the whole reason both are published.

### 4.6 The same numbers under a broken denominator

If you compute "precision" over `valid_cases` instead of `answered`:

```
P: 355 / 554 = 64.1%
Q: 289 / 554 = 52.2%      <- Q now ranks BELOW P
R:  53 / 554 =  9.6%      <- R ranks last
```

Q, which delivers a quarter as many wrong answers as P, would rank second. Every abstention would
be charged as though it were an error. This is the specific mistake the two-denominator rule exists
to prevent, and it is a common one — including in this project's own pre-1.0 history (§8.5).

### 4.7 A run with errors

A system attempts all 554 cases; 22 calls fail with provider 5xx.

```
attempted   = 554
errors      =  22
valid_cases = 532
answered    = 400     (abstained 132)
correct     = 348

precision = 348 / 400 = 0.870000 = 87.0%
coverage  = 400 / 532 = 0.751880 = 75.2%
wrong_pct = (400 - 348) / 532 = 52 / 532 = 0.097744 =  9.8%
```

`coverage` uses **532**, not 554. Using `attempted` would give 72.2% and would charge the system for
its provider's downtime. The published row still carries `errors = 22` so a reader can apply their
own discount. A run whose `valid_cases` drops meaningfully below the suite size is a provisional
result at best; state the count, do not bury it.

### 4.8 Undefined values and reporting conventions

Every ratio is `None` — never `0.0`, never `1.0` — when its denominator is zero
(`_ratio()` in `metrics.py`). An abstain-everything run has no precision to report, and emitting
either `0%` ("always wrong") or `100%` ("never wrong") would be false. Render `None` as `n/a`.

- Compute in full floating-point precision; round only for display, to one decimal place.
- Never round an intermediate value, and never compute a percentage from an already-rounded one.
- Always publish `valid n` and the error count next to the percentages.
- **Never publish `precision` without `coverage` immediately adjacent** — not in a table, not in a
  chart title, not in a commit message, not in a headline.
- Two entries are comparable only if they ran the same suite, the same case set, the same prompt,
  and the same matcher, verified per §8.

`agribench compare` enforces some of this mechanically. It emits a comparability warning when runs
came from different manifests, when valid-case counts differ across runs, and when any run answered
under 50% of its cases (`_comparability_warnings` in `src/agribench/cli.py`).

---

## 5. Abstention as a first-class behaviour

### 5.1 Three outcomes, kept strictly distinct

This is the part adapters most often get wrong, and getting it wrong silently corrupts the headline
number.

| Outcome | How the adapter signals it | Counted as |
| --- | --- | --- |
| **answered** | `Prediction(answered=True, ...)` or `Prediction.from_payload(...)` | in `valid_cases`, in `answered` |
| **abstained** | `Prediction.abstain(...)`, or `primary_category` of `""`/`"unknown"` | in `valid_cases`, **not** in `answered` |
| **failed** | `raise AdapterError` (or `TransientAdapterError` / `AdapterConfigError` / `AdapterResponseError`) | **excluded from `valid_cases`** |

Timeouts, 429s, 5xx responses, provider refusals, unparseable payloads: **raise**. Never return an
abstention to paper over an infrastructure failure — it silently inflates precision by moving a
case out of the wrong-answer numerator without removing it from the coverage denominator, and it is
treated as a falsified result. Equally, never raise to mean "the model was unsure"; that is an
abstention, and it costs coverage, which is the honest price.

`Prediction.abstain()` accepts the fields the system *did* produce. A system that named the crop but
would not name the disease should report the crop: `subject_accuracy` is measured over all valid
cases, so it gets credit for what it knew.

### 5.2 Why abstention is not penalised twice

Look at where an abstained case lands in each formula:

| Metric | Effect of one more abstention |
| --- | --- |
| `precision` | Case leaves the denominator **and** the numerator. Precision is unchanged in expectation; it rises if the case would have been wrong, falls if it would have been right. |
| `coverage` | Numerator falls, denominator unchanged. **Coverage falls.** |
| `wrong_pct` | Numerator falls if the case would have been wrong; denominator unchanged. **Falls or stays.** |
| `subject_accuracy` | Unaffected — still scored, still in the denominator. |
| `issue_class_accuracy` | Numerator falls if the declined answer would have matched; denominator unchanged. |

So abstention has exactly one cost — **coverage** — and it is paid in full. It does not
additionally get charged as a wrong answer. A benchmark that did both would be double-counting: the
case would lower coverage *and* raise the wrong-answer rate, which describes a system that both
refused and misled, which is not what happened.

The symmetric point matters just as much: **abstention earns nothing.** A declined case adds no
correct answer anywhere. `correct_rate = coverage × precision` falls in lockstep with coverage. The
incentive structure is a genuine trade, not a free lunch, which is why systems P, Q and R in §4.4
sit on a frontier rather than in a ranking.

### 5.3 Why this is the right incentive for agricultural advice specifically

Most benchmarks treat a wrong answer and a missing answer as roughly equivalent costs. In
agricultural advisory they are not remotely equivalent, and the asymmetry runs in a specific
direction.

A confident wrong diagnosis sends a farmer to buy the wrong input. Concretely: a bacterial leaf
blight misdiagnosed as a fungal blight sends them to buy a fungicide. The farmer then pays for a
product that cannot work, applies it during the window in which the correct intervention would have
worked, watches the problem continue, and concludes that the advisory is worthless. Four costs, all
real, none recoverable:

1. **Input cost.** Money spent on a product with no effect on the actual pathogen.
2. **The spray window.** The intervention that would have worked has a time limit; it is now past.
3. **Agronomic harm.** Unnecessary pesticide application selects for resistance, damages
   beneficials, and carries residue and pre-harvest-interval implications.
4. **Trust, which does not come back.** A farmer who is burned once by an automated recommendation
   is not a user you get to re-acquire.

Compare the cost of "I'm not sure — send a clearer photo, or ask an extension officer." The farmer
loses time and is mildly annoyed. That is it. The problem is still there, still diagnosable, and
the correct intervention window is usually still open.

**A benchmark that scores "I don't know" identically to "aphids" is measuring the wrong thing.** It
rewards the behaviour that is expensive in the field and punishes the behaviour that is cheap. Every
system optimised against such a benchmark learns to guess, because guessing is free there and is
not free anywhere else.

So the headline here is precision among answered cases, published next to coverage, with
`wrong_pct` — confidently wrong, over all valid cases — as the number that carries the actual field
cost.

### 5.4 The obvious way to game this, and why it does not work

Abstain harder. Answer only the cases you are certain about. Post 96.4% precision at 9.9% coverage,
like System R.

The countermeasures are structural, not procedural:

1. **Coverage is published in the same row, always.** Every rendering path in the tooling prints
   them together — the terminal table, the markdown report, the CSV, the comparison table. There is
   no code path that emits precision alone.
2. **Nothing ranks on precision alone.** `agribench compare --sort precision` is the default sort
   key, but the table it prints carries coverage and `wrong_pct` in adjacent columns and a
   "How to read this" note beneath it.
3. **`agribench compare` warns automatically** when any run's coverage is below 50%, in the run's
   own output: "*answered only N% of cases — its precision is computed over that subset, not the
   benchmark*".
4. **`correct_rate = precision × coverage`** is recoverable by any reader from the published fields
   in one multiplication, and it collapses the gaming immediately.

The remaining honest concern is a subtler one: an adapter author who filters their own model's weak
answers *inside the adapter*, turning a system comparison into an adapter comparison. The rule is:

> **Do not abstain on the benchmark's behalf.** If your model returned a usable diagnosis, report it
> as answered even when you suspect it is wrong. The one legitimate exception is a system that
> genuinely ships a confidence gate in production — then the gate is part of what is under test and
> must run exactly as it ships, and `system_type` must be `"system"`.

---

## 6. Suite construction: `openfield_v1` as the worked example

### 6.1 The double filter

Every candidate case had to clear two independent gates. Failing either meant exclusion, and
neither gate could be waived for a case that was interesting.

**Gate 1 — a redistributable licence, or an explicit link-only route.** A public benchmark that
cannot say where an image came from and under what terms is not redistributable and not
reproducible. The licences accepted for bundled cases are enumerated in code
(`REDISTRIBUTABLE_LICENCES` in `src/agribench/manifest.py`): `CC0`, `CC-BY`, `MIT`, `Apache-2.0`,
`public-domain`. Anything else must be routed through the link-only mechanism (§7) or dropped.
Manifest validation enforces this — a bundled case with a non-redistributable licence is a hard
error, not a warning.

**Gate 2 — label provenance.** The condition label had to come with the image from an identifiable
public source, not be assigned by us. `source` and `license` are **required** manifest fields for
exactly this reason, and per-image provenance is recorded in
`tracks/diagnosis/suites/openfield_v1/ATTRIBUTION.csv`.

Gate 2 has a large and honest consequence that you should hold against every number here: **labels
are inherited, not re-annotated.** No plant pathologist re-examined these images for this benchmark.
Naming is heterogeneous across sources, some labels are coarse damage descriptions rather than
diagnoses, and some fraction of the ground truth is simply wrong. That fraction has not been
measured. See [`LIMITATIONS.md` §6](LIMITATIONS.md#6-label-noise-and-set-composition).

The alternative — re-annotating 554 images to a single taxonomy — would produce a cleaner benchmark
and a much smaller, less traceable one, since the re-annotation would then be an unverifiable claim
by us rather than a citable claim by the original publisher. Inherited labels with published
provenance was judged the better trade for a public benchmark. It is a trade, and this is the side
that was chosen.

### 6.2 What the suite contains

| | |
| --- | --- |
| Cases | **554** |
| Crops (distinct `entity` values) | **79** |
| Distinct crop × condition pairs | **277** |
| Images per pair | **exactly 2** (277 × 2 = 554) |
| Issue families | disease **404**, pest **150** |

Licences of the bundled cases:

| Licence | Cases |
| --- | ---: |
| CC-BY | 421 |
| CC0 | 88 |
| MIT | 31 |
| Apache-2.0 | 14 |
| **Total** | **554** |

Every image is redistributable or link-only. There are no scraped-without-permission images, no
proprietary customer photographs, and no synthetic renders.

**Crop pest and disease only.** Every case is `disease` or `pest`. There are **no animal cases, no
weed cases, no nutrient-deficiency cases, no abiotic cases, and no healthy cases**, even though the
frozen schema accepts all of those categories. Two things follow and both are serious:

- Those categories are **completely untested** on this suite. A system could route every nutrient
  deficiency to the wrong answer and score identically here.
- **There are no negative controls.** A system that assumes every image contains a problem is never
  penalised, because every image does. The real-world false-positive rate — telling a farmer to
  spray a healthy crop — is invisible to this benchmark. That is arguably its most important blind
  spot, and it is one reason `openfield_v1` is version 1 of something rather than a finished
  artifact.

These are open-field photographs, not lab plates: variable lighting, cluttered backgrounds, multiple
leaves in frame, occasional co-occurring problems. That is deliberate, and it is also the source of
much of the label noise.

### 6.3 The 2-images-per-condition design, and what it forbids

The suite is 277 crop × condition pairs with **exactly two cases each**. Not approximately two —
exactly two, for every pair, verifiable in one line:

```bash
python - <<'PY'
import json, collections
rows = [json.loads(l) for l in open("tracks/diagnosis/suites/openfield_v1/manifest.jsonl") if l.strip()]
pairs = collections.Counter((r["entity"], r["issue"]) for r in rows)
print(len(rows), "cases,", len(pairs), "pairs, sizes:", set(pairs.values()))
PY
```

**Why this shape.** With a fixed budget of cases, the choice is between many conditions shallowly
sampled and few conditions deeply sampled. This suite chose breadth: 277 distinct diagnostic
problems across 79 crops, rather than, say, 20 conditions with 28 images each. The reasoning is that
the failure mode that matters in a deployed advisory is *encountering something outside the
familiar set* — an unusual crop, a regionally specific pest — and a suite that samples 20 conditions
deeply cannot see that failure at all. Breadth also makes the suite harder to overfit: there is no
small set of conditions to tune against.

The price is paid in statistical resolution, and it is not negotiable:

> **Do not compute per-condition accuracy from this suite.** At n = 2, a per-condition rate can only
> take the values 0%, 50%, or 100%. Any ranking, heatmap, or "hardest conditions" table built on
> those numbers is noise dressed as analysis.

What *is* supported:

| Slice | Supported? | Why |
| --- | --- | --- |
| Whole suite | **yes** | n = 554 |
| Per issue family | **yes** | disease n = 404, pest n = 150 — the finest slice that means anything |
| Per condition | **no** | n = 2 |
| Per crop | **no** | median 6 cases per crop, range 2–32; 21 of 79 crops have 3 cases or fewer |
| Per region / cropping system | **no** | the set is not balanced by region and no such field exists |

`agribench report --by family` is supported and printed by default. `agribench report --by entity`
exists because it is occasionally useful for debugging an adapter, **not** because per-crop
comparisons are supported by this set. Do not publish it as a result.

### 6.4 Removing unwinnable cases

Ten cases were removed from the candidate set because the frozen contract makes them impossible to
score fairly. They are not deleted — they are recorded, with their reason, in
`tracks/diagnosis/suites/openfield_v1/EXCLUDED.jsonl`, so the removal is auditable rather than a silent convenience.

| Reason | Cases | Labels involved |
| --- | ---: | --- |
| `family_conflict` | 8 | `Insect feeding damage syndrome` (6 cases), `Pest feeding damage syndrome` (2 cases) |
| `unmatchable_ground_truth` | 2 | `Raspberry leaf spot` |

**The 8 family-conflict cases.** These carry a manifest `issue_family` of `pest`, which is
agronomically right — they are insect feeding damage. But `family_of()` classifies the label as
`disease`, because `syndrome` is on the disease keyword list and precedence gives it the win
(§3.5). Scoring uses the derived family, not the manifest's. So on these cases a system that
correctly answers `pest` would receive **no** category credit, and a system that answers `disease`
would receive it. That is not a hard case; it is a case where the scorer is wrong and the correct
answer is the one that loses.

**The 2 unmatchable cases.** Ground truth `Raspberry leaf spot` on entity `raspberry` tokenises to
the empty set: `raspberry` is dropped as the crop, `leaf` and `spot` as stop words (Example 6 in
§3.8). `matches()` returns `False` for every possible prediction. No system can earn `class_ok`,
ever.

**Why this is a fairness measure, not a convenience.**

The distinction turns on *who* the case is unfair to. These ten cases are not merely difficult —
difficult cases are the point of a benchmark and none were removed for being hard. They are cases
where the **scoring function** is broken in a way that is *directionally biased*:

- On the family-conflict cases, the correct answer is scored wrong and a specific wrong answer is
  scored right. A system whose agronomy is better loses to one whose agronomy is worse. That is not
  a hard case; it is an inverted one.
- On the unmatchable cases, no answer can score. Every system loses equally, so the *comparison*
  survives — but the absolute `issue_class_accuracy` is depressed by a defect in the tokeniser
  rather than by anything about the systems. Keeping them would mean publishing a number that is
  pessimistic for a reason unrelated to diagnosis.

The alternative to removing them was fixing the matcher, and §2.4 explains why that door is closed.
The remaining alternative — keeping them and documenting them — was rejected for the eight
inverted cases specifically, because a documented inversion is still an inversion: readers do not
apply footnotes to leaderboard rows.

Note the limit of this measure. It removes the cases where the *specific* labels in this suite
collide with the *specific* stop list and keyword lists. It does **not** remove the general classes
of defect — the matcher still rewards vagueness, the synonym path still leaks, and family credit is
still unavailable on labels that resolve to `unknown`. Those are measured, published, and left in
place. See §9.

`agribench validate-manifest` re-derives both checks on any manifest and reports them as warnings,
so anyone building a new suite on this contract finds their own unwinnable cases before publishing
a number derived from them:

```bash
agribench validate-manifest --manifest tracks/diagnosis/suites/openfield_v1/manifest.jsonl
```

---

## 7. Data distribution: bundled, link-only and source-only cases

This repository ships **a manifest, not a pile of photographs**. Images are stored by content hash
and are obtained by the operator, through one of three routes.

### 7.1 The three routes

| | `bundled` | `link-only` | `source-only` |
| --- | --- | --- | --- |
| Licence | must be redistributable: `CC0`, `CC-BY`, `MIT`, `Apache-2.0`, `public-domain` | any, including all-rights-reserved | any, including unknown |
| Manifest carries | `image_sha256` (**required**) | `image_url` (**required**), `image_sha256` optional until first fetch | `image_sha256` and `source` (**both required**) |
| How the operator gets the bytes | `agribench stage --from <dir>` — copied from a local dataset checkout, matched by content | `agribench fetch` — downloaded from the origin server | obtain the named upstream dataset from its publisher, then `agribench stage` |
| Redistributed by this repository | in principle yes, subject to attribution | **never** | **never** |
| One command? | yes, given a local copy | yes | **no** |
| Used by | `openfield_v1` | `field_v1` | `clean_v1` |

`distribution` defaults to `bundled` when a case does not say (`DEFAULT_DISTRIBUTION` in
`src/agribench/manifest.py`). Bundled is the stricter default on purpose: it demands a digest up
front and a redistributable licence, so an omission fails loudly instead of silently creating an
unverifiable case.

**Why `source-only` exists.** Some images are neither redistributable nor individually addressable.
Kaggle, Roboflow, Mendeley and parquet-packed HuggingFace datasets ship archives; there is no URL
that returns one image, so `link-only` has nothing to put in `image_url`. Marking such a case
`bundled` instead would assert a redistribution right nobody granted — the single most expensive
mistake this repository could make. `source-only` says the true thing: here is the digest, here is
the dataset it came from, go and get that dataset. A suite of these is reproducible, but not in one
command, and its README must say so plainly rather than implying otherwise.

Manifest validation enforces the asymmetry as hard errors, not warnings:

```
bundled     + no image_sha256              -> error: "image_sha256 is required for a bundled case"
bundled     + non-redistributable licence  -> error: "licence ... does not permit redistribution,
                                                      so the case cannot be bundled"
link-only   + no image_url                 -> error: "link-only cases must carry an image_url"
link-only   + non-http(s) image_url        -> error
source-only + no image_sha256              -> error: missing required field
```

These are authoring rules rather than runtime ones, and `agribench validate-manifest` is the only
place they are enforced — `agribench run` deliberately uses a permissive loader, so that validation
is never stricter than running.

Worked examples of every shape, including a hand-authored unpinned case and the same case after
pinning, are in `tracks/diagnosis/templates/link-only.example.jsonl`.

### 7.2 Why link-only exists

**The most realistic images are usually the ones nobody has licensed for redistribution.**

An extension-service photograph, a research figure, a grower's own picture of the problem in their
field — these are the images that look like what a deployed advisory actually receives. They are
also, almost without exception, all-rights-reserved or unlicensed.

A benchmark that accepts only CC-licensed images is biased toward whatever happens to be
CC-licensed, which is disproportionately: curated dataset images, well-lit, centred, single-leaf,
often photographed against a uniform background specifically to be a training image. That is not the
distribution a system meets in the field, and a benchmark built exclusively from it systematically
overstates field performance.

The link-only route resolves the conflict without anyone redistributing anything they may not. The
repository ships the **reference** and the **ground truth**; whoever runs the benchmark downloads
the bytes themselves, directly from the origin, under whatever terms that origin sets. The case
participates in scoring exactly like any other. Nothing copyrighted enters this repository —
`.gitignore` and a test both enforce that image bytes for link-only cases never get committed.

`openfield_v1` as committed is entirely bundled. The link-only mechanism is available for this and
future suites, and the fetch path, the pinning protocol, and the validation rules are all live and
tested.

### 7.3 The SHA-256 pinning protocol

A URL is a promise that can be broken. `image_sha256` is what turns it back into a fact.

**Authoring flow** (`src/agribench/datasets/fetch.py`):

1. **Author the case with a URL and no digest.** You do not have the file yet, so you cannot state
   its hash. Manifest validation permits exactly this, and only for link-only cases:

   ```json
   {"case_id": "of1-l0001", "entity": "rice_paddy", "image": "images/of1-l0001.jpg",
    "issue": "Sheath Blight", "issue_family": "disease",
    "license": "all-rights-reserved", "source": "University Extension",
    "distribution": "link-only", "image_url": "https://example.org/path/to/sheath-blight.jpg"}
   ```

2. **Fetch and pin.**

   ```bash
   agribench fetch --manifest bench/<suite>/manifest.jsonl --pin
   ```

   Each image is downloaded, hashed, written to `images/<sha256><ext>`, and the observed digest
   written back into the manifest. `image` is rewritten to the content-addressed filename.

3. **Commit the manifest.** Not the bytes.

From that point the case is pinned and every future download is verified against the digest.

**What `--pin` will not do.** `pin_manifest()` touches **only** cases that had no `image_sha256`. An
already-pinned case is never rewritten, even if the origin now serves something different. Silently
re-pinning would erase the exact check that detects a link serving different bytes than the ground
truth was written against — the tool would helpfully overwrite the evidence that the case has
broken.

**Safety limits on the fetch path**, so that a manifest entry cannot be turned into a local file
read or an unbounded write:

| Control | Value |
| --- | --- |
| Allowed URL schemes | `http`, `https` only — notably **not** `file://` or `ftp://` |
| Maximum download | 64 MiB, checked against the declared `Content-Length` **and** enforced while streaming |
| Retries | 2 additional attempts by default, with exponential backoff |
| Politeness pause | 0.5 s between downloads by default; origins are often small institutional hosts |
| User agent | Identifies the tool and links to the project — several extension sites reject the default urllib agent outright |

**A suite with unpinned cases is usable but not yet reproducible**, and `agribench fetch` says so
loudly: it prints the unpinned count up front and emits a `still unpinned` warning for every case
whose fetch did not succeed.

### 7.4 Link rot, and what a digest mismatch means

Two distinct failures, reported separately because they mean different things.

**`failed`** — the URL did not yield a file. A 404, a DNS failure, a timeout, a TLS error, a host
that has gone away. This is ordinary link rot. The case cannot be run; the suite is incomplete; the
run is not a full-suite result. `agribench fetch` exits non-zero.

**`digest-mismatch`** — the URL yielded a file, and it is **not the file the ground truth was
written against**. This is the serious one.

```
MISMATCH of1-l0002: expected 3b1f0c9e5a7d…, origin served 9c4e2a7b6d15…
```

The downloaded bytes are discarded, never written into the images directory, and never scored. The
tooling then prints:

> A digest mismatch means the origin now serves different bytes than the ground truth was written
> against. Do not re-pin it blindly: re-check the label against the new image first.

The reason for that instruction is that a mismatch has several possible causes and only one of them
is benign:

| Cause | What it means |
| --- | --- |
| The host re-encoded or re-compressed the image | Same picture, different bytes. Benign, but the case is no longer byte-identical to what other people ran, so scores are no longer strictly comparable. |
| The host replaced the image at that URL | **The ground-truth label may now describe a different photograph.** Re-pinning without re-checking the label silently creates a mislabelled case. |
| The URL now serves an error page, a placeholder, or a login wall | Whatever was downloaded is not a benchmark image at all. |
| Man-in-the-middle or a compromised origin | Rare, but the digest is the only thing that would tell you. |

The same protection covers bundled cases from the other direction. `stage_images()` matches source
files **by content, never by filename**, so a source tree that has renamed or reorganised everything
still works, and a file that has been re-encoded or truncated is rejected rather than silently
benchmarked. `agribench stage --audit --deep` re-hashes everything already staged and reports
anything that has drifted:

```bash
agribench stage --manifest tracks/diagnosis/suites/openfield_v1/manifest.jsonl --audit --deep
```

This is also why `AGENTS.md` forbids re-encoding, resizing, or re-compressing a benchmark image
under any circumstances: `image_sha256` pins every file byte-for-byte, and a re-encode silently
invalidates every run ever done against that case.

### 7.5 Preflight: a run cannot start on a half-staged suite

`agribench run` audits image presence before calling the system under test, and refuses to start if
any case image is missing (`cmd_run` in `src/agribench/cli.py`). The reason is that the run file it
would otherwise leave behind *looks like a legitimate result* — a table of errors, or worse, a table
of confident guesses from an adapter that tolerates a missing file. `--allow-missing-images`
overrides it for harness debugging and is documented as producing something that is not a valid
suite score. The offline `echo` fixture is exempt, because its whole purpose is to work before any
dataset exists.

---

## 8. Reproducibility

Four independent mechanisms, each answering a different question about a published number.

| Mechanism | Question it answers | Artifact |
| --- | --- | --- |
| Contract lock | Was this scored with the same ruler? | `src/agribench/contract/contract.lock.json` |
| Manifest fingerprint | Was this scored on the same cases? | `manifest_sha256`, stamped into every run file |
| Run file | What did the system actually say? | `results/<slug>.jsonl` |
| Re-scoring | Did the model improve, or did the ruler move? | `agribench score --run … --manifest …` |

### 8.1 The contract lock

`contract.lock.json` pins the SHA-256 of every file in the frozen zone, plus the hash and character
length of the prompt *string* (not just its file), plus the contract version:

```bash
python -m agribench.contract.verify
# contract v1.0 OK (7 files pinned, prompt 552 chars)
```

`verify()` recomputes and checks, in order: the contract version against the package constant; the
prompt's SHA-256 and length; `matching.py`'s digest, including a self-consistency check that the
lock's dedicated `matching_sha256` agrees with its own `files` entry; every pinned file's digest;
and — the check people forget — **any `.py` file present in the frozen zone that the lock does not
pin at all**. You cannot smuggle in a new scoring module.

Exit status 1 means the frozen zone drifted, and the report names exactly what changed. When it
fails legitimately — an intentional contract change — the fix is to bump `contract_version`,
regenerate the lock, and **re-run every published result under the new version**. The fix is never
to paste in the new hash.

Run it before you start work, to prove the tree was clean when you got it, and after every change,
to prove you did not disturb it.

### 8.2 The manifest fingerprint

`compute_manifest_sha256()` in `src/agribench/manifest.py` reduces a case set to one digest:

- taken over `(case_id, image_sha256)` pairs **only**;
- sorted by `case_id`, each pair encoded as `f"{case_id}\x00{image_sha256}\n"` in UTF-8.

It is therefore **insensitive** to line order in the manifest file and to fields that describe
provenance rather than measurement (`license`, `source`) — correcting an attribution typo must not
orphan every run ever done against the suite. It is **sensitive** to a case being added, removed,
renamed, or repointed at different image bytes.

The value is stamped into every run file's metadata. `RunFile.validate_against()` refuses to check a
run against a manifest whose fingerprint differs:

```
run 'example' was produced against manifest 3f9a1c0e21b4… but was checked against
7c02de5518af… (openfield_v1)
    hint: results from different case sets are not comparable
```

It also rejects case ids not in the manifest, duplicate case ids, and — under
`require_complete=True` — a partial run. A partial run is a valid file and a legitimate smoke test.
It is not a leaderboard entry.

### 8.3 Run files

A leaderboard row is an assertion; **a run file is the evidence**. Every published entry ships one.

Two encodings, one data model (`src/agribench/record.py`). JSON Lines is canonical — a metadata
header line followed by one result per line, so a long run streams to disk and an interrupted
process leaves everything before the cut readable:

```
{"run_file_version": 1, "metadata": {...}}
{"case_id": "of1-0000", "entity": "aloe_vera", "issue": "Anthracnose", ...}
```

Metadata carries what makes a run comparable: `manifest_id`, `manifest_sha256`, `system_name`,
`system_type`, `contract_version`, `created_at`, plus free-text `notes` and an `extra` dict for
non-secret provenance (model id, endpoint host, decoding parameters).

Each result row carries the ground truth it was scored against, the system's prediction **verbatim
including the unscored fields**, and the three scored flags. That combination is what makes both
directions possible: a table can be rebuilt without re-running the matcher, and the matcher can be
re-run without re-running the system.

Deliberately **not** carried: absolute filesystem paths, source file names, harness-internal fields,
and credentials. A run file is a published artifact; it inherits nothing it does not need.
`describe()` output lands in this file, so it must report *that* a key was present, never what it
was.

Operational properties: runs are written atomically (temp file plus `os.replace`, so a reader never
observes a half-written file); `agribench run` snapshots every 25 cases by default and **resumes**
by default, so Ctrl-C is safe and a half-finished run is a valid artifact.

### 8.4 Re-scoring frozen predictions

This is the mechanism that separates *"the model improved"* from *"the ruler moved"*, and it is the
reason predictions are stored verbatim.

```bash
# recompute metrics from the stored per-case flags — no model call, no ground truth needed
agribench score --run results/example.jsonl

# re-derive every flag from the stored predictions against ground truth,
# using the CURRENT matcher
agribench score --run results/example.jsonl --manifest tracks/diagnosis/suites/openfield_v1/manifest.jsonl
```

The second form re-runs `score_subject` and `score_issue` over the recorded `subject`,
`primary_issue`, and `primary_category` of every non-errored row, discarding the stored verdicts.
The output records `rescored_against_manifest` and the `contract_version` it was scored under, so
the artifact says which ruler produced it.

`RunFile.rescored()` is the programmatic form, and it restamps the copy with the current contract
version by default — because after re-scoring, the numbers belong to that contract, not to the one
the run was originally produced under. Labelling them otherwise would be the exact confusion the
mechanism exists to prevent.

The workflow that follows: when a system's number changes between two dates, re-score the *older*
run file under the *current* contract. If the old number moves, the ruler moved. If it does not, the
system did. Neither claim is available to a benchmark that publishes only summary tables, which is
why a row that cannot be re-scored from a raw file does not go in the table.

### 8.5 Historical numbers, and why they differ

`src/agribench/legacy.py` exists so pre-1.0 published numbers stay auditable rather than being
quietly disowned. It reproduces the v0.2-era formulas **bit-exactly, asymmetries included** — and
the asymmetries were the problem:

| Metric | Pre-1.0 behaviour | Contract 1.0 behaviour |
| --- | --- | --- |
| `issue_class_accuracy` | Abstaining systems divided by **answered**; single-call systems divided by **valid cases** | Every system divided by valid cases |
| `wrong_pct` | Single-call systems charged for every case they got wrong *including ones they never answered*; abstaining systems charged only for answered-and-wrong | Only answered-and-wrong, for every system |

The second rule — a declined case is not a wrong answer — is the defensible one, and the historical
code applied it to only one kind of system. On a run that answers half the set, the
`issue_class_accuracy` asymmetry alone roughly doubles the reported number for the abstaining
system. Putting the two in one column was apples-to-oranges and it flattered the abstaining system.

Contract 1.0 applies one rule to every system, which is the only basis on which a public leaderboard
can rank systems against each other. `legacy.py` is read-only, stdlib-only, takes already-scored
rows, and exists to **verify past results, never to produce new ones**. A golden test
(`tests/golden/legacy_v02_published.json`) pins the reproduction.

### 8.6 What CI checks

**83 tests pass.** CI runs on Linux, macOS, and Windows across Python 3.11, 3.12, and 3.13, and
executes four things:

1. **Contract verification** — `python -m agribench.contract.verify`. Runs *before* the tests,
   because if the frozen zone drifted without a lock bump, nothing below it is trustworthy.
2. **The test suite** — `python -m pytest -q`.
3. **Manifest validation** — `agribench validate-manifest` against every shipped suite, under the
   strict distribution and licence rules of §7.1.
4. **A privacy guard** — a separate job that greps the whole tree for absolute developer paths,
   credential-shaped strings, internal identifiers, attributed TODOs, and image binaries committed
   anywhere. Any hit fails the build.

A fifth check runs on Linux: the scoring path is imported in an isolated interpreter and asserted to
have loaded **nothing from `site-packages`**. Core runtime dependencies are deliberately empty, so
any published number can be reproduced from a bare CPython install with no dependency resolution.
That constraint is enforced, not merely intended.

Local definition of done, from `AGENTS.md`:

```bash
python -m agribench.contract.verify   # exits 0
python -m pytest -q                   # passes
python -m ruff check .                # clean (contract/ excluded by configuration)
agribench run --adapter echo --limit 20   # still produces a metric block
```

---

## 9. Known limitations

`docs/LIMITATIONS.md` is the authoritative list, with counts measured against the committed manifest
and the frozen matcher. It is not summarised here, because a summary is how a limitation gets
quietly softened. Read it before quoting any figure from this repository.

What it covers, and where this document connects to it:

| `LIMITATIONS.md` section | Relates to |
| --- | --- |
| §1 The matcher is generous, and it rewards vagueness | §3.3 rule 2 (bidirectional subset), §3.4 (the synonym leak) |
| §2 The empty-subject quirk | §3.6 |
| §3 Stop-word false negatives | §3.1, §3.2, Example 5 |
| §4 Family credit unavailable on some cases | §3.5 (`family_of` → `unknown`), §3.7 property 2 |
| §5 Single image, no context | §1.3 |
| §6 Label noise and set composition | §6.1 gate 2, §6.2, §6.3 |
| §7 The images are third-party | §6.1 gate 1, §7 |
| §8 What this benchmark does not measure at all | — |
| §9 How to state a result honestly | §4.8, §10 |

Two items from §7 and §8 deserve repeating here because they bound what any number from this suite
can support:

**Training-data contamination is likely and unquantified.** These are public datasets, several
well-known and mirrored for years. Any system trained on a broad web crawl may have seen these exact
images during training. This inflates absolute scores by an unknown amount and **does not inflate
them evenly** — a system trained on public agricultural corpora benefits more than one trained on
proprietary field photography. Treat cross-system deltas with more confidence than absolute levels,
and treat both with less confidence than you would for a held-out private set.

**Treatment quality is never evaluated.** The benchmark scores the diagnosis. What to apply, at what
rate, with what pre-harvest interval, is not scored at all. A correct diagnosis followed by a
dangerous recommendation scores perfectly here.

---

## 10. How to submit a result

### 10.1 Current status

The Diagnosis track publishes six frontier vision-language baselines on `field_v1` and `clean_v1`,
in [`tracks/diagnosis/results/`](../tracks/diagnosis/results/). Every one is backed by its raw
per-case run file and can be re-scored offline with `agribench score`. `openfield_v1` has no
baselines yet.

A row goes on a leaderboard **only** when a committed raw run file backs it. Filling a table in with
numbers from an unreproduced run, an estimate, an example, or a figure seen elsewhere is explicitly
out of bounds, and a row that cannot be re-scored from source is removed rather than footnoted.

### 10.2 The procedure

**1. Verify the contract before you start.**

```bash
python -m agribench.contract.verify
```

If this fails on a clean checkout, stop and report it. Do not proceed — nothing you produce would be
comparable.

**2. Obtain the suite's images.** How depends on the suite's distribution mode (§7.1):

```bash
# field_v1 -- link-only: downloads from the public origin, digest-verified
agribench fetch --manifest tracks/diagnosis/suites/field_v1/manifest.jsonl --yes

# openfield_v1 -- bundled: stage from a local copy of the upstream datasets
agribench stage --manifest tracks/diagnosis/suites/openfield_v1/manifest.jsonl --from <your-image-dir>

# clean_v1 -- source-only: obtain each upstream dataset in SOURCES.csv, then stage as above
```

Both verify every file against its recorded SHA-256. A `missing` or `corrupt` count above zero means
the suite is not runnable yet.

**3. Write one adapter class**, anywhere importable — `my_bench.py` is fine. Subclass `Adapter`, set `name`
and `system_type`, implement `predict()`. **No other module in the repo should need to change.** If
you find yourself editing the runner, the CLI, or the scoring path to accommodate one system, the
adapter interface is being abused — stop and ask. `predict()` is called from a thread pool and must
be thread-safe.

**4. Import the prompt; never retype it.**

```python
from agribench.contract.prompt import PROMPT
```

**5. Credentials from the environment only.**

```python
key = os.environ["AGRIBENCH_PROVIDER_API_KEY"]
```

Add the *variable name* to `.env.example` with an empty value. Never a real key — not as a default,
not as a fallback literal, not in a test fixture, not in a comment.

**6. Keep the three outcomes distinct.** §5.1. This is the most common and most damaging adapter
bug.

**7. Smoke, then run the full suite.**

```bash
agribench run --adapter my_bench:MyAdapter --manifest tracks/diagnosis/suites/field_v1/manifest.jsonl --limit 8
agribench run --adapter my_bench:MyAdapter --manifest tracks/diagnosis/suites/field_v1/manifest.jsonl --out runs/mine.json
```

Read the smoke output by eye before spending a full run. A full run must cover all 554 cases; a
partial run is not a leaderboard entry. Confirm `describe()` returns useful metadata — model id,
endpoint host, decoding parameters — and **no secret**.

**8. Publish the raw file, not just the summary.** Copy the completed run to `results/<slug>.jsonl`
and record its SHA-256 alongside the leaderboard row. `runs/` and `reports/` are git-ignored
scratch; `results/` is committed. **A row nobody can re-score from a raw file does not go in the
table.**

**9. Report precision and coverage together, always.** Not just in the table: in the commit message,
the PR description, the chart title, the announcement. A high precision at 4% coverage is not a
result, and presenting it as one is the specific failure mode this benchmark exists to prevent.

**10. Verify last.**

```bash
python -m agribench.contract.verify && python -m pytest -q && python -m ruff check .
```

### 10.3 What a submission must include

| Item | Why |
| --- | --- |
| `results/<slug>.jsonl` and its SHA-256 | So the row can be re-scored from source by anyone |
| `manifest_sha256` in the run metadata | So a reader can confirm it ran on this case set (§8.2) |
| `contract_version` in the run metadata | So a reader can confirm it was scored with this ruler (§8.1) |
| `system_type`, declared honestly | So readers compare like with like (§1.2) |
| Error count, published next to `valid n` | So a reader can discount an unreliable run (§4.1) |
| `describe()` metadata, secret-free | Model id, endpoint host, decoding parameters (§8.3) |
| A note on any schema relaxation | If your provider could not honour the strict schema (§2.2) |

### 10.4 What will get a submission rejected

- A modified prompt, in any respect, including punctuation and translation.
- Any edit under `src/agribench/contract/`.
- An adapter that reads the manifest, the attribution file, the ground-truth label, or the directory
  layout, or that keys behaviour off `case_id`.
- Returning an abstention for an infrastructure failure, or an error for a case the system merely
  found hard.
- A partial run presented as a suite score.
- Precision quoted without coverage.
- A leaderboard row with no committed raw run file behind it.

### 10.5 Reporting a data problem

If you are the rights holder for an image in this suite and it should not be here, open an issue.
The case will be **removed and the suite version bumped**, rather than silently edited — a silent
edit would orphan every run ever done against the suite without telling anyone (§8.2).

The same rule covers a label you believe is wrong, a matcher behaviour you believe is a defect, and
a synonym you believe is obviously correct. Open an issue describing the defect and its measured
effect on the manifest. **Do not modify the contract.** A fix ships as a new suite version with its
own frozen contract and its own results table. It never retro-edits `openfield_v1`.

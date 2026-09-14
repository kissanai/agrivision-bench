# Metrics

Precise definitions for every number this benchmark publishes, plus the arithmetic that shows why
an abstaining system posts a higher precision at a lower coverage — and what that does and does not
prove.

All examples on this page use invented round numbers to make the arithmetic checkable by hand.
They are **not** results. Published baselines live in `tracks/diagnosis/results/`, each backed by
a raw per-case run file.

---

## 1. Populations

Three counts define every metric. Get these wrong and nothing downstream means anything.

| Population | Definition |
| --- | --- |
| `attempted` | Cases the system was given. For a complete run this is the full suite (554 for `openfield_v1`). |
| `valid_cases` | `attempted` minus rows where the system returned `{"error": ...}` — a timeout, a rate-limit, a 5xx, a malformed response. |
| `answered` | Valid cases where the system returned a **usable diagnosis**: it named a condition and gave a real category rather than declining. |

An **abstention** is a valid case that is not answered: the system returned a well-formed response
whose `primary_category` is missing, empty, or `"unknown"`. Every other category value — including
`"healthy"` — counts as answered. Abstentions stay in `valid_cases` and are excluded from
`answered`.

```
valid_cases = answered + abstained
attempted   = valid_cases + errors
```

### Errors are not wrong answers

A provider timeout is a **missing measurement**, not a bad diagnosis. Scoring it as wrong would
conflate infrastructure reliability with diagnostic quality and would let a flaky network make a
good system look dangerous.

Errors are therefore removed from the denominator and reported separately, always, as an absolute
count next to `valid n`. This creates an obvious incentive to launder failures into errors, so:

- A run with a high error count is not a clean result. Publish the count; readers discount it.
- Returning `{"error": ...}` for a case the system simply found hard is falsification, not
  abstention. Abstain with `primary_category: "unknown"` — it costs coverage, which is the honest
  price.
- A row that cannot be classified as either a clean response or a clean error is an error.

---

## 2. Per-case judgements

Each answered case yields three booleans, computed by the frozen matcher in
`src/agribench/contract/matching.py`.

### `subject_ok` — did it identify the crop?

True when the predicted `subject` matches the ground-truth `entity` under the matcher's deliberately
generous subject rule: normalised token overlap, substring containment in either direction, or a
single shared significant token. Loose on purpose — "tomato" and "tomato plant" are the same answer,
and subject identification is not what this benchmark is fundamentally about.

> This rule has a known quirk: an **empty** subject string scores as correct. See
> [LIMITATIONS.md §2](LIMITATIONS.md).

### `exact_issue_ok` — did it name the right condition?

True when the predicted `primary_issue` matches the ground-truth `issue` under the matcher: token
subset in either direction, Jaccard token overlap ≥ 0.5, or membership in the same curated synonym
group (different sources name the same pest differently — the matcher reconciles a fixed list of
those). Strict about the condition; blind to spelling, word order, and capitalisation.

### `correct_category` — did it get the *kind* of problem right?

True when **either**:

- `exact_issue_ok` is true, **or**
- the predicted `primary_category` equals the issue family derived from the ground-truth condition
  label, and that family is resolvable (not `unknown`).

This is the one that feeds the headline. It is deliberately more forgiving than `exact_issue_ok`,
because the actionable decision in the field is usually the family, not the species: fungicide
versus insecticide versus nutrient correction. A system that says "some fungal blight" on a case of
Alternaria blight has not given a great answer, but it has not sent the farmer to the wrong shelf.
A system that says "aphids" has.

**The headline metric is lenient about specificity and strict about being wrong.** That asymmetry is
intentional and is the whole design.

---

## 3. The metrics

```
precision            = correct_category_among_answered / answered
coverage             = answered / valid_cases
wrong_pct            = (answered - correct_category_among_answered) / valid_cases
subject_accuracy     = subject_ok / valid_cases
issue_class_accuracy = exact_issue_ok / valid_cases
```

| Metric | Denominator | Reads as |
| --- | --- | --- |
| `precision` | **`answered`** | "When it speaks, how often is it right?" |
| `coverage` | `valid_cases` | "How often does it speak at all?" |
| `wrong_pct` | `valid_cases` | "How often does a farmer get a confidently wrong answer?" |
| `subject_accuracy` | `valid_cases` | "How often does it identify the crop?" |
| `issue_class_accuracy` | `valid_cases` | "How often does it name the exact condition?" |

### The two-denominator rule

> **`precision` is the only metric measured over `answered`. Everything else is measured over
> `valid_cases`.**

Both denominators must be published for every entry. Mixing them is how a benchmark starts lying:

- Dividing correct answers by `valid_cases` and calling the result "precision" **punishes
  abstention** — a system that refuses a case it would have got wrong is scored as though it got it
  wrong, so the rational move becomes always guessing.
- Dividing wrong answers by `answered` **hides refusal** — a system that answers 5% of cases and
  gets 95% of those right looks flawless, and the 95% of the field it walked away from disappears
  from the number entirely.

`wrong_pct` exists specifically to keep the second failure visible. It is the only metric that puts
wrong answers over the full population, and it is the number an agronomist should look at first.

### Undefined values

`precision` is undefined when `answered == 0`. Report it as `n/a`. Never as `0%` (which reads as
"always wrong") and never as `100%` (which reads as "never wrong"). Likewise, any metric over
`valid_cases == 0` is `n/a` and the run is not a result.

### Reporting conventions

- Compute in full floating-point precision; round only for display, to **one decimal place**.
- Never round an intermediate value, and never compute a percentage from an already-rounded one.
- Always publish `valid n` and the error count next to the percentages.
- Never publish `precision` without `coverage` immediately adjacent — not in a table, not in a chart
  title, not in a commit message, not in a headline.
- Two entries are comparable only if they ran the same suite, the same case set, the same prompt,
  and the same matcher, verified by `python -m agribench.contract.verify`.

---

## 4. Two identities worth memorising

```
wrong_pct    = coverage × (1 − precision)
correct_rate = coverage × precision            # correct diagnoses per valid case
coverage     = correct_rate + wrong_pct
```

`correct_rate` is not a published headline, but it is what you compute in your head when you want
to know how much useful output a system produced per photo. Precision alone cannot tell you that,
and neither can coverage alone. That is the point of publishing both.

---

## 5. Worked arithmetic: abstention buys precision, and it costs something

Three illustrative systems, each run on the same **500 valid cases** (zero errors, to keep the
arithmetic clean).

### System A — answers everything

Never abstains. Guesses when unsure.

```
valid_cases = 500
answered    = 500        (abstained 0)
correct      = 310

precision = 310 / 500 = 0.620 =  62.0%
coverage  = 500 / 500 = 1.000 = 100.0%
wrong_pct = (500 − 310) / 500 = 190 / 500 = 0.380 = 38.0%
correct_rate = 310 / 500 = 62.0%
```

### System B — abstains on the 200 cases it is least sure about

Same underlying perception. The only difference is a confidence gate in front of the output.

```
valid_cases = 500
answered    = 300        (abstained 200)
correct      = 255

precision = 255 / 300 = 0.850 = 85.0%
coverage  = 300 / 500 = 0.600 = 60.0%
wrong_pct = (300 − 255) / 500 = 45 / 500 = 0.090 =  9.0%
correct_rate = 255 / 500 = 51.0%
```

### System C — abstains on almost everything

```
valid_cases = 500
answered    =  60        (abstained 440)
correct      =  57

precision =  57 /  60 = 0.950 = 95.0%
coverage  =  60 / 500 = 0.120 = 12.0%
wrong_pct = ( 60 −  57) / 500 =  3 / 500 = 0.006 =  0.6%
correct_rate = 57 / 500 = 11.4%
```

### Side by side

| System | precision | coverage | wrong_pct | correct_rate | wrong answers delivered |
| --- | ---: | ---: | ---: | ---: | ---: |
| A — answers everything | 62.0% | 100.0% | 38.0% | 62.0% | **190** |
| B — gated | **85.0%** | 60.0% | 9.0% | 51.0% | **45** |
| C — over-cautious | **95.0%** | 12.0% | 0.6% | 11.4% | **3** |

Check the identity on B: `coverage × (1 − precision) = 0.600 × 0.150 = 0.090` = `wrong_pct`. ✔

### What this actually shows

**Precision rises monotonically with abstention, and it is not free.**

- B posts a precision 23 points above A while producing **4.2× fewer wrong diagnoses** (45 versus
  190). Over 500 farmer queries that is 145 avoided bad recommendations. This is a real improvement
  and it is exactly what a confidence gate is for.
- But B also produced 55 fewer *correct* diagnoses than A (255 versus 310). Abstention traded
  correct answers for avoided wrong ones, at a rate of roughly 1 correct given up per 2.6 wrong
  avoided. Whether that trade is good depends on the relative cost of a wrong spray versus a missed
  diagnosis — a question this benchmark does not answer for you.
- C shows the degenerate corner. Its 95.0% precision is the best on the page and it is nearly
  useless: it answered 60 of 500 questions. **This is why coverage is mandatory next to precision.**
  A precision-only leaderboard is trivially gamed by abstaining harder, and the winner would be a
  system that answers once.

There is no single ranking of A, B, and C. There is a frontier. The benchmark's job is to publish
the coordinates honestly, not to collapse them into one number.

### The same numbers under a broken denominator

If you compute "precision" over `valid_cases` instead of `answered`:

```
A: 310 / 500 = 62.0%
B: 255 / 500 = 51.0%      <- B now ranks BELOW A
```

B, which delivers a quarter as many wrong answers, would rank last. Every abstention would be
charged as an error. This is the mistake the two-denominator rule exists to prevent, and it is a
common one.

---

## 6. Worked example with errors

A system attempts all 554 cases of `openfield_v1`. Twenty calls fail with provider 5xx.

```
attempted   = 554
errors      =  20
valid_cases = 534
answered    = 402        (abstained 132)
correct      = 349

precision = 349 / 402 = 0.868 = 86.8%
coverage  = 402 / 534 = 0.753 = 75.3%
wrong_pct = (402 − 349) / 534 = 53 / 534 = 0.099 = 9.9%
```

Note that `coverage` uses 534, not 554. Using `attempted` would give 72.6% and would charge the
system for its provider's downtime. The published row still carries `errors = 20` so a reader can
apply their own discount:

| system | type | valid n | errors | precision | coverage | wrong of all |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| example | `system` | 544 | 20 | 86.8% | 73.9% | 9.7% |

A run with enough errors that `valid_cases` drops meaningfully below the suite size is a
provisional result at best. State the count; do not bury it.

---

## 7. Caveats

Every number defined here is only as good as the matcher that produces the booleans in §2, and the
matcher is known-generous in specific, measured ways — a prediction of bare `"blight"` is scored
exactly correct against 21 of the 204 condition labels in the suite, while 62 cases (11.0%) can
earn no family-level credit at all. Read **[LIMITATIONS.md](LIMITATIONS.md)** before quoting any
figure from this repository.

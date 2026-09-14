# AGENTS.md — operating rules for AI coding agents

This file is for automated contributors. Humans should read `README.md` instead; it explains what
the benchmark is for. This file explains what you are allowed to do to it. The method itself lives
in `docs/METHODOLOGY.md` — read it before changing anything that touches scoring, and do not
restate it here.

Package `agribench` **0.1.0**, evaluation contract **v1.0**, suite `openfield_v1` (554 cases).

This repository is **public**. Everything you write here is published. Three properties matter more
than any feature you might add:

1. **Comparability.** Published numbers must stay re-derivable. The evaluation contract is frozen.
2. **Cleanliness.** No proprietary vocabulary, no secrets, no absolute paths, no binaries in the
   wrong place.
3. **Redistribution safety.** This repository ships a manifest and ground truth. It never ships
   third-party image bytes, and it never claims a licence it was not granted.

If a requested change would break any of the three, stop and say so rather than doing it partially.

---

## 1. `src/agribench/contract/` is a FROZEN ZONE

**Never edit any file under `src/agribench/contract/`.** Not to fix a typo. Not to reformat. Not to
satisfy a linter. Not to "improve" the matcher. Not to add a synonym you believe is obviously
correct.

The directory holds the task prompt and the label matcher. Every published score was produced by
those exact bytes. Changing one character silently changes what "precision" means and invalidates
comparability with every previously published result — including results in third-party papers.
The damage is not detectable by reading a diff months later, which is precisely why the rule is
absolute rather than case-by-case.

Known-imperfect behaviour in the matcher is **documented, not fixed** — see `docs/LIMITATIONS.md`.
It stays imperfect on purpose. A benchmark whose scorer drifts is worth less than a benchmark whose
scorer is known-generous in a specific, published way.

If you genuinely believe the contract is wrong:

- Do not modify it.
- Open an issue describing the defect and its measured effect on the manifest.
- A fix ships as a **new suite version** (`openfield-v2`) with its own frozen contract and its own
  results table. It never retro-edits `openfield_v1`.

Ruff is configured to ignore `contract/` entirely (`per-file-ignores = ["ALL"]`). If a lint or
formatter run wants to touch a file in there, the run is wrong, not the file.

### Verify before and after — every time

```bash
python -m agribench.contract.verify
```

Run it **before** you start work (to prove the tree was clean when you got it) and **after** every
change (to prove you did not disturb it). It re-hashes the frozen files and compares them against
their pinned sha256 values. Non-zero exit means the tree is not comparable; fix it before anything
else, and never regenerate the lock file to make the error go away.

Do not add the verify step to a commit hook and consider it handled — run it and read the output.

---

## 2. IP rules for public text

Prohibited vocabulary is **not enumerated in this file**, because writing the forbidden terms down
in a public repository defeats the point. Instead, reject any term belonging to these categories:

| Category | Rule |
| --- | --- |
| Internal product tier / plan names | Never. Describe capability, not commercial packaging. |
| Model architecture or backbone acronyms | Never. Say "diagnosis system"; the internals are out of scope. |
| Internal class codes, label IDs, taxonomy IDs | Never. Use the public labels in `manifest.jsonl`. |
| Training-run, bundle, or checkpoint identifiers | Never. |
| Private repository or internal service names | Never. |
| Customer, partner, or pilot-deployment names | Never. |
| API keys, tokens, endpoints with embedded credentials | Never. Environment variables only. |
| Absolute filesystem paths rooted in a developer home directory | Never. Repo-relative paths only. |

**Use this vocabulary instead:**

| Say this | Not something else |
| --- | --- |
| `diagnosis system` | the unit under test, whatever its internals |
| `single_call` | one model, one call, one response |
| `system` | multi-call, gated, retrieval-augmented, or otherwise composite |
| `answered` | the system returned a usable diagnosis |
| `abstained` | the system returned `unknown` |
| `valid_cases` | cases with a well-formed response (no infrastructure error) |
| `bundled` | a case whose licence permits redistribution, staged from a local copy |
| `link-only` | a case whose image is downloaded from the origin by the operator |
| `source-only` | a case with neither a redistribution grant nor a per-image URL; the operator obtains the named upstream dataset |

The words for "answered" and "abstained" are load-bearing: they appear in metric names, in run
files, and in `docs/METRICS.md`. So are `bundled`, `link-only` and `source-only`: they are literal
values of the manifest's `distribution` field, checked by `validate-manifest`. Do not introduce a
synonym for any of the five, and never write "mirrored", "hosted", or "cached" for a link-only or
source-only case — this project does not do any of those things.

Cheap self-check before you commit:

```bash
git diff --cached | grep -nE '/home/|/Users/|api[_-]?key\s*=\s*["'"'"']|sk-[A-Za-z0-9]{16}'
git diff --cached --name-only | grep -iE '\.(jpg|jpeg|png|webp|bmp|tiff?)$'
```

Any hit from either is a blocker. If you are unsure whether a term is internal, assume it is and
ask.

---

## 3. Never commit image bytes. Anywhere.

This repository ships a manifest and ground truth, and no photographs. `.gitignore` blocks
`*.jpg`/`*.jpeg`/`*.png`/`*.webp`/`*.bmp`/`*.tif`/`*.tiff` repo-wide **and** ignores
`**/images/` outright — deliberately, with no negation, because an earlier version
whitelisted image extensions under the suite tree and that would have let a link-only image be committed by a plain
`git add -A`. `git add -f` defeats the ignore, so do not use `-f` on an image, ever.
`tests/test_link_only.py::test_no_link_only_image_is_committed` fails the build if any image file
appears anywhere in the repository.

Specifically:

- No screenshots in `docs/`. Describe the thing in prose or a Markdown table.
- No debug renders, no crops, no montages, no "before/after" comparison images.
- Charts are generated on demand via the `chart` extra; commit the script, not the `.png`.
- No test fixture images. The fetch tests serve bytes from a loopback HTTP server they construct at
  runtime; do the same.
- Never re-encode, resize, or re-compress a benchmark image. `image_sha256` in the manifest pins
  every file byte-for-byte; a re-encode silently invalidates every run ever done against that case.

If a task seems to require committing an image, it is the wrong approach. Say so.

---

## 4. Image distribution: three things you must never do

A case declares in `distribution` how its bytes reach the operator. `bundled` means the licence
permits redistribution, so the file can be staged from a local copy. `link-only` means it does not,
so the manifest carries only an `http`/`https` `image_url` and the operator downloads it from the
origin themselves, under that origin's terms. `source-only` means neither is available — there is no
redistribution grant *and* no address that returns the image, because its publisher ships an archive
— so the manifest names the upstream dataset and the operator obtains that. `src/agribench/manifest.py`
enforces the schema rules and `docs/METHODOLOGY.md` explains the reasoning; do not restate either here.

Three rules are absolute.

**Never commit link-only image bytes.** They are the one category of file nobody granted this
project the right to redistribute. Committing one is a licence breach that stays in the git history
after you delete it. This is why `**/images/` is ignored wholesale rather than filtered.

**Never re-pin an existing `image_sha256` to silence a mismatch.** A digest mismatch means the
origin is now serving different bytes than the ground-truth label was written against. Re-pinning
erases the only check that detects that, and converts a caught problem into a silently mis-scored
case. `pin_manifest` refuses to overwrite an existing digest by design — do not work around it, do
not hand-edit the digest, do not delete the field and re-run `--pin`. Investigate instead:
re-examine the new image, and if the label no longer describes it, remove the case and bump the
suite version.

**Never mark a non-redistributable image as bundled.** `bundled` asserts a redistribution right.
Only CC0, CC-BY, MIT, Apache-2.0, and public-domain qualify, and `validate-manifest` rejects
anything else — that check exists precisely because a licence breach is otherwise one typo away.
If you cannot redistribute the image, the case is `link-only` with an `image_url`, or `source-only`
naming the dataset it came from, or it is not a case.

Two more, less absolute but still load-bearing:

- Bundled and source-only cases must carry `image_sha256`; only a link-only case may be authored
  unpinned, and only until its first successful fetch. Do not invent a digest to make validation
  pass.
- `agribench fetch` talks to the public internet. Never call it from a test. Never point it at a
  URL you have not read the terms of.

Authoring a new link-only case: copy the shape from `tracks/diagnosis/templates/link-only.example.jsonl`, omit
`image_sha256`, run `agribench fetch --pin`, then commit **the manifest only**.

---

## 5. Commands

Run all of these from the repository root.

```bash
# install with dev tooling
pip install -e ".[dev]"

# the frozen-contract check — before and after every change
python -m agribench.contract.verify

# full test suite (83 tests)
python -m pytest -q

# the tests that guard comparability specifically
python -m pytest tests/test_contract_lock.py -q

# the tests that guard redistribution safety
python -m pytest tests/test_link_only.py tests/test_stage.py -q

# one test file, or one test, verbosely, when debugging
python -m pytest tests/test_openfield_suite.py -vv
python -m pytest tests/test_link_only.py -k pin -vv

# lint (contract/ is excluded by configuration — do not override that)
python -m ruff check .

# offline end-to-end smoke, no API key, no images staged, ~1 second
agribench run --adapter echo --limit 20

# the two paths people forget to exercise: abstention and errors
agribench run --adapter echo --limit 50 --adapter-option answer_rate=0.6
agribench run --adapter echo --limit 50 --adapter-option fail_rate=0.1
```

Dataset commands. The repository ships a manifest, not photographs, so a real run needs the bytes
put on disk first. `stage` handles bundled cases from a local copy; `fetch` handles link-only cases
from their origin URLs. Both verify every file against its recorded SHA-256.

```bash
# bundled cases: copy from a local directory, matched by content digest, never by filename
agribench stage --from <dir>
agribench stage --audit                 # what is present; copies nothing
agribench stage --audit --deep          # re-hash every staged file

# link-only cases: download from the origin, verified against the recorded digest
agribench fetch                         # NETWORK. Never call this from a test.
agribench fetch --recheck               # re-verify what is already on disk
agribench fetch --pin                   # authoring only: record digests for unpinned cases
agribench fetch --pin --dry-run         # show what --pin would write; writes nothing

# structural checks, image presence, and byte verification
agribench validate-manifest --manifest tracks/diagnosis/suites/openfield_v1/manifest.jsonl
agribench validate-manifest --manifest tracks/diagnosis/suites/openfield_v1/manifest.jsonl \
    --images tracks/diagnosis/suites/openfield_v1 --check-hashes
```

The other four commands — `run`, `score`, `report`, `compare` — are documented in `README.md` and
in `agribench <command> --help`, which is authoritative for flags.

**Definition of done for any change:** `python -m agribench.contract.verify` exits 0,
`python -m pytest -q` passes, `python -m ruff check .` is clean, `agribench validate-manifest
--manifest tracks/diagnosis/suites/openfield_v1/manifest.jsonl` exits 0, and the echo smoke run still produces a
metric block. Report the actual output of these commands — do not assert they passed.

Core runtime dependencies are deliberately **empty**. The scoring path is stdlib-only so a reader
can reproduce a published number from a bare CPython install. Do not add a runtime dependency to
`[project.dependencies]`; if you need a library, it belongs in an optional extra and must not be
importable from the scoring path.

---

## 6. Recipe: "add a system to the benchmark"

Follow this exactly. Do not improvise a shortcut.

1. **Verify first, then get the images.**
   ```bash
   python -m agribench.contract.verify
   agribench stage --audit
   ```
   If verification fails on a clean checkout, stop and report it. Do not proceed. If `stage
   --audit` reports missing images, stage or fetch them — a real adapter cannot run without the
   bytes, and `agribench run` will refuse to start rather than write a run file full of errors that
   looks like a result.

2. **Write one adapter file.** It may live anywhere importable — for a third-party system, outside
   this repository entirely (`my_bench.py`); for a built-in, at `src/agribench/adapters/<slug>.py`
   *and* registered in `BUILTIN_ADAPTERS`, since bare names resolve only through that table.
   Subclass `agribench.adapters.base.Adapter`, set `name` and `system_type` (`"single_call"` or
   `"system"`),
   and implement `predict(self, case: Case) -> Prediction`. **No other module in the repo should
   need to change.** If you find yourself editing the runner, the CLI, or the scoring path to
   accommodate one system, the adapter interface is being abused — stop and ask. `predict` is
   called from a thread pool, so it must be thread-safe.

3. **Import the prompt; never retype it.**
   ```python
   from agribench.contract.prompt import PROMPT
   ```
   Do not append instructions, do not prefix a persona, do not translate it, do not "clean up" its
   punctuation. A modified prompt is a different benchmark and the result is unpublishable.

4. **Credentials come from the environment only.**
   ```python
   key = os.environ["AGRIBENCH_PROVIDER_API_KEY"]
   ```
   Add the *variable name* to `.env.example` with an empty value. Never a real key, never a
   default, never a fallback literal, never a key in a test fixture, never a key in a comment.

5. **Keep the three outcomes distinct.** This is the most common and most damaging adapter bug.

   | outcome | signal | counted as |
   | --- | --- | --- |
   | answered | `Prediction(answered=True, ...)` or `Prediction.from_payload(...)` | in `valid_cases`, in `answered` |
   | abstained | `Prediction.abstain(...)` | in `valid_cases`, not answered |
   | failed | `raise AdapterError` / `TransientAdapterError` / `AdapterConfigError` | excluded from `valid_cases` |

   Timeouts, 429s, 5xx, refusals, unparseable payloads: **raise**. Never return an abstention for an
   infrastructure failure — it silently inflates precision and is treated as a falsified result.
   Equally, never raise to mean "the model was unsure"; that is an abstention.

6. **Do not abstain on the benchmark's behalf.** If the model returned a usable diagnosis, report
   `answered=True` even when you suspect it is wrong. Filtering a model's weak answers inside the
   adapter turns a system comparison into an adapter comparison. The one legitimate exception is a
   system that genuinely ships a confidence gate in production — then the gate is under test and
   must run exactly as it ships, and `system_type` must be `"system"`.

   The adapter receives a `Case` carrying only `case_id`, `image_path`, and `image_sha256` — no
   ground truth, by construction. Reading the manifest, the attribution file, the ground-truth
   label, or the directory name to influence a prediction is cheating, and so is keying behaviour
   off `case_id`.

7. **Smoke, then full run.**
   ```bash
   agribench run --adapter my_bench:MyAdapter --manifest tracks/diagnosis/suites/field_v1/manifest.jsonl --limit 8
   agribench run --adapter my_bench:MyAdapter --manifest tracks/diagnosis/suites/field_v1/manifest.jsonl \
       --out runs/<slug>.json
   ```
   Read the smoke output by eye before spending a full run. A full run must cover all 554 cases; a
   partial run is not a leaderboard entry. Also confirm `describe()` returns useful metadata — model
   id, endpoint host, decoding parameters — and **no secret**. That dict is written to disk and
   published; report that a key was present, never what it was.

8. **Publish the raw file, not just the summary.** Copy both the aggregate `runs/<slug>.json` and
   its per-case sidecar `runs/<slug>.records.jsonl` into the track's `results/`, and record their sha256
   alongside the leaderboard row. `runs/` and `reports/` are git-ignored scratch; a track's `results/` is
   committed. A row nobody can re-score from a raw file does not go in the table.

9. **Report precision and coverage together, always.** Never quote precision alone, in a commit
   message, a PR description, a chart title, or a table. A high precision at 4% coverage is not a
   result; presenting it as one is the specific failure mode this benchmark exists to prevent.

10. **Verify last.**
    ```bash
    python -m agribench.contract.verify && python -m pytest -q && python -m ruff check .
    ```

---

## 7. Things that look helpful and are not

- Adding a synonym to the matcher because a system's correct-looking answer was scored wrong.
  That is a contract edit. See §1.
- Normalising, deduplicating, or re-labelling entries in `manifest.jsonl`. The case set is frozen
  alongside the contract; known label noise is documented in `docs/LIMITATIONS.md`.
- Restoring one of the 10 cases in `tracks/diagnosis/suites/openfield_v1/EXCLUDED.jsonl`. They were removed because
  the frozen matcher cannot score them fairly — 8 whose declared family conflicts with the family
  the matcher derives, 2 whose ground truth cannot match itself because every token is a stop word.
  Adding them back changes every published number and fixes nothing.
- Re-pinning a link-only digest so a mismatched fetch passes. See §4. This is the highest-damage
  edit in the repository that looks like a one-line fix.
- Marking a case `bundled` because `validate-manifest` complained about its licence. The validator
  is right. See §4.
- Committing an image "just for the test fixture". See §3.
- "Tidying" `docs/LIMITATIONS.md` by removing limitations that were fixed elsewhere. Every entry
  there is load-bearing and carries counts measured against the manifest; if a count changes, update
  the count and show your work.
- Copying method content out of `docs/METHODOLOGY.md` into `README.md` or this file. One
  description of the method, in one place; the others link to it.
- Adding a leaderboard row from an unreproduced run, an estimate, an example, or a number you saw
  elsewhere. A row is legitimate only when a committed raw run file in the track's `results/`
  backs it and `agribench score` reproduces it. No exceptions, including for our own systems.
- Quoting a pre-1.0 number next to a v1.0 one. The old builder used different denominators for
  different system types; `src/agribench/legacy.py` reproduces it for audit only. The two never
  share a table.
- Adding a dependency to make the scoring code shorter. See §5.
- Reformatting the repository wholesale. Large mechanical diffs hide contract edits, which is
  exactly the review failure this file exists to prevent.

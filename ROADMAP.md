# Roadmap

AgriVision Bench is built as a programme of **tracks**. A track is a distinct agricultural vision
task with its own suites, its own frozen scoring contract, and its own leaderboard. Tracks are
released independently and versioned independently; nothing about one track's contract constrains
another's.

This file lists what exists and what is intended. **Nothing here is a commitment to a date.** A
track appears in the repository only when it ships with a manifest, a frozen contract, and at least
one reproducible baseline — not before.

## Released

### Diagnosis — `diagnosis-v1.0`

Name the crop and what is wrong with it, from a single photo, or decline to answer. Scored on
precision among answered cases, published next to coverage. Three suites, 1,468 cases, six frontier
VLM baselines. See [tracks/diagnosis/](tracks/diagnosis/).

## Intended

These are the tasks we expect to add. Each is listed with the question it would answer and the
reason the Diagnosis contract cannot answer it.

### Grading

Grade harvested produce for quality and market class — size, colour, blemish, maturity, defect
grade. Diagnosis asks "what is wrong with this plant"; grading asks "what is this worth", against
a graded scale rather than a condition label, and is scored against a rubric with ordered
categories where being one grade out is not the same error as being three grades out.

### Counting

Count objects in frame: fruit per tree, tillers per hill, pests per trap. A counting task needs
error measured as a distribution over counts rather than a right/wrong judgement, so it needs a
different metric family, not a different label set.

## How a new track gets added

The repository layout is designed so that adding a track is purely additive — a new directory under
`tracks/`, with no reorganisation of anything already published and no change to an existing
track's contract:

```
tracks/
  diagnosis/      README.md, suites/, results/, templates/
  <new-track>/    same shape, its own contract version
```

Shared machinery — the adapter interface, the run-file format, the CLI, the manifest and staging
tooling — lives in `src/agribench/` and is reused. What a track defines for itself is its task
prompt, its response schema, its matcher, and its metrics.

## Suggesting a track

Open an issue describing the task, why an existing track cannot express it, and — most importantly —
where the ground truth would come from. Label availability, not model capability, is the binding
constraint on every track here.

# Evidence trust model for distillations, and the `puba summarize` sanity-check artifact

## Status

Accepted

## Context

`puba distill`'s output is currently unstructured free text — see
`docs/design-log.md`'s explicit non-goal "puba does not enforce or
parse the output format." The new evidence feature (`docs/OBJECTIVE.md`
Milestone 4) adds an opt-in structured companion to that free text: a
list of exact quotations the model claims support its answer.

A comparable prior-art project, `wake`
(https://github.com/rbross-hpc/wake), uses a heavier
`provisional → proposed → verified` lifecycle where a human must
explicitly sign off before a citation-relationship finding is treated
as settled, and (as of its current release) does not
programmatically verify that a quoted passage actually appears in the
source text at all — that check is left to a human reviewer.

This project deliberately does **not** adopt `wake`'s
human-verification lifecycle. These decisions were made explicitly
during planning for this objective and should not be relitigated by a
future planner or architect invocation:

1. Evidence remains model-generated only — there is no "verified"
   human sign-off state for puba's evidence.
2. Unlike `wake`'s known limitation, puba **does** programmatically
   verify each quoted passage against the canonical source before
   persisting it (see `docs/OBJECTIVE.md` Milestone 4 for the exact
   canonical source and offset coordinate system per scope) — this
   is a strictly stronger guarantee than `wake` currently provides,
   just without the human-approval workflow layered on top.
3. `puba summarize` displays only the user-authored query prompt for
   each distillation, never puba's internal system/format
   instructions — those are represented by a version identifier in
   provenance instead.

## Decision

- A structured evidence response that fails to parse as valid JSON
  matching the required shape, even after the existing LLM-call retry
  policy, is treated as an ordinary LLM-call failure: the run returns
  an error, and neither the output file nor `.state.json` is touched.
  This is deliberately stricter than the "quote doesn't match"
  handling below — there is no well-formed answer to fall back to in
  this case.
- Once a structurally valid response is obtained, an evidence item
  that fails local verification is **not** a fatal error and does
  **not** cause the produced answer to be discarded. It is persisted
  with an explicit unverified status and reason:
  - `no_match` — the quoted string does not appear in the canonical
    source at all (or, for `section` scope, only appears outside that
    section's own span).
  - `ambiguous_match` — the quoted string appears more than once
    within the allowed range, so no single offset can be attributed
    with confidence.
  A structurally valid response whose `evidence` array is simply
  empty is treated identically to "every item was unverified" from a
  caller's perspective: both produce `evidence_status: partial`. A
  record where every item verified gets `evidence_status: verified`.
  A warning is surfaced in both non-`verified` cases (stderr for
  interactive use, a structured field for `--json` output).
- `puba summarize <pdf>` is a new, purely deterministic (no LLM call,
  no network call — see ADR 0002, it must not trigger any stage), 
  read-only command that renders `<pdf-stem>.summary.md` next to the
  source PDF (not inside `<pdf-stem>.puba/`), only when explicitly
  invoked, using an atomic write so a rerun replaces it cleanly. It
  aggregates what has already been computed (bib, stage status,
  figures, every distillation's user-authored prompt/model/output/
  evidence) into one OKF-style document with YAML frontmatter, so a
  human can sanity-check everything puba has done for a paper in one
  place, including a prominent section surfacing anything missing,
  stale, or unverified. It exits `0` whenever it successfully
  produces a report, even one consisting mostly of "nothing has been
  run yet" — see `docs/OBJECTIVE.md` Milestone 5 for the precise exit
  conditions.

## Consequences

- Callers relying on `puba distill --json` output must handle evidence
  items that are present but marked unverified (with a
  `no_match`/`ambiguous_match` reason), and must handle
  `evidence_status: partial` arising from either an unverified item or
  a structurally valid but empty evidence list — the two are not
  distinguished at the `evidence_status` level.
- Each distillation record must persist the resolved, user-authored
  query prompt text verbatim (not just its hash), specifically so
  `puba summarize` can display it without needing the original
  `prompts/*.yaml` file to still exist or be unchanged. A record
  written before this existed is rendered with an explicit
  "not recorded" placeholder, itself flagged in "Missing / Needs
  Attention," rather than silently omitted.
- `puba summarize`'s output is a snapshot at generation time (modulo
  its own generation timestamp, it is byte-identical for unchanged
  inputs); it is not auto-regenerated by other commands and can go
  stale relative to the underlying artifacts. This is intentional
  (see ADR 0002) and should be stated in the command's own `--help`
  text and in `docs/distillations.md`/README, not treated as a bug to
  silently fix by adding an auto-refresh side effect elsewhere.
- If a future need arises to dial down `puba summarize`'s verbosity
  (raised during planning but explicitly deferred), that is a
  legitimate future task, not something this ADR forecloses.

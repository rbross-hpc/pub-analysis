# Separate quote presence from model-assessed claim support

## Status

Accepted

## Context

ADR 0004 established schema-v1 evidence handling, including local exact-quote
matching and an aggregate `verified`/`unverified` vocabulary. That vocabulary
conflates two different questions: whether proposed text occurs uniquely in the
permitted source, and whether that text supports a claim in the generated
answer. Exact matching can answer only the first question.

This ADR refines ADR 0004 rather than rewriting it. ADR 0004 remains the
historical record of the schema-v1 contract and its decisions about canonical
sources, atomic failure, retaining unmatched quotations, and the absence of a
human sign-off workflow.

## Decision

- Schema v2 names deterministic local matching **quote presence**. A quote is
  `present`, `not_found`, or `ambiguous`; aggregate presence is `complete` only
  for a non-empty collection in which every quote is uniquely present, and is
  `partial` otherwise.
- Whether evidence supports an answer is a separate **model-assessed claim
  support** result produced by a distinct LLM request. Its labels are
  `supported`, `partially_supported`, `unsupported`, `contradicted`,
  `insufficient_evidence`, and locally assigned `not_assessed`. These labels
  are not formal proof, deterministic verification, or human review.
- Schema-v2 records explicitly select `evidence_mode: none`, `quotes`, or
  `claims`. No-evidence records omit quote and claim structures; quote records
  contain flat proposed quotations and local presence results; claim records
  contain atomic claims, their proposed quotations and presence results, and a
  support assessment for each claim.
- Artifact readers form a schema boundary. An absent `schema_version` means
  version 1; only integer versions 1 and 2 are accepted. Schema-v1 evidence is
  validated and may be normalized in memory from `verified`/`unverified` to
  quote-presence terminology. Schema-v1 files are never rewritten merely
  because they were read. Unknown future versions are invalid rather than
  interpreted heuristically.
- Quote-presence aggregates are derived from individual quote results, and
  claim-support aggregates are derived from individual claim assessments.
  Persisted aggregate fields must agree with their items.
- A verifier call succeeds when it returns any structurally valid support
  label. `partially_supported`, `unsupported`, `contradicted`, and
  `insufficient_evidence` are acceptable outcomes that are persisted and
  surfaced for attention; they do not fail the distillation. Transport failure,
  malformed output, an invalid label or evidence reference, or exhausted
  retries do fail the run atomically and leave the prior artifact and state
  untouched.

## Consequences

- A uniquely present quote no longer implies that the quote supports any
  generated claim; user-facing surfaces must report presence and support
  independently.
- Claim support must always be described as model-assessed. Human review, if
  introduced later, requires a third independent state.
- Existing schema-v1 artifacts remain readable and unchanged on disk, while
  consumers can use normalized quote-presence fields in memory.
- Malformed schema values, unsupported future versions, mode-inconsistent v2
  records, and inconsistent aggregates make a distillation artifact invalid.

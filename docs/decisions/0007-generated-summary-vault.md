# Generate a navigable summary vault

## Status

Accepted

## Context

ADR 0004 established `puba summarize` as an explicitly invoked,
deterministic, offline, point-in-time sanity check and selected one generated
`<pdf-stem>.summary.md` as its output. As the report grew to include
bibliographic detail, figures, every distillation, claims, and evidence, one
file became difficult to navigate and could not package the source PDF and
figure images with the review surface.

This ADR supersedes only ADR 0004's decision to aggregate the summary into one
Markdown file. ADR 0004's evidence trust model, prompt-display rule,
explicit-invocation requirement, no-LLM/no-network constraint, status and exit
semantics, and point-in-time behavior remain unchanged.

## Decision

- `puba summarize <pdf>` generates `<pdf-stem>.vault/` next to the source PDF.
  It is still run only when explicitly invoked and makes no LLM or network
  calls.
- The generated layout is `README.md`, `bibliography.md`, `figures.md`,
  `distillations/<safe-query>.md`, `assets/paper.pdf`, and
  `assets/figures/`. Query-note filenames are deterministic, filesystem-safe,
  and disambiguated when they collide with another query or a reserved vault
  name.
- `README.md` includes every distillation's prompt and answer and relative
  Markdown navigation. Detail notes link relatively to the README and assets,
  making the vault portable as a directory.
- Detail notes identify the processed-text scope. The aggregate distill status
  is `mixed` when configured queries have different four-state currency values;
  the attention section names each non-current query in that case.
- `.obsidian/` and `notes/` are user-owned paths and are preserved when the
  vault is refreshed. All other content under `<pdf-stem>.vault/` is generated
  and replaced.
- The complete replacement must succeed before legacy cleanup. After success,
  an existing `<pdf-stem>.summary.md` is deleted only when its frontmatter
  identifies it as a generated puba summary. An unrecognized file at that path
  is preserved and a warning is emitted.

## Consequences

- Human review can move between overview, bibliography, figures, individual
  distillations, evidence, and copied source assets without one oversized
  document or absolute links.
- Users may customize Obsidian settings and keep notes inside the two reserved
  user-owned paths without losing them on refresh. Other manual edits inside
  the vault are not durable because generated content is replaced.
- Existing generated single-file summaries are cleaned up after the first
  successful vault generation, while unrelated user files with the same legacy
  name are not removed.
- The vault remains a point-in-time inspection artifact. Other puba commands do
  not refresh it, and it may become stale relative to analysis artifacts.

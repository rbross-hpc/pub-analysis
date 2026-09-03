# Single-paper, file-based architecture

## Status

Accepted

## Context

`puba` grew out of three predecessor projects, one of which
(`paper_thing`) used a SQLite corpus database. `docs/design-log.md`
records the deliberate decision to reject that model: one PDF gets one
`<pdf-stem>.puba/` analysis directory sitting next to it, containing
per-stage sidecar files (`bib.yaml`/`bib.json`, `paper.md`,
`paper.sections.json`, `paper.figures.json`,
`analyses/*.yaml`/`.json`, `.state.json`). There is no daemon and no
corpus-wide database.

## Decision

`puba` remains single-paper and file-based. No task should introduce
a corpus-level database, a long-running server process, or
cross-paper indexing inside this codebase. A future corpus tool may
scan `*.puba/bib.json` files across many papers, but that tool is
explicitly out of scope here.

## Consequences

- Every artifact must remain independently readable/diffable/greppable
  without a database or running process.
- Multi-paper batch operations remain the caller's responsibility (a
  shell loop over `puba bib`/`puba md`, as already documented in
  `README.md`'s "Multi-paper batch" section), not a feature of `puba`
  itself.
- The JSON migration (see ADR 0003) does not change this: JSON
  sidecars are still one-file-per-paper-per-stage, not a shared index.

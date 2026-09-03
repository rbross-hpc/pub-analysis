# `show` commands are strictly read-only

## Status

Accepted

## Context

An earlier version of `puba`'s `show *` commands (`show bib`, `show
md`, `show sections`, `show section`, `show figures`, `show figure`)
would implicitly trigger the underlying stage (bib resolution,
markdown rendering, etc.) if it had not yet run. `docs/design-log.md`'s
Stage F records this being deliberately removed: `show` commands were
hardened to be strictly read-only, erroring with `error_type:
"CacheError"` via `_require_cached_bib`/`_require_cached_md` when the
required stage has not been run, rather than auto-running it.

## Decision

No `show *` command, and no new read-oriented command added to this
codebase (including the planned `puba summarize`, see ADR 0004), may
trigger a stage as a side effect of being invoked. If required input
is missing, the command must fail clearly (or, for `puba summarize`
specifically, surface the gap in its "Missing / Needs Attention"
section) rather than silently running `bib`/`md`/`figures`/`distill`
on the caller's behalf.

## Consequences

- Agents and scripts using `puba show *` (or `puba summarize`) can
  rely on these commands never mutating `<pdf-stem>.puba/` or making a
  network/LLM call.
- Any new inspection-style command must follow the same
  `_require_cached_*`-style precondition check pattern already
  established in `cli.py`, not reintroduce implicit auto-running.
- The shared Milestone 3 currency helper verifies that a present output
  artifact parses and detects state entries whose output has disappeared.
  Read-only commands may therefore distinguish `current`, `stale`,
  `never-run`, and `invalid` without triggering a stage.

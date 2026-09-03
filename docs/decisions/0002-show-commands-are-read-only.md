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
- Note: as of this ADR, `_require_cached_bib`/`_require_cached_md`
  correctly reject an entirely never-run stage, but do not yet verify
  that a *present* output artifact still parses or that a
  `.state.json` entry's referenced file still exists on disk (see
  `docs/OBJECTIVE.md` Milestone 3, which closes this gap). Until
  Milestone 3 lands, "read-only and precondition-checked" does not
  yet imply "the precondition check itself is fully accurate" — do
  not conflate the two when auditing pre-Milestone-3 work.

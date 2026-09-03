# Tool-generated artifacts move from YAML to JSON; human-authored files stay YAML

## Status

Accepted

## Context

`puba` currently mixes YAML (`bib.yaml`, `analyses/<name>.yaml`,
`config.yaml`, `puba.config.yaml`, `prompts/*.yaml`) and JSON
(`paper.sections.json`, `paper.figures.json`, `.state.json`) for its
persisted artifacts, with no consistent rule for which format a given
file uses. The upcoming evidence-backed distillation feature (see
`docs/OBJECTIVE.md` Milestone 4) needs to persist nested structured
data (quotes, offsets, verification status) per distillation record,
which is materially easier to produce, validate, and consume
losslessly in JSON than in hand-rolled YAML.

## Decision

Tool-*generated* artifacts (`bib.yaml` → `bib.json`,
`analyses/<name>.yaml` → `analyses/<name>.json`) move to JSON.
Human-*authored* files (`config.yaml`, `puba.config.yaml`,
`prompts/*.yaml`) stay YAML — they are hand-edited by users, and
YAML's multi-line block-scalar support materially helps for the
prompt text they contain.

Migration happens in two separate steps, not one:

1. A reader/writer helper layer (`docs/OBJECTIVE.md` Milestone 1) is
   introduced first. Readers prefer `.json`, falling back to legacy
   `.yaml` when `.json` is absent. This lands with **no** behavior
   change to what format any existing call site writes.
2. Only in Milestone 2 do `puba bib`/`puba distill` actually switch to
   writing `.json`: atomically write the new `.json` file, then
   delete the corresponding legacy `.yaml` file if one existed.

There is no standalone migration command. If the two-step write in
(2) is interrupted between writing `.json` and deleting `.yaml` (e.g.
process killed mid-operation), both files can transiently exist on
disk; the reader treats `.json` as authoritative in that case (see
Milestone 1) and the stale `.yaml` is cleaned up the next time that
artifact is rewritten. This is a self-healing transient state, not a
supported steady state — a caller should never *rely on* both files
being present, and puba surfaces it as an attention-worthy condition
(see Milestone 5) rather than silently tolerating it indefinitely.

## Consequences

- Existing `<pdf-stem>.puba/` directories from before this change
  continue to work without any action from the user; they migrate
  file-by-file, lazily, the next time each stage re-runs after
  Milestone 2 lands.
- Any new code that reads `bib.yaml`/`analyses/*.yaml` directly (by
  path or glob) instead of going through the Milestone 1 helper is a
  bug — it will silently miss migrated papers, and will silently miss
  the "both formats present" case entirely (it would read whichever
  format it hardcoded, not necessarily the authoritative one).
  Reviewers (auditor, future contributors) should treat a literal
  `"bib.yaml"` or `"*.yaml"` glob added in `puba/` outside the
  compatibility helper itself as a red flag.
- `paper.md` remains Markdown (it is the human-readable rendered
  document, not a data sidecar) and `.state.json`/
  `paper.sections.json`/`paper.figures.json` are unaffected — they
  were already JSON.
- Both `bib.json` and `analyses/<name>.json` carry a top-level
  `"schema_version"` field from Milestone 2 onward, so a future format
  revision (e.g. the evidence shape in Milestone 4) has an explicit
  way to distinguish old records from new ones without content
  sniffing.

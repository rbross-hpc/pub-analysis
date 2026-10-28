# Objective

`puba` is a single-paper CLI. Given one PDF, it resolves bibliographic metadata
with provenance, renders clean Markdown, extracts figures, runs named LLM
distillations, and produces a reviewable summary vault. This file is the living
roadmap; historical rationale belongs in `docs/design-log.md` and accepted
decisions belong in `docs/decisions/`.

## Delivered foundations

- Generated bibliographic and distillation artifacts use JSON with explicit
  schema versions while legacy YAML artifacts remain readable.
- Distillation schema v2 separates deterministic quote presence from
  model-assessed claim support and preserves complete structured provenance.
- A provider-neutral one-shot LLM boundary supports native managed OpenCode and
  direct OpenAI-compatible backends.
- `puba summarize` creates a deterministic, portable summary vault for
  inspecting bibliography, figures, distillations, claims, and evidence.
- Human review overlays are independent from artifact currency, quote presence,
  and model-assessed claim support.
- Bibliographic `_edit_log` history survives forced regeneration.
- CI runs the offline, non-network, non-GPU suite on Python 3.11 and 3.12.

## Current priority

Harden dependency and packaging behavior in CI. Installation, lockfile,
optional-extra, and packaged-resource checks should fail early and
reproducibly across the supported Python versions without requiring network
services, GPU hardware, or model downloads during the test run.

After that work is stable, the next optional units are:

1. Add an opt-in live semantic workflow that exercises real model behavior
   without weakening the deterministic offline CI gate.
2. Harden local MinerU installation and optional-dependency coverage, including
   actionable diagnostics for unsupported or incomplete environments.

## Known implementation gaps

- Duplicate distillation prompt names are not handled consistently across all
  configuration sources; collision behavior needs one explicit contract and
  complete tests.
- Some nominally direct-backend end-to-end tests contain OpenCode-specific
  assertions; backend-neutral assertions and backend-specific coverage need to
  be separated.
- The live bibliographic end-to-end suite still reads and asserts legacy
  `bib.yaml` output even though production writes `bib.json`; migrate it to the
  artifact compatibility helpers before treating it as a supported test lane.

## Explicitly deferred

- Attached OpenCode server reuse, including externally managed servers.
- Map-reduce distillation for inputs beyond a model context window.
- Speculative `puba md edit` or `puba figures edit` commands without a concrete
  workflow that justifies their semantics and maintenance cost.

## References

- `docs/decisions/0003-json-over-yaml-for-generated-artifacts.md`
- `docs/decisions/0004-evidence-trust-model-and-summarize-command.md`
- `docs/decisions/0005-separate-quote-presence-from-model-assessed-claim-support.md`
- `docs/decisions/0006-native-one-shot-llm-architecture.md`
- `docs/decisions/0007-generated-summary-vault.md`
- `docs/decisions/0008-human-distillation-review-overlays.md`
- `docs/archive/2026-native-one-shot-and-artifact-objective.md` preserves the
  complete previous objective and its milestone-level implementation contract.

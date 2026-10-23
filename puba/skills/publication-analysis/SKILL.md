---
name: publication-analysis
description: Resolve bibliographic metadata, render clean markdown, extract figures, and run named LLM distillations against a single academic PDF. Use when the user provides a paper and asks to summarize it, extract its bib info, pull its figures, or run structured questions over its abstract, narrative, full text, or a named section.
license: BSD-3-Clause
metadata:
  audience: researchers, program managers, reviewers
  tool: puba
---

# Skill: Publication Analysis with puba

Use this skill when the user asks you to analyse a single academic PDF —
resolving its bibliographic metadata, rendering clean markdown, extracting
figures, or running named distillation queries against its content.

## Prerequisites

### Installation

```bash
pipx install git+https://github.com/rbross-hpc/pub-analysis.git
```

Python 3.11+ required. Verify: `puba --help`

`puba md` uses the configured MinerU service by default. Local extraction is
available with the `local-mineru` installation extra and `md.extractor: local`.

### Environment variables

The default `bridge` LLM backend uses credentials managed by OpenCode. Run
`opencode providers login` once before using `puba bib` or `puba distill`.

| Variable | Required | Notes |
|---|---|---|
| `OPENAI_API_KEY` | Direct backend only | Required when `llm.backend: openai` |
| `OPENAI_BASE_URL` | Direct backend only | OpenAI-compatible endpoint URL |
| `OPENALEX_MAILTO` | Recommended | Your email — enables the polite pool for faster, more reliable lookups |
| `SEMANTICSCHOLAR_API_KEY` | Optional | Without one, Semantic Scholar is rate-limited; it is only used as a last resort |

Put these in a `.env` file at your working directory; puba loads it automatically.

## Typical workflow

```bash
puba bib paper.pdf           # 1. resolve bibliographic metadata (recommended first)
puba md paper.pdf            # 2. render clean markdown
puba show sections paper.pdf # 3. discover section short names
puba distill paper.pdf       # 4. run distillation queries
puba show info paper.pdf     # 5. combined status check
puba summarize paper.pdf     # 6. write a point-in-time sanity-check snapshot
```

`puba md` runs regardless of bib state. If `bib.yaml` is missing or has
`needs_review=true`, it warns on stderr and renders with the PDF stem as the
title. Run `puba bib` first for a proper header; use `--strict-bib` if you want
`puba md` to exit 3 on unresolved bib.

## Common invocations

```bash
# Bib resolution
puba bib paper.pdf
puba bib paper.pdf --force                        # re-run ignoring cache
puba bib paper.pdf --model "openai/gpt-5.6-sol"
puba bib paper.pdf --bibtex refs.bib              # provide BibTeX as fallback

# Editing bib fields
puba bib edit paper.pdf --set "title=Corrected Title" --clear-review
puba show bib paper.pdf --writable \
  | jq '.title = "Corrected Title"' \
  | puba bib edit paper.pdf --json-file - --source tool:my-agent --clear-review

# Markdown rendering
puba md paper.pdf
puba md paper.pdf --force
puba md paper.pdf --strict-bib   # exit 3 if bib.yaml missing or needs_review=true

# Figure extraction
puba figures paper.pdf

# Distillations
puba distill paper.pdf --list                      # see all queries and status
puba distill paper.pdf --only summary
puba distill paper.pdf --only summary --force
puba distill paper.pdf --model "openai/gpt-5.6-sol"
puba distill paper.pdf --verifier-model "openai/gpt-5.6-sol"

# Inspecting outputs
puba show bib paper.pdf                            # resolved bib fields
puba show bib paper.pdf --json --verbose           # include conflicts and lookup log
puba show sections paper.pdf                       # section short names
puba show section paper.pdf methods                # body of one section
puba show figures paper.pdf                        # figure list
puba show figure paper.pdf fig-3 --json --embed    # one figure with base64 image
puba show distill paper.pdf summary                # distillation text
puba show distill paper.pdf --all --json           # all distillations as JSON
puba show md paper.pdf                             # rendered markdown to stdout
puba show md paper.pdf --head 2000                 # first 2000 chars
puba show info paper.pdf                           # combined status

# Point-in-time sanity-check snapshot (not auto-regenerated; can go stale)
puba summarize paper.pdf

# Housekeeping
puba clean paper.pdf --what all
puba config show
puba config validate
puba config init                                   # copy packaged config to ./puba.config.yaml

# Skill
puba skill show
puba skill export ~/.config/opencode/skills/publication-analysis
```

## Reviewing bib results

When `puba bib` exits 3, the paper's metadata needs review. Common causes:
- Two or more sources disagreed on a field (title, year, authors, venue)
- Core fields (`title`, `authors`, `year`) could not be resolved

```bash
puba show bib paper.pdf --verbose --json | jq ._conflicts
puba show bib paper.pdf --writable \
  | jq '.title = "The Real Title"' \
  | puba bib edit paper.pdf --json-file - --source tool:my-agent --clear-review
```

Fields corrected with `--source human` (the default) or `--source tool:<name>`
are sticky — future `puba bib` runs will not overwrite them.

`puba md` will warn on stderr but still render with tentative metadata. Use
`--strict-bib` to make it exit 3 instead. Distillation scopes that require
`paper.md` (`narrative`, `full`, `section`) are unaffected — they only need
the md stage to have run, not a clean bib.

## Defining distillation queries

One query is built in: `summary` (scope `abstract`). To add your own, create
YAML files in a `prompts/` directory next to your PDFs. Each file can define
one or more named queries:

```yaml
# prompts/my_queries.yaml
contributions:
  scope: narrative
  evidence_mode: claims
  prompt: |
    List the explicit contributions of this paper as a markdown bulleted list.
    Use the paper's own framing.
  max_chars: 800

methods_critique:
  scope: section
  section: methods
  prompt: |
    Critique the methodology. Identify threats to validity and unsupported claims.
  max_chars: 1500
```

Then run:

```bash
puba distill paper.pdf --list           # confirm queries are loaded
puba distill paper.pdf --only contributions
puba distill paper.pdf                  # run everything
```

### Scope

| Scope | What is sent to the LLM | Requires |
|---|---|---|
| `abstract` | Bib header + abstract from resolved metadata | `puba bib` |
| `narrative` | Full paper with references/acknowledgments stripped | `puba bib` + `puba md` |
| `full` | Full paper verbatim | `puba bib` + `puba md` |
| `section` | One named section (add `section: <short_name>`) | `puba bib` + `puba md` |

For `scope: section`, run `puba show sections paper.pdf` first to find the
exact short name — do not guess. If the section does not exist in a given paper,
`puba distill` reports `missing-section` and lists available names.

### Other fields

- **`model`** — optional per-query override (e.g.
  `model: "openai/gpt-5.6-sol"`); falls back to the active backend's
  `models.<backend>.distill` value.
- **`max_chars`** — optional; soft instruction to the LLM + hard truncation if
  exceeded. Omit for no length limit.
- **`evidence_mode`** — `claims` (the packaged default), `quotes`, or `none`.
  `none` writes only the answer. `quotes` requests an answer plus exact quotes
  and checks quote presence locally. `claims` atomizes the finalized answer,
  checks proposed quotes locally, and uses separate LLM calls to assess claim
  support.
- **`verifier_model`** — optional query override for claim-support requests.
  Precedence is CLI `--verifier-model`, query `verifier_model`, then
  `models.<backend>.claim_verify`. The atomizer still uses the generation model.
- **`evidence`** — legacy boolean alias only: `true` means `quotes`, `false`
  means `none`. Never define it together with `evidence_mode`.

Do not report that puba has "verified" an answer. Quote presence is a
deterministic exact-match result: `present`, `not_found`, or `ambiguous`.
Model-assessed claim support is separate and can be `supported`,
`partially_supported`, `unsupported`, `contradicted`, or
`insufficient_evidence`; `not_assessed` is assigned locally when no uniquely
present evidence is available. None of these labels is formal proof, confidence
calibration, or human approval. A present quotation does not establish that it
supports the claim.

Every allowed model support label, including `unsupported`, `contradicted`, and
`insufficient_evidence`, is a successful result and is persisted. By contrast,
a transport error, malformed atomizer/verifier response, unknown evidence ID,
exhausted retry, or request-budget failure aborts the query atomically without
replacing an existing artifact or state entry.

Canonical matching uses the literal bib abstract for `abstract`, raw
`paper.md` for `full`, the selected raw section span for `section`, and the raw
narrative span before configured trailing sections for `narrative`. Offsets
address that source; page values are approximate navigation aids. Exact quotes
are never truncated to fit request budgets.

Results are cached. Prompt, canonical input/span, evidence mode, generation or
verifier model, processing/schema versions, and claim/verification limits all
participate in identity. Per-call provenance records backend, model,
instruction/response-schema version, token estimate, and verifier request
count, but does not establish correctness. New writes are schema v2. Schema-v1
records remain readable through in-memory normalization and are not rewritten
by read-only commands.

### Currency status

`puba distill --list` and `puba show info` use one shared four-state currency
vocabulary for every generated artifact: `current` means its cache key matches
and its output exists and parses; `stale` means a parseable output exists but
its cache key changed; `never-run` means neither an output nor a state entry
exists; and `invalid` means an output cannot be parsed or state references an
output that is missing. These are currency values, not quality values.
`needs_review`, `evidence_mode`, `quote_presence_status`,
`claim_support_status`, and any future human-review state are separate. The
list/info surfaces also report the verifier model where applicable.

## JSON output for agents

Most commands accept `--json`. Output goes to stdout; progress and warnings go
to stderr.

**Every envelope has `"ok": true|false` and `"command": "<name>"`** — check
`ok` before reading any other field. Errors always include `"error"` (message)
and `"error_type"` (exception class name).

### Start here: `puba show info --json`

The recommended first call when picking up an existing paper. Returns a
complete picture of every stage in one shot — no follow-up calls needed to
know what has run and what is available:

```json
{
  "pdf": "/abs/path/paper.pdf",
  "analysis_dir": "/abs/path/paper.puba",
  "state": { "stages": { "bib": { "done": true }, "md": { "done": true } } },
  "bib": { "title": "...", "authors": [...], "year": 2017, "needs_review": false, ... },
  "review_reasons": [],
  "distillations": [
    {
      "name": "summary",
      "status": "current",
      "scope": "abstract",
      "model": "openai/gpt-5.6-sol",
      "verifier_model": "openai/gpt-5.6-sol",
      "evidence_mode": "claims",
      "quote_presence_status": "complete",
      "claim_support_status": "partial"
    }
  ],
  "bib_status": "current",
  "needs_review": false,
  "md_status": "current",
  "figures_status": "current",
  "figures_count": 12,
  "sections_count": 8,
  "distillations_count": 3
}
```

`bib_status`, `md_status`, `figures_status`, and every distillation `status`
use exactly `"current"` | `"stale"` | `"never-run"` | `"invalid"`. Check
`needs_review` separately for bib quality, quote presence separately from
model-assessed claim support, and the verifier model separately from the
generation model. Do not infer human review or certainty from any of them.

### `puba show bib --writable` — agent patch round-trip

`--writable` emits just the fields dict (no `ok`/`command` envelope), ready to
pipe into `puba bib edit --json-file -`:

```bash
puba show bib paper.pdf --writable \
  | jq '.title = "Corrected Title"' \
  | puba bib edit paper.pdf --json-file - --source tool:my-agent --clear-review
```

`puba bib edit --json` confirms with `"fields_changed": ["title"]` and
`"cleared_review": true`.

### Error envelope

```json
{
  "ok": false,
  "command": "show.bib",
  "pdf": "...",
  "error": "bib stage not run for this PDF",
  "error_type": "StageMissingError"
}
```

### Other `--json` commands

| Command | Key fields in successful envelope |
|---|---|
| `puba bib --json` | `ok`, `command`, `pdf`, `analysis_dir`, `bib_path`, `cached`, `needs_review` (+ `review_reasons` if flagged) |
| `puba md --json` | `ok`, `command`, `pdf`, `analysis_dir`, `paper_md`, `paper_sections_json`, `cached`, `bib_status` (`"current"`, `"stale"`, `"never-run"`, or `"invalid"`), `needs_review` |
| `puba show bib --json` | `ok`, `bib` (fields dict), `provenance`, `needs_review`, `review_reasons`; add `--verbose` for `conflicts`, `lookup_log`, `meta` |
| `puba show sections --json` | bare array: `[{"short_name", "title", "level", "start", "end"}, ...]` — no `ok` envelope |
| `puba distill --json` | `ok`, `results`; fresh and cached results include `evidence_mode` and applicable `quote_presence_status`/`quote_presence_counts`, `claim_support_status`/`claim_support_counts`, and `verifier_model` |
| `puba show distill NAME --json` | `ok`, `name`, `scope`, `model`, `generated_at`, `chars`, `output`, `schema_version`, `_provenance`; schema-v2 records expose complete quote or claim structures and separate aggregates |
| `puba show distill --all --json` | `ok`, `count`, `distillations` (array of above) |
| `puba bib edit --json` | `ok`, `fields_changed`, `needs_review`, `cleared_review` |

Plain `puba show distill paper.pdf NAME` intentionally prints only the answer.
Use `--json` when evidence, claim assessments, and provenance matter.
`puba summarize` renders those details and flags non-complete quote presence or
non-fully-supported claims, but it is a point-in-time report that can become
stale and is not a human sign-off.

## Workflow guidance

1. **Run `puba bib` first when possible.** `puba md` will run without it, but
   the rendered markdown will use the PDF stem as the title and have no author
   or venue header. Fix any `needs_review=true` conflicts with `puba bib edit
   --clear-review` before generating the final markdown. Use `--strict-bib` if
   you need `puba md` to fail hard on unresolved bib.

2. **Set `OPENALEX_MAILTO`** before any run for reliable bib lookups.

3. **Discover names before using them.** Run `puba show sections` for section
   short names and `puba show figures` for figure IDs — do not guess.

4. **Start with `puba show info --json`** when picking up an existing paper —
   it gives a complete picture of what has run and what is available.

5. **On MinerU service errors**, verify `md.service.base_url` and the service
   `/health` endpoint. `libGL.so.1` and CUDA troubleshooting applies only to
    `md.extractor: local`. Results are cached, so retrying after a fix only
    re-runs the failed stage.

6. **Treat evidence as review assistance, not certainty.** Inspect linked
   source text for consequential conclusions. Exact matching can fail because
   of extraction differences, atomization can omit or distort claims, page
   locations are approximate, and verifier models can assess support
   incorrectly. Limits bound requests (`max_claims`, evidence count/length,
   atomizer input, per-claim and total verifier input, and rationale length);
   they do not improve epistemic certainty.

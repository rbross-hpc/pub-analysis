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

> **First run of `puba md`:** MinerU downloads ~1.5–3 GB of model weights on
> first use. GPU strongly recommended; CPU-only takes ~10 min per paper.

### Environment variables

| Variable | Required | Notes |
|---|---|---|
| `OPENAI_API_KEY` | **Yes** | LLM API key (Argo: your username; OpenAI: `sk-…`) |
| `OPENAI_BASE_URL` | **Yes** | OpenAI-compatible endpoint URL |
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
puba bib paper.pdf --model "Claude Opus 4.7"
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
puba distill paper.pdf --model "Claude Opus 4.7"

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

- **`model`** — optional per-query override (e.g. `model: "Claude Opus 4.7"`);
  falls back to `models.distill` in config.
- **`max_chars`** — optional; soft instruction to the LLM + hard truncation if
  exceeded. Omit for no length limit.

Results are cached; re-run with `--force` or change the prompt text to
invalidate.

## JSON output for agents

Most commands accept `--json`. Output goes to stdout; progress and warnings go
to stderr.

**Every envelope has `"ok": true|false` and `"command": "<name>"`** — check
`ok` before reading any other field. Errors always include `"error"` (message)
and `"error_type"` (exception class name).

### Start here: `puba show info --json`

The recommended first call when picking up an existing paper. Returns stage
status, the full resolved bib record, and the list of available distillations
in one shot:

```json
{
  "pdf": "/abs/path/paper.pdf",
  "analysis_dir": "/abs/path/paper.puba",
  "state": { "stages": { "bib": { "done": true }, "md": { "done": true } } },
  "bib": { "title": "...", "authors": [...], "year": 2017, "needs_review": false, ... },
  "review_reasons": [],
  "distillations": [
    { "name": "summary", "status": "cached", "scope": "abstract", "model": "Claude Sonnet 4.6" }
  ]
}
```

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
| `puba bib --json` | `ok`, `command`, `pdf`, `analysis_dir`, `bib_yaml`, `cached`, `needs_review` (+ `review_reasons` if flagged) |
| `puba md --json` | `ok`, `command`, `pdf`, `analysis_dir`, `paper_md`, `paper_sections_json`, `cached`, `bib_status` (`"resolved"`, `"review"`, or `"missing"`) |
| `puba show bib --json` | `ok`, `bib` (fields dict), `provenance`, `needs_review`, `review_reasons`; add `--verbose` for `conflicts`, `lookup_log`, `meta` |
| `puba show sections --json` | bare array: `[{"short_name", "title", "level", "start", "end"}, ...]` — no `ok` envelope |
| `puba show distill NAME --json` | `ok`, `name`, `scope`, `model`, `generated_at`, `chars`, `output`, `_provenance` |
| `puba show distill --all --json` | `ok`, `count`, `distillations` (array of above) |
| `puba bib edit --json` | `ok`, `fields_changed`, `needs_review`, `cleared_review` |

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

5. **On MinerU errors** (`libGL.so.1`, CUDA), see the README Troubleshooting
   section. Results are cached — retrying after a fix only re-runs the failed stage.

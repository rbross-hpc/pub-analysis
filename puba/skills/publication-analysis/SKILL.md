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
puba bib paper.pdf           # 1. resolve bibliographic metadata
puba md paper.pdf            # 2. render clean markdown (requires bib to be clean)
puba show sections paper.pdf # 3. discover section short names
puba distill paper.pdf       # 4. run distillation queries
puba show info paper.pdf     # 5. combined status check
```

If `puba bib` exits with code 3, metadata needs review — inspect and fix before
proceeding (see *Reviewing bib results* below).

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
puba show info paper.pdf                           # combined status

# Housekeeping
puba clean paper.pdf --what all
puba config show
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

`puba md` is blocked until the review flag is cleared.

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

All commands that produce structured data accept `--json`. Output always goes to
stdout; progress and errors go to stderr. Every envelope has `"ok": true|false`
and `"command": "<name>"` so you can detect failures without inspecting exit
codes.

### `puba bib --json`

```json
{
  "ok": true,
  "command": "bib",
  "pdf": "/abs/path/paper.pdf",
  "analysis_dir": "/abs/path/paper.puba",
  "bib_yaml": "/abs/path/paper.puba/bib.yaml",
  "cached": false,
  "needs_review": false
}
```

If `needs_review` is `true`, the exit code is 3 and `review_reasons` is added.

### `puba show bib --json`

```json
{
  "ok": true,
  "command": "show.bib",
  "pdf": "...",
  "analysis_dir": "...",
  "needs_review": false,
  "review_reasons": [],
  "bib": {
    "title": "Attention Is All You Need",
    "authors": ["Ashish Vaswani", "Noam Shazeer"],
    "year": 2017,
    "venue": "Advances in Neural Information Processing Systems",
    "doi": "10.48550/arXiv.1706.03762",
    "category": "conference paper",
    "abstract": "..."
  },
  "provenance": { "title": {"source": "openalex", ...}, ... }
}
```

Add `--verbose` to include `conflicts`, `lookup_log`, and `meta`. Use
`--writable` instead to get just the `bib` fields dict (no envelope) —
pipe directly into `puba bib edit --json-file -`.

### `puba show bib --writable` (agent patch round-trip)

```bash
puba show bib paper.pdf --writable \
  | jq '.title = "Corrected Title"' \
  | puba bib edit paper.pdf --json-file - --source tool:my-agent --clear-review
```

`puba bib edit --json` emits:

```json
{
  "ok": true,
  "command": "bib.edit",
  "pdf": "...",
  "fields_changed": ["title"],
  "needs_review": false,
  "cleared_review": true
}
```

### `puba show sections --json`

Returns a bare array (no `ok` envelope):

```json
[
  {"short_name": "introduction", "title": "1 Introduction", "level": 1, "start": 120, "end": 3400},
  {"short_name": "methods",      "title": "2 Methods",       "level": 1, "start": 3401, "end": 7200}
]
```

### `puba show distill NAME --json`

```json
{
  "ok": true,
  "command": "show.distill",
  "pdf": "...",
  "analysis_dir": "...",
  "name": "summary",
  "scope": "abstract",
  "model": "Claude Sonnet 4.6",
  "generated_at": "2026-07-01T14:00:00+00:00",
  "chars": 312,
  "output": "Mofka is a persistent event-streaming framework ...",
  "_provenance": { ... }
}
```

Use `--all --json` to collect every distillation in one call:

```json
{
  "ok": true,
  "command": "show.distill",
  "count": 3,
  "distillations": [ { "name": "summary", "output": "...", ... }, ... ]
}
```

### `puba show info --json`

```json
{
  "pdf": "...",
  "analysis_dir": "...",
  "state": { "stages": { "bib": { "done": true, ... }, "md": { "done": true, ... } } },
  "bib": { "title": "...", "needs_review": false, ... },
  "review_reasons": [],
  "distillations": [
    { "name": "summary", "status": "cached", "scope": "abstract", "model": "Claude Sonnet 4.6" }
  ]
}
```

`show info --json` is the recommended first call when an agent picks up a paper
— it tells you what stages have run and which distillations are available without
reading any individual files.

### Error envelopes

All `--json` errors follow the same shape:

```json
{
  "ok": false,
  "command": "show.bib",
  "pdf": "...",
  "error": "bib stage not run for this PDF",
  "error_type": "StageMissingError"
}
```

Check `ok` before reading any other field.

## Workflow guidance

1. **Run `puba bib` first.** Never proceed to `puba md` or distillations while
   the review flag is set. Fix conflicts, then clear with `--clear-review`.

2. **Set `OPENALEX_MAILTO`** before any run for reliable bib lookups.

3. **Discover names before using them.** Run `puba show sections` for section
   short names and `puba show figures` for figure IDs — do not guess.

4. **Start with `puba show info --json`** when picking up an existing paper —
   it gives a complete picture of what has run and what is available.

5. **On MinerU errors** (`libGL.so.1`, CUDA), see the README Troubleshooting
   section. Results are cached — retrying after a fix only re-runs the failed stage.

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

## What puba does

`puba` is a single-paper CLI. Give it a PDF; it writes a `<pdf>.puba/`
directory containing a verified bibliographic record (`bib.yaml`), clean
markdown (`paper.md`), a section index (`paper.sections.json`), a figure
manifest (`paper.figures.json`), and per-query distillation outputs under
`analyses/`. Every stage is cached in `.state.json` keyed by PDF sha256,
prompt version, and tool version; re-running is cheap.

## Prerequisites

### Installation

```bash
pipx install git+https://github.com/rbross-hpc/pub-analysis.git
```

Verify: `puba --help`

Python 3.11+ required.

> **First run of `puba md`:** MinerU downloads ~1.5–3 GB of model weights into
> `~/.cache/huggingface/` on first use. GPU is strongly recommended; CPU-only
> processing of a 50-page paper takes ~10 minutes.

### Required environment variables

| Variable | Required | Notes |
|---|---|---|
| `OPENAI_API_KEY` | **Yes** | LLM API key. For Argo: your Argo username. For real OpenAI: `sk-…`. |
| `OPENAI_BASE_URL` | **Yes** | OpenAI-compatible endpoint. For Argo: `https://apps.inside.anl.gov/argoapi/v1`. For real OpenAI: `https://api.openai.com/v1`. |
| `OPENALEX_MAILTO` | Recommended | Your email — enables the polite pool for OpenAlex and CrossRef. |
| `SEMANTICSCHOLAR_API_KEY` | Optional | Without one, Semantic Scholar is aggressively rate-limited; it is only used as a last resort. |
| `OPENALEX_API_KEY` | Optional | Most users do not have one; safe to omit. |

Put these in a `.env` file at your working directory; puba loads it automatically.

## Typical workflow

1. **Resolve bib** — `puba bib paper.pdf`
   - If exit code 3, `needs_review: true` is set. Inspect conflicts with
     `puba show bib paper.pdf`, patch with `puba bib edit`, clear the flag
     with `--clear-review`. Do not proceed to `md` until bib is clean.

2. **Render markdown** — `puba md paper.pdf`
   - Blocked (exit 3) when `bib.yaml` is missing or `needs_review: true`.

3. **Discover section names** — `puba show sections paper.pdf`
   - Required before writing any `scope: section` distillation query or
     calling `puba show section`. Never guess short names.

4. **Run distillations** — `puba distill paper.pdf --only summary`
   - The built-in `summary` query uses `scope: abstract`. Add your own
     queries in `prompts/*.yaml` (see *Defining distillation queries* below).

5. **Review everything** — `puba show info paper.pdf`

## Common invocations

### Bibliographic resolution

```bash
puba bib paper.pdf
puba bib paper.pdf --force                        # re-run ignoring cache
puba bib paper.pdf --model "Claude Opus 4.7"      # override LLM for this run
puba bib paper.pdf --no-llm                       # skip LLM; PDF heuristic only
puba bib paper.pdf --bibtex refs.bib              # provide BibTeX as fallback
```

### Editing bib.yaml

```bash
# Fix one field inline
puba bib edit paper.pdf --set "title=Corrected Title" --clear-review

# Agent/tool round-trip: read writable JSON, patch, pipe back
puba show bib paper.pdf --writable \
  | jq '.title = "Corrected Title"' \
  | puba bib edit paper.pdf --json-file - --source tool:my-agent --clear-review

# Dry-run to preview changes
puba bib edit paper.pdf --set "year=2025" --dry-run
```

### Markdown rendering

```bash
puba md paper.pdf
puba md paper.pdf --force
```

### Figure extraction

```bash
puba figures paper.pdf
puba figures paper.pdf --types image,chart         # subset of types
```

### Distillations

```bash
puba distill paper.pdf                             # run all defined queries
puba distill paper.pdf --list                      # status of all queries
puba distill paper.pdf --only summary              # run one query
puba distill paper.pdf --only summary --force      # re-run one query
puba distill paper.pdf --model "Claude Opus 4.7"  # override model
```

### Inspecting outputs

```bash
puba show bib paper.pdf                            # resolved bib fields
puba show bib paper.pdf --json                     # bib as JSON
puba show bib paper.pdf --verbose                  # include conflicts, lookup log, meta
puba show bib paper.pdf --writable                 # fields dict only; pipe into bib edit

puba show md paper.pdf                             # rendered markdown to stdout
puba show md paper.pdf --head 2000                 # first 2000 chars
puba show md paper.pdf --tail 2000                 # last 2000 chars
puba show md paper.pdf --json --include-content    # full markdown embedded in JSON

puba show sections paper.pdf                       # detected sections + short names
puba show section paper.pdf methods                # body of one named section

puba show figures paper.pdf                        # figure manifest
puba show figure paper.pdf fig-3                   # detail for one figure
puba show figure paper.pdf fig-3 --json --embed    # includes base64 JPEG data URL

puba show distill paper.pdf summary                # raw text of one distillation
puba show distill paper.pdf summary --json         # text + provenance as JSON
puba show distill paper.pdf --all --json           # all distillations as JSON

puba show info paper.pdf                           # combined status: bib, stages, distillations
```

### Cleaning and re-running

```bash
puba clean paper.pdf --what bib
puba clean paper.pdf --what md
puba clean paper.pdf --what distill
puba clean paper.pdf --what all
```

### Configuration

```bash
puba config show                                   # resolved config + source of each key
puba config validate                               # check regexes, enums, env vars
puba config init                                   # copy packaged config.yaml → ./puba.config.yaml
```

## Reviewing bib results

When `puba bib` exits 3, `bib.yaml` contains `needs_review: true` and a
`_review_reasons` list. Common triggers:

| Trigger | Reason string |
|---|---|
| ≥2 tier-1 sources disagree on a field | `"sources disagreed: <field>, …"` |
| `title`, `authors`, or `year` missing | `"title missing"` / `"authors missing"` / `"year missing"` |
| LLM bootstrap failed, no DOI or arXiv ID found | `"no identifiers extracted from PDF …"` |

Inspect the conflict values:

```bash
puba show bib paper.pdf --verbose --json | jq ._conflicts
```

Then patch and clear:

```bash
puba show bib paper.pdf --writable \
  | jq '.title = "The Real Title"' \
  | puba bib edit paper.pdf --json-file - --source tool:my-agent --clear-review
```

Fields patched with `--source human` (the default) or `--source tool:<name>`
are **sticky** — future `puba bib` runs will never overwrite them.

`puba md` is blocked until `needs_review: false`. Do not skip this step.

## Defining distillation queries

Add query files to `prompts/` in your working directory:

```yaml
# prompts/contributions.yaml
contributions:
  scope: narrative
  prompt: |
    List the explicit contributions of this paper as a markdown bulleted list.
    Use the paper's own framing.
  max_chars: 800

methods_critique:
  scope: section
  section: methods          # short_name from puba show sections; do not guess
  prompt: |
    Critique the methodology. Identify threats to validity and unsupported claims.
  max_chars: 1500
```

### Scope options

| Scope | Content sent to the LLM |
|---|---|
| `abstract` | Bib header + abstract from `bib.yaml` |
| `narrative` | Bib header + `paper.md` with References, Acknowledgments, etc. stripped |
| `full` | Bib header + `paper.md` verbatim |
| `section` | Bib header + body of one named section (requires `section:` field) |

`scope: abstract` requires only `bib.yaml`. All others require `paper.md`
(run `puba md` first). `scope: section` additionally requires the section to
exist — check with `puba show sections`.

### Output

Each query writes `<pdf>.puba/analyses/<name>.yaml` with `output:` (LLM text)
and full `_provenance` (model, timestamps, sha256 hashes, token estimate).

## Output layout

```
paper.pdf
paper.puba/
  bib.yaml                  # verified bib record + provenance (puba bib)
  paper.md                  # MinerU markdown with YAML frontmatter (puba md)
  paper.sections.json       # section spans: short_name, title, level, start, end
  paper.figures.json        # figure manifest (puba figures)
  .state.json               # cache keys: pdf sha256, stage timestamps, version strings
  analyses/                 # one YAML file per named distillation (puba distill)
  figures/                  # per-figure JPG crops + JSON sidecars (puba figures)
  mineru/                   # MinerU intermediates (debugging; puba clean --what md removes)
```

## Caching and re-runs

Each stage is cached in `.state.json` by PDF sha256 + version key + tool version.
`--force` bypasses the cache for one run. To invalidate all papers for a stage
after changing a prompt or upgrading MinerU, bump the version key in
`puba.config.yaml`:

```yaml
prompt_versions:
  bib_extract: "bib-3"      # bump to re-run puba bib on all papers

md:
  mineru_version: "mineru-2" # bump to re-run puba md on all papers
```

Distillation cache keys additionally include the model name — changing the
model triggers a re-run automatically.

## JSON I/O

Most commands accept `--json` for machine-readable output. For `puba bib` and
`puba md`, `--json` also implies `--quiet`; errors are emitted as JSON too.
Use `puba show distill --all --json` to collect every distillation in one call.

## Workflow guidance

1. **Always run `puba bib` first.** Never proceed to `puba md` or distillations
   while `needs_review: true`. Inspect `_conflicts` and patch with `puba bib edit`.

2. **Set `OPENALEX_MAILTO`** to your email before any run, for the polite pool.

3. **Discover names before referencing them.** Use `puba show sections` to find
   section short names and `puba show figures` to find figure IDs before using
   them in queries or `show` commands — do not guess.

4. **Prefer `--json` when scripting.** Use `puba show info` for a human
   overview of stage status and distillation results.

5. **On MinerU errors** (`libGL.so.1: cannot open shared object file`, CUDA
   unavailable), consult the Troubleshooting section of the README. puba's
   caching means retrying after a fix is safe — only the failed stage re-runs.

# puba — publication analysis

Single-paper CLI: given one PDF, resolve its bibliographic information with full
provenance, render a clean markdown version, and run named LLM distillations
against the abstract, narrative, or full paper.

## Documentation

- [docs/configuration.md](docs/configuration.md) — env vars, models, Argo endpoint,
  rate limits, conflict thresholds, classification lists, prompt versions
- [docs/bib-yaml.md](docs/bib-yaml.md) — `bib.yaml` schema, field reference,
  source priority, resolution flow, category enum, provenance entries
- [docs/distillations.md](docs/distillations.md) — defining distillation queries,
  scopes, `prompts/` directory, output schema, caching
- [tests/fixtures/README.md](tests/fixtures/README.md) — fixture licensing and
  criteria for adding new test PDFs

---

## Install

```bash
pipx install git+https://github.com/<user>/pub-analysis.git
```

For development:

```bash
python3.11 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

Python 3.11+ required.

## Environment

At minimum:

```
OPENAI_API_KEY=rross
OPENALEX_MAILTO=you@anl.gov
```

Put these in a `.env` file at the repo root (already in `.gitignore`). Full
details including optional variables in
[docs/configuration.md](docs/configuration.md#environment-variables).

---

## Quick start

A typical first-time workflow for a new paper:

```bash
# 1. Resolve bibliographic metadata (title, authors, venue, DOI, …)
puba bib paper.pdf

# 2. Render clean markdown + detect sections
puba md paper.pdf

# 3. Inspect what sections were detected
puba show sections paper.pdf

# 4. Run the built-in summary distillation (scope: abstract)
puba distill paper.pdf --only summary

# 5. Review everything
puba show info paper.pdf
```

Steps 1 + 2 together as one command:

```bash
puba run paper.pdf
```

### Distillations quick start

Define a query in `prompts/my_queries.yaml`:

```yaml
contributions:
  scope: narrative
  prompt: |
    List the explicit contributions of this paper as a markdown bulleted list.
    Use the paper's own framing.
  max_chars: 800

methods_critique:
  scope: section
  section: methods        # short_name from `puba show sections paper.pdf`
  prompt: |
    Critique the methodology. Identify threats to validity and unsupported claims.
  max_chars: 1200
```

Then run:

```bash
puba distill paper.pdf                  # run all defined queries
puba distill paper.pdf --list           # see status of all queries
puba distill paper.pdf --only methods_critique --force   # re-run one
```

### Other useful commands

```bash
# Bib only (no markdown)
puba bib paper.pdf

# Markdown only, skip LLM cleanup for speed
puba md paper.pdf --no-llm-cleanup

# Show resolved configuration with per-key source
puba config show

# Validate configuration syntax (regexes, enums, env vars)
puba config validate

# Remove cached outputs and re-run fresh
puba clean paper.pdf
puba run paper.pdf --force
```

---

## Output layout

Each PDF gets its own analysis directory next to it:

```
paper.pdf
paper.puba/
  bib.yaml              # verified bibliographic record + per-field provenance
  paper.md              # clean markdown with YAML frontmatter
  paper.raw.txt         # raw extracted text (debug / reproducibility)
  paper.sections.json   # detected section spans {title, level, start, end}
  .state.json           # pdf sha256, stage timestamps, prompt versions (cache key)
  analyses/             # distillation outputs, one YAML file per named query
```

If the PDF is on a read-only filesystem, `puba` will error. There is no
auto-fallback output directory; use a writable copy of the PDF.

---

## CLI reference

| Command | What it does |
|---|---|
| `puba run <pdf>` | Full pipeline: bib then md, sequential; stops after bib if review needed (exit 3) |
| `puba bib <pdf>` | Resolve and write bibliographic information; exit 3 if `needs_review=true` |
| `puba md <pdf>` | Render clean markdown |
| `puba distill <pdf>` | Run all defined distillation queries |
| `puba distill <pdf> --only NAME` | Run one named distillation |
| `puba distill <pdf> --list` | List defined queries and their cached status |
| `puba clean <pdf>` | Remove cached outputs |
| `puba show bib <pdf>` | Read resolved bib fields + provenance (auto-resolves if needed) |
| `puba show md <pdf>` | Print rendered markdown to stdout (auto-renders if needed) |
| `puba show sections <pdf>` | List detected sections with short names and full titles |
| `puba show info <pdf>` | Combined status: bib summary, stage cache, distillations |
| `puba show distill <pdf> NAME` | Print the raw text of a named distillation |
| `puba show distill <pdf> NAME --json` | Emit distillation text + provenance as JSON |
| `puba show distill <pdf> --all --json` | Emit all distillations as JSON |
| `puba config show` | Print fully resolved configuration + source of each key |
| `puba config validate` | Validate regexes, enums, required env vars |
| `puba config init` | Copy packaged config.yaml into CWD as puba.config.yaml |

### Key flags

| Flag | Applies to | Effect |
|---|---|---|
| `--force` | bib, md, run, distill | Re-run even if stage is cached |
| `--no-llm` | bib | Skip LLM title extraction; use PDF cover-page heuristic only |
| `--bibtex FILE` | bib | Provide a `.bib` file as a fallback metadata source. Must exist, be a file (not a directory), and contain at least one parseable entry; otherwise the stage fails. |
| `--dry-run` | bib, md | Print what would run without running it |
| `--no-llm-cleanup` | md | Skip per-section LLM cleanup; emit repaired raw text |
| `--only NAME` | distill | Run only the named distillation (repeatable) |
| `--list` | distill | List all defined queries with cached status |
| `--json` | bib, md, run | Emit a JSON result object on stdout; implies `--quiet`; errors are also JSON. Mutually exclusive with `--dry-run`. |
| `--json` | show bib, show md, show sections, show info, show distill | Output as JSON instead of Rich table; required for `--all` in show distill |
| `--all` | show distill | Emit every distillation; requires `--json` |
| `--verbose` | show bib | Include `conflicts`, `lookup_log`, and `meta` in JSON output |
| `--include-content` | show md | Inline markdown text and sections list into JSON (requires `--json`) |
| `--no-run` | show bib, show md, show sections | Error instead of auto-running the stage |
| `--what bib\|md\|state\|distill\|all` | clean | What to remove |

---

## Markdown rendering

`puba md` produces `paper.md` with:

- YAML frontmatter (title, authors, year, venue, doi, arxiv\_id, bib\_yaml\_sha)
- `# Title`, author line, venue · year
- Sections as `##` / `###` (config-driven heading detection + numbered section regex)
- Page boundaries as HTML comments (`<!-- page 7 -->`) for downstream tools
- Figure captions as `*Figure N: ...*`
- Footnotes as `[^1]` markdown footnotes
- References as a numbered list (raw text per entry)
- Math preserved as `$...$` / `$$...$$` where pdfplumber yields it
- Tables: skipped in v1

PDF text is repaired before assembly: hyphenated line-breaks, split-glyph
artifacts (`V ector` → `Vector`), Unicode ligatures (fi, fl, ff, ffi, ffl),
soft hyphens.

Each detected section is sent to Argo with a strict "fix extraction artifacts
only, do not rewrite" prompt. Long sections (> 8000 tokens) are split on
paragraph boundaries. Disable cleanup with `--no-llm-cleanup`. If cleanup fails
for any section, the whole `puba md` run fails rather than silently producing
partial output.

---

## Caching

Each stage is cached in `<pdf>.puba/.state.json`, keyed by PDF sha256,
`prompt_version`, and `tool_version`. A run is a no-op when all three match.
`--force` bypasses the cache. `.state.json` corruption is treated as "no prior
run" and the stage re-runs cleanly. Cached no-ops are shown as `(cached)` next
to the output path in non-JSON output.

To invalidate all papers for a stage after changing a prompt, bump
`prompt_versions.bib_extract` (or `md_cleanup`) in `config.yaml` or
`puba.config.yaml`. See
[docs/configuration.md](docs/configuration.md#prompt-versions-and-cache-invalidation).

---

## Multi-paper batch

```bash
for f in *.pdf; do puba run "$f"; done
```

---

## Development

```bash
# Offline tests only (no network calls)
pytest tests/ -m 'not network'

# All tests including live API calls
OPENAI_API_KEY=rross pytest tests/ -v

# End-to-end bib resolution tests only
OPENAI_API_KEY=rross pytest tests/test_e2e_bib.py -v
```

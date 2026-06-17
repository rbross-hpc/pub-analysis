# puba — publication analysis

Single-paper CLI: given one PDF, resolve its bibliographic information with full
provenance and render a clean markdown version.

Phase-2 planned: distillation, Q&A (outputs will land in `<pdf>.puba/analyses/`).

## Documentation

- [docs/configuration.md](docs/configuration.md) — env vars, models, Argo endpoint,
  rate limits, conflict thresholds, classification lists, prompt versions
- [docs/bib-yaml.md](docs/bib-yaml.md) — `bib.yaml` schema, field reference,
  source priority, resolution flow, category enum, provenance entries
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

```bash
# Full pipeline: resolve bib + render markdown
puba run paper.pdf

# Bib only
puba bib paper.pdf

# Markdown only (skip LLM cleanup for speed)
puba md paper.pdf --no-llm-cleanup

# Show resolved bib summary + stage status
puba info paper.pdf

# Show resolved configuration (with per-key source)
puba config show

# Validate configuration (regexes, enums, env vars)
puba config validate
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
  analyses/             # reserved for future distillation / Q&A tool
```

If the PDF is on a read-only filesystem, `puba` will error. There is no
auto-fallback output directory; use a writable copy of the PDF.

---

## CLI reference

| Command | What it does |
|---|---|
| `puba run <pdf>` | Full pipeline: bib then md, sequential |
| `puba bib <pdf>` | Resolve and write bibliographic information |
| `puba md <pdf>` | Render clean markdown |
| `puba info <pdf>` | Show bib summary + stage cache status |
| `puba clean <pdf>` | Remove cached outputs |
| `puba config show` | Print fully resolved configuration + source of each key |
| `puba config validate` | Validate regexes, enums, required env vars |
| `puba distill <pdf>` | *(phase 2 — not yet implemented)* |
| `puba ask <pdf> "..."` | *(phase 2 — not yet implemented)* |

### Key flags

| Flag | Applies to | Effect |
|---|---|---|
| `--force` | bib, md, run | Re-run even if stage is cached |
| `--no-llm` | bib | Skip LLM title extraction; use PDF cover-page heuristic only |
| `--bibtex FILE` | bib | Provide a `.bib` file as a fallback metadata source |
| `--dry-run` | bib, md | Print what would run without running it |
| `--no-llm-cleanup` | md | Skip per-section LLM cleanup; emit repaired raw text |
| `-q` / `--quiet` | all | Suppress Rich progress output |
| `--json` | info | Output as JSON instead of Rich table |
| `--what bib\|md\|state\|all` | clean | What to remove |

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
run" and the stage re-runs cleanly.

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

# puba — Design and Decisions Log

This document records the design rationale and key decisions made during
development. It is a retrospective design doc, not a quickstart guide.
For usage, see README.md. For configuration, see docs/configuration.md.
For the bib.yaml schema, see docs/bib-yaml.md. For distillations, see
docs/distillations.md.

---

## Project context and motivation

`puba` grew out of three predecessor projects:

- **annual-report** (`/workspaces/annual-report`) — automated DOE RPPR drafting
  pipeline. Builds a vector+BM25 index over publication PDFs and quarterly docs,
  then drafts 24 report sections via RAG. Contains a polished publication
  stub generator (`extract_publication_stub_one.py`) and per-publication
  distillation pipeline (`distill_publication_one.py`) that served as the
  primary design source for puba.

- **ref-checker** (`github.com/rbross-hpc/ref-checker`) — verifies references in
  an academic paper by querying OpenAlex, CrossRef, DBLP, Semantic Scholar, and
  arXiv. Contributed PDF text repair, multi-source bibliographic lookup clients,
  and the sidecar + rate-limit patterns.

- **paper_thing** (`github.com/rbross-hpc/paper_thing`) — prototype corpus
  management system. Contributed the conceptual data model (Paper → Sections →
  Content units) which informs `paper.sections.json`. Not used as a code source
  because it operates on a SQLite corpus; puba is deliberately file-based.

**Design philosophy:** take the best of all three, apply lessons learned from
the pain points, but keep the tool focused on a single paper at a time. No
daemon, no corpus DB, trivially diff-able outputs, human-editable everywhere.

---

## Architecture: single-paper, file-based

The fundamental decision: **one PDF = one analysis directory** (`<pdf>.puba/`)
sitting next to the PDF.

```
paper.pdf
paper.puba/
  bib.yaml              bibliographic record + per-field provenance
  paper.md              clean markdown rendering
  paper.raw.txt         raw extracted text (debug)
  paper.sections.json   detected section spans
  .state.json           per-stage cache key (sha256, prompt, model)
  analyses/
    summary.yaml        distillation outputs, one file per named query
    ...
```

### Why file-based over a corpus DB

- annual-report's per-publication YAML sidecars proved extremely useful in
  practice: diff-able, grep-able, human-editable, work with any editor, and
  require no schema migration when the format evolves.
- paper_thing's SQLite approach is better for corpus-level operations (find all
  papers by author X, join with embeddings). puba is not a corpus tool.
- A future corpus tool can aggregate puba outputs with a simple
  `find . -name bib.yaml -path '*/*.puba/*'` scan — no migration needed.

### Why `.puba/` directory, not flat sidecars

ref-checker uses flat sidecar files (`paper.refs.json`, `paper.results.json`).
That pattern works when there are 2–3 outputs, but puba anticipates multiple
distillation analyses (`analyses/*.yaml`), a rendered markdown, a raw text
file, and a sections JSON. A container directory keeps the user's folder clean
and groups all outputs so `puba clean` is a single `rm -rf` on one directory.

---

## Bibliographic resolution (`puba bib`)

### Source priority chain

```
human > osti > openalex > crossref > dblp > bibtex > arxiv > pdf > llm > semanticscholar > derived > unknown
```

**Rationale for this order:**

- `human` is always sticky — the user's manual correction is the ground truth.
- `osti` ranks above `openalex` because for DOE-funded work, OSTI has the
  canonical record (award number, OSTI ID, sometimes the only place the paper
  is deposited). OSTI data is often authoritative even when OpenAlex has it too.
- `openalex` above `crossref`: OpenAlex reconstructs abstracts from inverted
  indices and provides OA status and keyword data that CrossRef does not.
  CrossRef is the DOI registration authority so it's more reliable for DOI
  confirmation, but OpenAlex is richer.
- `crossref` above `dblp`: DBLP is CS-only and does not include journal
  article metadata as reliably as CrossRef.
- `bibtex` above `arxiv`: a user-provided .bib file is more likely to have
  the correct venue and year for a published paper than arXiv (which may
  have the preprint date, not the publication date).
- `arxiv` above `pdf`: arXiv's API returns structured metadata; PDF heuristics
  can mis-parse.
- `pdf` above `llm`: a regex match on a DOI printed in the PDF is more reliable
  than an LLM's interpretation.
- `llm` above `semanticscholar`: Semantic Scholar is a last-resort fallback
  because it rate-limits unauthenticated requests aggressively (up to 8s
  between calls) and its data quality on title matching is variable.

### Tier-1 parallel + fallback chain

Three tiers of querying, established in the plan after considering three
options (per-field short-circuit, tier-1-parallel, or query-everything):

**Option B chosen: tier-1 parallel, fallbacks short-circuit.**

- OpenAlex, CrossRef, and OSTI are always queried in parallel regardless of
  whether a DOI was found, because (a) they are the most authoritative and
  (b) their rate limits are permissive enough to afford parallel calls.
- Conflicts between tier-1 sources on title/year/authors/venue/doi are
  detected and recorded, setting `needs_review: true`.
- Fallback sources (DBLP, arXiv title search, BibTeX, LLM, Semantic Scholar)
  run only when tier-1 left gaps in core fields (title, authors, year).
- arXiv-by-ID always runs when an arXiv ID is known (cheap and fills
  arxiv-specific fields).

### LLM title bootstrap

The most important lesson from annual-report: **extract the title from page 1
using the LLM before querying any external source.** Without a title, all
title-based API searches fail, leaving only the DOI path open.

annual-report's `extract_publication_stub_one.py` calls `_llm_extract_title()`
as the *first* step, before any source is queried. puba initially missed this
and instead used a heuristic line-scoring pass over the cover page — which
worked for the ZFP technical report (clear single-line title) but failed for
the Mofka paper (two-line title, no DOI on page 1). The LLM bootstrap was
added after seeing this failure.

The heuristic remains for `--no-llm` users (offline, air-gapped, quota-
limited), and was also improved to join continuation lines (if the best-scoring
line is immediately followed by a lowercase-starting line of similar length,
they are joined as one title).

### PDF scan: first 3 pages, not just page 1

annual-report's stub generator scans page 1 only. puba scans pages 1–3 because
OSTI technical reports and some conference papers put the DOI on a disclaimer
or copyright page (page 2) rather than the cover (page 1). This is why the ZFP
spectral report fixture tests pass: its DOI appears on an OSTI deposit page
after the cover.

### Category classification

All classification lists (conference acronyms, preprint DOI prefixes, preprint
hosts, technical report venue patterns, etc.) are config-driven. The cascade
order is hardcoded in `puba/bib/classify.py`:

1. arXiv preprint (no real DOI)
2. Preprint server (DOI prefix or host)
3. Book-related (CrossRef type)
4. Technical report (CrossRef type)
5. Thesis (CrossRef type)
6. Thesis (venue pattern)
7. Technical report (venue pattern)
8. Workshop (venue pattern)
9. Conference (venue acronym, whole-word)
10. Conference (venue pattern)
11. Journal article (venue present, no other signal)
12. Other (fallthrough)

This ordering was refined through fixture testing: a paper with venue "Office
of Scientific and Technical Information (OSTI)" initially classified as
"journal article" (rule 11) before CrossRef type `report` was added as rule 4.

**`arxiv preprint` is a first-class category** — annual-report's enum mapped
it to "other publication". puba treats it as distinct so downstream tools can
filter arXiv preprints without regex heuristics.

### arXiv DOI handling

OpenAlex returns `10.48550/arxiv.<id>` as the DOI for arXiv-only papers.
puba treats this as "no real DOI": `doi` is set to null, `arxiv_id` is
populated, and the category cascade resolves to `arxiv preprint`. This keeps
the DOI field semantically clean (only real publisher DOIs) and prevents the
`arxiv preprint` classification rule from missing its trigger.

---

## Markdown rendering (`puba md`)

### PDF extraction

pypdf is tried first; pdfplumber is used as a fallback per page when the
pypdf output is below a minimum character threshold. Both are available
in the annual-report environment already.

### Text repair

Ported from ref-checker with additions:

- **De-hyphenation:** joins `word-\nword` across line breaks, protecting URLs
  (URLs are replaced with placeholders before any repair pass).
- **Ligature normalization:** Unicode fi/fl/ff/ffi/ffl → ASCII equivalents.
- **Soft-hyphen stripping:** removes U+00AD (invisible hyphen used in some PDFs).
- **Split-glyph fix:** `V ector` → `Vector` (capital letter + space + lowercase
  word artifact from some font encodings).
- **Tabular numeral glyphs:** `/zero.tnum`, `/one.tnum`, … → `0`, `1`, … Added
  after discovering that Frontiers PDFs encode page numbers and DOIs using
  named tabular numeral glyphs that pdfplumber passes through literally. This
  was discovered during the first `puba md` run on `klasky-5.pdf`.

### Section detection

Config-driven: heading words (`md.section_heading_words`) and a numbered-
section pattern (`md.section_numbered_pattern`). Detection is intentionally
conservative — the heuristic requires short lines (≤ 6 words) that are either
an exact heading word or a known heading word followed by a short continuation.

**Two-column PDFs over-fire the detector.** When a journal renders two columns
and pdfplumber linearizes them, mid-sentence lines can appear as standalone
short lines that score as headings. The current detector does not solve this
fully; LLM section cleanup (`--no-llm-cleanup` off) smooths the text but does
not restructure the section tree. A Marker/Docling/vision backend would be
needed for reliable two-column section detection.

### Short-name derivation (`short_name`)

Every detected section gets a `short_name` — a stable, human-friendly
identifier used to target sections in `scope: section` distillations. The
derivation algorithm (`derive_short_name` in `puba/pdf/sections.py`):

1. Strip any leading numeric prefix and trailing punctuation
   (`"2.1 Related Work:"` → `"Related Work"`, `"1 Introduction"` → `"Introduction"`)
2. Lowercase the result
3. Split on any non-alphanumeric character run (spaces, hyphens, slashes, etc.)
4. Keep at most the **first 4 words**
5. Join with `_`
6. If the result begins with a digit, prefix with `s_`
7. If the result is empty, use the fallback `"section"`

The 4-word cap was chosen after observing that section titles longer than
4 words produced identifiers that were unpleasant to type in YAML
(`fms_become_cost_effective_when_number_of_training_time` → should be
`fms_become_cost_effective`). A character cap (originally 40) was rejected
because it produced arbitrary mid-word truncations.

**Collision disambiguation:** when two sections produce the same base slug
(e.g., two "Discussion" sections in a paper with two parts), the second
gets `_2`, the third `_3`, and so on. The suffix is appended to the full
base slug, not to a truncated form.

**Backwards compatibility:** `load_sections_json` silently re-derives
`short_name` for any entry in `paper.sections.json` that is missing the
field. This means papers analyzed before `short_name` was introduced do not
need to be re-run through `puba md`.

### `puba sections` command

`puba sections <pdf>` requires that `puba md` has been run first (it reads
`paper.sections.json`). It prints a Rich table with `short_name`, `level`, and
the full `title` of each detected section. `--json` emits the raw
`paper.sections.json` content.

The primary use case is discovering short names before writing a
`scope: section` distillation query. Without running `puba sections` first, a
user has no way to know the exact `short_name` to put in the YAML definition.

### LLM section cleanup

Enabled by default; disable with `--no-llm-cleanup`. Each section body is sent
to Argo with a strict "fix extraction artifacts only, do not summarize, do not
rewrite" prompt. Sections over 8k tokens are split on paragraph boundaries.

**Fail-fast:** if cleanup fails for any section, `puba md` fails the whole run.
This is deliberate — silent fallback to uncleaned text would produce inconsistent
markdown that is hard to debug. The user can re-run with `--no-llm-cleanup` to
get the raw repaired text.

### Tables, figures, footnotes, math

- **Tables:** skipped in v1. The pdfplumber table-extraction API is available
  but unreliable across PDF layouts; LLM vision extraction would be needed for
  reliable tables.
- **Figures:** captions rendered as `*Figure N: ...*`; images not extracted.
- **Footnotes:** rendered as `[^1]` markdown footnotes.
- **Math:** preserved as `$...$` / `$$...$$` where pdfplumber yields LaTeX-like
  text; no symbolic reconstruction.
- **Page boundaries:** preserved as `<!-- page N -->` HTML comments so downstream
  tools (e.g., a future ref-checker integration) can map text spans back to PDF
  pages.

---

## Distillation (`puba distill`)

### Design provenance

Annual-report's `distill_publication_one.py` distills each publication's
abstract into four structured fields (accomplishment, discipline_impact,
domain_impact, domain_tags) using a single LLM call with a fixed prompt.
That prompt is RPPR-specific and DOE-flavored.

puba generalizes this: instead of a fixed schema, the user defines named
queries with arbitrary prompts. The output schema (`output:` field) is
intentionally unstructured — the prompt is the contract.

### Four scopes

- `abstract` — cheapest; requires only `bib.yaml`. Works even when `puba md`
  has not been run.
- `narrative` — strips trailing sections (References, Acknowledgments, etc.)
  from `paper.md` before sending. Sections to strip are config-driven
  (`distill.narrative_strip_sections`). Page markers (`<!-- page N -->`) are
  also stripped.
- `full` — sends `paper.md` verbatim.
- `section` — sends the body of a single named section from `paper.md`,
  identified by its `short_name`. Requires `bib.yaml` + `paper.md` (and
  therefore `paper.sections.json`). Page markers are stripped from the body
  before sending. See "Section scope design" below.

**No map-reduce in v1.** If input exceeds `max_input_tokens` (default 100k),
puba errors with a clear message suggesting a narrower scope or a larger-
context model. Map-reduce distillation (split by section, run per section,
stitch) is deferred because the stitching semantics depend heavily on the
prompt (some prompts need global context; others don't).

### Section scope design

The query definition must include a `section:` field with a valid `short_name`:

```yaml
methods_critique:
  scope: section
  section: methods
  prompt: |
    Critique the methodology ...
```

**Validation is syntax-only at config time.** `puba config validate` checks
that (a) the `section:` field is present when `scope: section` and (b) the
value matches the identifier pattern `^[a-zA-Z_][a-zA-Z0-9_]*$`. It does
*not* check whether that section actually exists in any paper — that would
require cross-paper knowledge that is out of scope.

**Runtime error if section is missing.** If the named section is not present
in a particular paper's `paper.sections.json`, `puba distill` records the
query as `missing-section` status (not a fatal error). The error message lists
all available short names for that paper so the user can correct the config or
`--only` the query against a different paper.

**`missing-section` is non-fatal.** One query's missing section does not block
other queries in the same run. The exit code is 1 if any query failed or had
`missing-section`, 0 if all succeeded.

**`puba distill --list` and `puba info` surface section status.** The Target
column shows the `short_name` for `scope: section` queries and the current
status (including `missing-section` in red).

**Why syntax-only validation (not a cross-paper check)?** The alternative was
to validate that `section:` names exist in some known paper set. Rejected
because: (a) puba is intentionally single-paper — there is no corpus to check
against; (b) a query targeting `methods` may be valid for 80% of papers and
miss for 20% — that is expected and handled gracefully at runtime; (c) prompts
are often written before the paper is processed, so the section may not yet
exist in any local `paper.sections.json`.

**Why case-sensitive matching with no aliases?** The `short_name` derivation
is deterministic and lowercased, so all short names are already lowercase.
Case-insensitive matching adds complexity with no real benefit. Aliases
(e.g., `methods` → also matches `methodology`) are deferred to a potential
LLM-above-tool layer that could map user intent to short names.

### Cache key

Per-query cache key: `sha256(input_content) + sha256(resolved_prompt) + model_name`.

Unlike bib and md stages (which use `prompt_version` strings), distillation
uses content hashing. Rationale: for bib/md, the "prompt" is a complex function
of code + config + schema and is hard to hash reliably; the `prompt_version`
string lets developers declare "this is a meaningfully different prompt".
For distillation, the prompt is a literal string in config/prompts, so
content hashing is exact and requires no manual version bumping.

Model name is in the distillation cache key (but not in bib/md) because
model choice has a direct, visible effect on the output text.

### `max_chars`: soft + hard

A soft instruction (`"Your response MUST be at most N characters. Be concise."`)
is appended to the prompt. LLMs comply ~95% of the time. A hard truncation at
word boundary with `…` is applied as a safety net. Truncation is logged in
`_provenance.truncated` so it is auditable.

Wording of the soft instruction is hardcoded — making it configurable adds
complexity for negligible benefit.

### Output format

puba does not enforce or parse the output format. UTF-8 verbatim, stored as
a YAML block scalar. Post-processing: trailing whitespace stripped per line,
leading/trailing blank lines stripped. Nothing else. The user's prompt
determines whether the output is prose, markdown, JSON, etc.

### `prompts/` directory

Distillation queries can be defined in `./prompts/*.yaml` files (one or more
queries per file, top-level YAML keys, no wrapper). Load order is:
packaged `config.yaml` → `puba.config.yaml` → `prompts/*.yaml` (alphabetical).
Later sources win on name collision. Same-name collisions *within* `prompts/`
(two files defining the same query name) are a hard validation error.

This separates the operational configuration (models, rate limits, thresholds)
from the analytical prompts (user-specific question definitions).

---

## Caching strategy

### `.state.json` structure

```json
{
  "pdf_sha256": "<sha>",
  "tool_version": "0.1.0",
  "stages": {
    "bib":  { "completed_at": "...", "prompt_version": "bib-2",
              "tool_version": "...", "input_sha": "<pdf-sha>" },
    "md":   { "completed_at": "...", "prompt_version": "md-cleanup-1",
              "tool_version": "...", "input_sha": "<pdf-sha>" },
    "distill": {
      "summary": { "completed_at": "...", "input_sha": "...",
                   "prompt_sha": "...", "model": "...", "tool_version": "..." }
    }
  }
}
```

`.state.json` corruption (e.g., interrupted write before atomic rename was
introduced) is treated as "no prior run" and the stage re-runs. This is safer
than aborting — the user can always run again.

### Why `prompt_version` for bib/md but content hashing for distill

- bib stage: the effective "prompt" includes the source priority chain, the
  classification cascade, and the LLM title-extraction prompt. No single string
  to hash. A developer bumps `bib.prompt_version` when they change something
  meaningful.
- md cleanup stage: the cleanup prompt is `_common_prompts.MD_CLEANUP_SYSTEM`
  which is a hardcoded string. Content hashing would work, but the prompt
  version string lets developers and ops people communicate "we changed the
  cleanup prompt" without looking at source code.
- distill stage: the prompt is a literal user-supplied string. Content hashing
  is exact, obvious, and requires no manual bumping. The user just edits the
  prompt and the cache invalidates automatically.

---

## What puba does NOT do (explicit non-goals, v1)

These were explicitly ruled out in the design phase:

- **Multi-paper corpus operations** — no corpus DB, no cross-paper search,
  no aggregation. A future corpus tool can scan `*.puba/bib.yaml` files.
- **Reference verification** — verifying the paper's own reference list is
  ref-checker's job. puba records `references_count` in `bib.yaml` as a
  hand-off field.
- **Figure/image extraction** — would require a vision-capable backend or
  Marker/Docling.
- **Table extraction** — pdfplumber's table API is unreliable across layouts.
- **Read-only filesystem support** — if the PDF is in a read-only path, puba
  errors with a clear message. No auto-fallback to `~/.cache/puba/` (surprises
  bad). The user copies the PDF to a writable location.
- **Web UI** — CLI only.
- **HTTP response caching across runs** — the per-paper source query volume
  is low (one paper at a time); an HTTP cache would add complexity without
  meaningful benefit. If batch use emerges, `requests-cache` could be layered in.
- **Prompt-editing CLI** — prompts live in `config.yaml` / `prompts/*.yaml`
  and are edited with `$EDITOR`. No `puba distill new` / `puba distill edit`.
- **Auto-append format instructions** — puba does not inject "Return JSON only"
  or "Format as markdown" into the prompt; the user's prompt is the contract.
- **`puba run` including distillation** — distillation is an explicit `puba
  distill` step because it is the most expensive LLM operation and many users
  will want bib+md but not distillation.

---

## Planned future work

- **Map-reduce distillation** — for `scope=full` on very long papers that
  exceed context windows. Section-by-section distillation with a stitching
  pass.
- **Marker / Docling / vision backend** — for `puba md --backend marker` to
  get higher-fidelity markdown (reliable tables, math, two-column layout) at
  the cost of an ML model dependency.
- **Ref-checker integration** — `puba refs check <pdf>` that invokes
  ref-checker as a library/subprocess and writes results to `analyses/`.

---

## Repo layout

```
pub-analysis/
  README.md                  quickstart, CLI reference, output layout
  PLAN.md                    this file — design rationale and decisions log
  LICENSE                    BSD-3-Clause
  pyproject.toml             Python package; console script: puba
  environment.yml            conda env for development
  config.yaml                packaged defaults (models, rate limits, distill)
  prompts/                   (user-created) distillation prompt YAML files
  docs/
    configuration.md         all config knobs: env vars, models, rate limits,
                             classification lists, section heading detection,
                             prompt versions, distillation config
    bib-yaml.md              bib.yaml schema, field reference, source priority,
                             resolution flow, category enum, provenance entries
    distillations.md         distillation query definition, scopes, output schema,
                             caching, narrative stripping, prompts/ tutorial
  puba/
    __init__.py              __version__
    cli.py                   Typer dispatcher: bib, md, run, info, clean,
                             distill, config show/validate
    config.py                load + override resolution + show + validate
    io.py                    sha256, atomic writes (adapted from annual-report)
    state.py                 .state.json per-stage cache management
    sidecar.py               bib.yaml read/write + provenance merge
    _common_prompts.py       LLM prompt strings (BIB_EXTRACT_SYSTEM, MD_CLEANUP_SYSTEM)
    pdf/
      extract.py             pypdf + pdfplumber wrappers
      repair.py              de-hyphenation, glyph fixes, tnum repair, ligatures
      sections.py            config-driven heading detection
    bib/
      stub.py                orchestrates PDF heuristics, LLM bootstrap, tier-1
                             parallel, fallback chain, provenance, category
      classify.py            config-driven category classification cascade
      conflicts.py           tier-1 source conflict detection
      sources/
        _common.py           rate limits, DOI/arXiv extraction, similarity
        openalex.py          OpenAlex client (adapted from annual-report)
        crossref.py          CrossRef client (adapted from ref-checker)
        dblp.py              DBLP client (adapted from ref-checker)
        arxiv.py             arXiv client (adapted from annual-report)
        osti.py              OSTI client (adapted from annual-report)
        bibtex.py            BibTeX file parser (adapted from annual-report)
        llm.py               Argo page-1 extractor
        semanticscholar.py   Semantic Scholar client (adapted from ref-checker)
    md/
      render.py              assemble paper.md from sections + bib
      cleanup.py             LLM per-section artifact cleanup
    llm/
      argo.py                OpenAI-compatible Argo client wrapper + retries
    distill/
      __init__.py
      queries.py             load + validate distillation query definitions
      scope.py               build LLM input for abstract/narrative/full scopes
      run.py                 run one query: prompt, LLM call, post-process, cache
  tests/
    fixtures/
      README.md              fixture licensing and criteria
      klasky-5.pdf           CC-BY Frontiers journal article (128 KB)
      zfp-spectral-report.pdf  Public-domain DOE OSTI tech report (7.3 MB)
      dorier-mofka.pdf       CC-BY Frontiers journal article (1.6 MB)
    test_repair.py
    test_sections.py
    test_sidecar_provenance.py
    test_classify.py
    test_conflicts.py
    test_bib_stub_offline.py
    test_config_validate.py
    test_distill_offline.py
    test_e2e_bib.py          network — bib resolution against live APIs
    test_e2e_distill.py      network — bib + distillation (Mofka fixture)
```

---

## Key dependencies and why

| Package | Role | Source |
|---|---|---|
| `pypdf` + `pdfplumber` | PDF text extraction | annual-report, ref-checker |
| `openai` | Argo LLM client (OpenAI-compatible) | annual-report |
| `tenacity` | LLM retry logic | annual-report |
| `pyyaml` | YAML read/write | annual-report |
| `typer` | CLI framework | annual-report |
| `rich` | Terminal output | annual-report |
| `requests` | HTTP for API clients | ref-checker |
| `bibtexparser` | Parse .bib files | annual-report |
| `tiktoken` | Token counting for section cleanup cap | annual-report |
| `python-dotenv` | Load `.env` | annual-report |

No database dependency (no lancedb, no sqlite, no redis). No ML model
dependency (no torch, no sentence-transformers). Designed to install in
seconds via `pipx` on any machine with Python 3.11+.

---

## Code provenance

| Module | Adapted from |
|---|---|
| `puba/io.py` | annual-report `annual_report/io.py` |
| `puba/config.py` | annual-report `annual_report/config.py` |
| `puba/sidecar.py` | annual-report `annual_report/sidecar.py` + `extract_publication_stub_one.py` |
| `puba/pdf/repair.py` | ref-checker text repair module |
| `puba/bib/sources/openalex.py` | annual-report `api/openalex.py` |
| `puba/bib/sources/crossref.py` | ref-checker CrossRef client |
| `puba/bib/sources/dblp.py` | ref-checker DBLP client |
| `puba/bib/sources/arxiv.py` | annual-report `api/arxiv.py` |
| `puba/bib/sources/osti.py` | annual-report `api/osti.py` |
| `puba/bib/sources/bibtex.py` | annual-report `api/bibtex.py` |
| `puba/bib/sources/semanticscholar.py` | ref-checker Semantic Scholar client |
| `puba/bib/stub.py` | annual-report `extract_publication_stub_one.py` (restructured) |
| `puba/distill/run.py` | annual-report `distill_publication_one.py` (generalized) |

All adapted modules carry the BSD-3-Clause header from the source repo.
License headers from the source repo are preserved.

---

## Open / deferred design questions

These were raised but not resolved in v1:

1. **arXiv preprint + published version:** when a paper has both an arXiv ID
   and a real DOI (e.g., published at NeurIPS but also on arXiv), the category
   resolves to "conference paper" (by venue). The arXiv ID is kept as a
   secondary identifier. An alternative design would expose both the preprint
   and published record as separate entries — deferred because puba is scoped to
   a single paper.

2. **`narrative_strip_sections` deep-merge:** project-local overrides of
   `distill.narrative_strip_sections` *replace* the packaged list (Python dict
   merge semantics) rather than appending. The user must copy the full packaged
   list and extend it. An append-mode merge (`_extend:` key or similar) would
   be more ergonomic; deferred because YAML deep-merge-by-append requires a
   custom merge strategy.

3. **`puba run` including distillation:** decided against in v1 (distillation
   is too expensive and optional), but revisit when typical usage patterns
   become clearer.

4. **Caching HTTP responses:** currently no cross-run HTTP cache; every `puba
   bib` invocation hits the APIs. At single-paper volume this is fine. If batch
   use (e.g., 50 papers from the same corpus) becomes common, a
   `requests-cache` layer or a puba-managed on-disk response cache would reduce
   API load and speed up re-runs.

5. **Semantic Scholar authentication:** the `SEMANTICSCHOLAR_API_KEY` env var
   is respected but not required. Unauthenticated requests are rate-limited
   aggressively (~1/sec). Since Semantic Scholar is a last-resort fallback, this
   is acceptable; but if it turns out to be consistently useful for a particular
   paper type, getting an API key improves reliability significantly.

6. **model name in prompt_version cache key for bib/md:** currently the bib and
   md cache keys do not include the model name. A user who changes
   `models.bib_extract` from GPT-5.4 to Claude Opus 4.7 without bumping
   `prompt_versions.bib_extract` will get cached output produced by the old
   model. This is a known limitation; documented in `docs/configuration.md`.

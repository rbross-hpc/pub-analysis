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
  paper.sections.json   detected section spans
  .state.json           per-stage cache key (sha256, version)
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

### `needs_review` triggers and exit-code policy

`needs_review: true` is set when any of the following hold after the full
resolution pipeline:

- Two or more good-quality tier-1 sources (sim ≥ `min_title_similarity`, or
  DOI-confirmed at sim 1.0) disagree beyond the configured conflict thresholds.
- Any of `title`, `authors`, `year` is still missing after the fallback chain.
- LLM bootstrap failed AND no DOI AND no arXiv ID was extracted from the PDF.

`_review_reasons` is a top-level list in `bib.yaml` of plain-English strings
recording which triggers fired (e.g. `"title missing"`,
`"sources disagreed: doi, year"`). Omitted when `needs_review: false`.

**Exit codes:** `puba bib` exits 3 when `needs_review: true`. `puba md` exits
3 when `bib.yaml` is missing or has `needs_review: true`, forcing a clean bib
before rendering proceeds. `puba show bib` and `puba show info` always exit 0
regardless of review state (read-only commands). `puba show md` and
`puba show sections` inherit the `puba md` gate when they would auto-render.

**`puba run` removed.** The `run` command (bib → md sequential orchestrator)
was removed to force an explicit human-review step between bib resolution and
markdown rendering. With `puba run` present, users could autopilot past
`needs_review` states. The gate is now enforced directly in `puba md` via
`_require_resolved_bib()` in `cli.py`, which checks (a) `bib.yaml` exists and
(b) `needs_review` is false, exiting 3 on either failure. The gate also applies
to the `show md` and `show sections` auto-render paths (via `_ensure_md`).

**Conflict detection is gated by source quality.** An earlier implementation
ran `detect_conflicts()` on all tier-1 results regardless of similarity score,
which produced spurious `needs_review` flags when a low-sim hit from one
source contradicted a high-confidence hit from another. The current
implementation passes only sources with sim ≥ `min_title_similarity` (or a
DOI-confirmed match) to `detect_conflicts()`. A low-quality hit that disagrees
with a strong hit is not a conflict.

### OSTI author format and surname extraction

Two related fixes to the OSTI source path:

- **OSTI returns authors in two formats** depending on the record: `list[dict]`
  with `name`/`first_name`/`last_name` keys, or `list[str]` formatted as
  `"Last, First [Affiliation] (ORCID:...)"`. `_summarize()` in
  `puba/bib/sources/osti.py` handles both; string-format entries are stripped
  of the affiliation suffix (everything from `[` onward). The string format was
  discovered when adding `tests/fixtures/wan-e3smv2-clouds.pdf` (OSTI 2587778)
  — the record returned a list of strings and `authors` came back empty.

- **`first_author_surname()`** now detects the name format before extracting:
  comma-containing names are `"Last, First"` format (surname is `parts[0]`
  after splitting on the comma); no-comma names are `"First Last"` format
  (surname is `parts[-1]`). The previous implementation always used `parts[0]`
  after replacing commas with spaces, which silently extracted the *first name*
  from `"First Last"` strings. The consequence: every paper where OSTI returned
  `"Wang, Dali"` and OpenAlex returned `"Dali Wang"` for the same person was
  flagged as an author conflict, setting `needs_review: true`.

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

### LLM bootstrap: naming and scope

The extractor entry point in `puba/bib/sources/llm.py` is
`extract_from_initial_pages(initial_pages_text)`. The function name and
parameter name reflect that the extractor receives the joined text of the
first 3 pages (capped at 3000 chars), not just page 1. The function was
previously named `extract_from_page1` with parameter `page1_text`, which was
misleading because `stub.py` already passed `_first_pages_text(pdf_path, n=3)`.

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

### BibTeX parse error surfacing

`--bibtex` is a fallback source for users who have an existing `.bib` file for
the paper. The earlier implementation silently swallowed all parse failures
(`load_bib_file` caught every exception and returned `[]`), meaning a missing
file, a directory path, or a completely malformed `.bib` produced a silent
`bibtex: no_match` log entry indistinguishable from a genuine "file parsed but
paper not found" result.

Current behavior: `load_bib_file()` raises `BibtexParseError` (a `RuntimeError`
subclass) for every user-visible error: file not found, path is a directory,
file unreadable, file is empty or whitespace-only, file is non-empty but
produces zero parseable entries. The Typer `--bibtex` option carries
`exists=True, file_okay=True, dir_okay=False, readable=True, resolve_path=True`
so common mistakes (nonexistent path, directory) are rejected at the CLI layer
before `resolve()` is invoked. `stub.py` catches `BibtexParseError` from the
bibtex lookup and re-raises as `RuntimeError`, which the CLI's existing
exit-2 handler surfaces.

**Design principle:** if you pass `--bibtex`, you are asserting "this file
contains useful entries for this paper." An empty or unparseable file is a
contract violation, not a soft miss.

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

### Backend: MinerU pipeline

`puba md` runs MinerU (`pipeline` backend, formula recognition disabled)
as a subprocess. MinerU is a layout-aware ML extractor that handles two-column
layouts, column ordering, and running headers correctly.

Invocation (hardcoded):

```
mineru -p <pdf> -o <tmp> -b pipeline -f false
```

MinerU writes `<stem>/auto/<stem>.md` and `<stem>_content_list.json`.
`render()` reads both, injects page markers, assembles `paper.md`, and derives
`paper.sections.json` from the headings in the assembled text.

### Persisted intermediates

After each successful MinerU run, `run_mineru()` copies the following files
from MinerU's temp directory into `<pdf>.puba/mineru/` before the temp dir is
deleted:

- `<stem>.md` — raw MinerU markdown before puba's page-marker injection and
  cover-strip. Useful for diagnosing injection anchoring failures.
- `<stem>_content_list.json` — flat ordered block list with `page_idx` values.
  The primary input to `_inject_page_markers()`.
- `<stem>_content_list_v2.json` — page-grouped structured block list. Useful
  for cross-checking MinerU's page boundaries.
- `<stem>_middle.json` — MinerU's internal intermediate representation.
- `<stem>_layout.pdf` — annotated PDF showing MinerU's layout detection bounding
  boxes. **TODO: remove from the always-persisted set once the page-marker
  injection logic is verified correct; it is the largest file and primarily a
  debugging aid.**

These files are removed by `puba clean --what md`. They are regenerated on
every cache-miss `puba md` run and left untouched on cache hits.

### Page markers

`content_list.json` is a flat ordered list of blocks, each with a `page_idx`
(0-based int). `_inject_page_markers()` runs on the **post-cover-strip**
markdown (cover-strip runs first in the pipeline). It groups blocks by
`page_idx` (preserving first-seen order), then for each page tries each
non-empty-text block in order as an anchor: searches forward in the markdown
for that block's text and inserts `<!-- page N -->` at the start of the line
containing it. The first successfully-anchored block is used.

Pages with no surviving anchor are silently skipped — no marker is emitted.
This is the correct behavior when a page's entire content was removed by
cover-strip (see "Cover-page heading filter" below). The marker sequence in
`paper.md` may therefore be non-contiguous; gaps are meaningful.

Pages with no blocks of text >= 8 chars (pure-figure pages or pages with
only short labels) emit no marker — they are silently skipped.

When a block's text exists somewhere in the markdown but is unreachable from
cursor (typically because cursor overshot it via a late-column anchor from the
prior page), a **fallback marker** is emitted at the current cursor position.
`_inject_page_markers()` returns `(text, fallback_pages)` where `fallback_pages`
is a list of `page_idx` values that received fallback markers. `render()` emits
a stderr warning when `len(fallback_pages) >= 2`. See
`docs/markdown-rendering.md §"Cursor overshoot and fallback markers"`.

`N = page_idx + 1` — physical PDF page number, 1-indexed from the first page
in the file (including cover/front-matter pages). See
`docs/markdown-rendering.md` for user-facing semantics and the inherent
artifact from MinerU's block model (paragraph-spanning page breaks cause
markers to lag visible page tops by 1–2 sentences).

**Empty-leading-block bug (fixed in mineru-3):** earlier versions used the
first block unconditionally as the anchor. When MinerU produced an empty-text
first block for a page (common for figure-dominated or formula-heavy pages),
the marker was emitted at the current cursor without advancing, causing
consecutive empty-leading pages to stack their markers at the same position
with no body text between them (observed on thornado pages 3–5). Fixed by
trying each non-empty block per page in order.

**Cover-strip / missing-marker bug (fixed in mineru-4):** earlier versions
ran marker injection before cover-strip. Cover-strip then removed the stripped
prefix including any page markers that fell within it (e.g. thornado pages 1–2,
klasky page 1). Fixed by inverting the order: cover-strip runs on the raw
MinerU markdown first, then marker injection runs on the post-strip text so
no markers can be lost.

**Refined fallback logic (mineru-5):** mineru-4's "try each block" approach
skipped pages entirely when all blocks were consumed or repeated. This
inadvertently dropped legitimate pages (e.g. thornado pages 22–49) when
short repeated fragments like page-number footers were the only long-enough
blocks remaining. mineru-5 distinguishes "block text absent from markdown"
(cover-stripped — skip) from "block text present but cursor-overshot"
(fallback marker at cursor, with warning if >= 2 such pages per PDF).

### Section detection

`_parse_sections()` runs `re.findall(r'^(#{1,6}) (.+)$', assembled_md, MULTILINE)`.
MinerU emits headings as `#`-prefixed lines; no config-driven heading-word
lists or numbered-section patterns are needed. Offsets are into the final
assembled `paper.md` text, so `distill/scope.py`'s `md_text[start:end]` slices
remain valid.

### `short_name` derivation

Unchanged: `derive_short_name()` in `puba/pdf/sections.py` slugifies each
heading title — strip leading numeric prefix, lowercase, split on
non-alphanumeric runs, keep first 4 words, join with `_`, prefix `s_` if
starts with digit. Collision disambiguation appends `_2`, `_3`, … in document
order. `load_sections_json` back-fills `short_name` for any legacy entry
missing the field.

### `puba show sections` command

Unchanged interface: auto-runs `puba md` if not cached, prints a Rich table
of `short_name`, `level`, `title`. `--json` emits raw `paper.sections.json`.

### Cover-page heading filter

Many academic PDFs begin with a cover page emitted by the repository or
publisher (LBL eScholarship, Frontiers, AAS journals) before the real paper
content. MinerU faithfully extracts these as level-1 headings (`# Lawrence
Berkeley National Laboratory LBL Publications`, etc.) with level-2 children
(`## OPEN ACCESS`, `## CITATION`, `## COPYRIGHT`, `## DOI`, …). Without
filtering these become spurious entries in `paper.sections.json` and `puba
show sections`.

`_strip_cover_headings(md_text, bib_title)` in `puba/md/render.py` removes
them when `bib.title` is known. It operates on the **raw MinerU markdown**
(before page-marker injection) and is the first transformation in the pipeline:

1. Normalize both `bib_title` and each level-1 heading text: lowercase,
   collapse non-alphanumeric runs to a single space, strip.
2. Build a prefix from the first `min(8, N)` normalized words of `bib_title`.
3. Scan level-1 (`#`) headings, stopping at whichever comes first: the 20th
   level-1 heading, or the 6000-character mark in the raw markdown.
4. If a heading's normalized text starts with that prefix, drop everything
   from the start of the markdown up to and including that heading line.
   The caller (`render()`) already prepends its own `# {bib.title}` line, so
   no duplication occurs.
5. If no match is found within the window, or if `bib_title` is absent or
   normalizes to fewer than 2 words, return the markdown unchanged (no-op).

Because cover-strip runs before marker injection, pages whose content is
entirely stripped produce no marker in the final `paper.md` (marker injection
finds no surviving anchor for those pages and silently skips them).

See `docs/markdown-rendering.md` for the full user-facing description.

Cache version bumped from `mineru-1` to `mineru-2` when this filter was
introduced. Bumped again to `mineru-4` when the pipeline order was corrected
(cover-strip before marker injection) to prevent markers from being discarded
as cover-strip collateral damage.

### Cache invalidation

`md.mineru_version` (default `"mineru-5"`) is written to `.state.json` for the
md stage, replacing the old `prompt_versions.md_cleanup` key. Bump it manually
after a MinerU upgrade or when the render output format changes. Old papers
cached under earlier versions will re-run automatically (version mismatch).

### First-run model download

MinerU downloads ~1.5–3 GB of model weights to `~/.cache/huggingface/` on
first use. GPU strongly recommended: ~2 min for a 50-page two-column paper on
NVIDIA GB10 (128 GB unified memory); CPU-only is ~10 min for the same paper
with formula recognition disabled (was ~18 min with formula recognition
enabled — the CPU timing in earlier notes was with `-f true`).

### Historical: the layered pipeline (removed)

The original `puba md` used a layered pipeline: pypdf-first/pdfplumber-fallback
extraction → text repair (de-hyphenation, ligature normalization, soft-hyphen
stripping, split-glyph fix, tabular numeral glyph repair) → config-driven
section detection (heading-word list + numbered-section regex) → per-section
LLM cleanup (`MD_CLEANUP_SYSTEM` prompt v2, `md-cleanup-2`).

Key pain points that motivated the switch:

- pdfplumber's `extract_text()` interleaves columns on two-column PDFs,
  producing garbled text and firing section detection on running headers.
- A position-band header/footer stripper was implemented and reverted: it
  removed body text on two-column layouts where "first line of page" is column
  body, not a header.
- The Thornado fixture (`endeve-thornado.pdf`, 52 pp, two-column, dense math)
  produced 64 spurious section spans from the heuristic detector.
- LLM per-section cleanup was expensive (~GPT-5.4 per section, sequentially)
  and required careful chunking for sections over 8k tokens.

MinerU on the same fixture: 56 real sections, running headers correctly
removed, columns correctly ordered, math preserved as LaTeX blocks. CPU timing
was ~18 min with formulas; ~10 min with `-f false`; ~2 min on GPU with
`-f false`. (An earlier note cited 15 sections; that reflected an older MinerU
version. Current pipeline backend produces 56 for this fixture.)

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

**`puba distill --list` and `puba show info` surface section status.** The Target
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

### `puba show distill` command

`puba show distill <pdf> NAME` prints the raw `output` text of a named
distillation. `--json` emits a full envelope including `_provenance` (always
included; provenance is small and useful for debugging). `--all --json` emits
every distillation in one envelope; plain `--all` without `--json` is rejected
with exit 2 (multi-record output is only meaningful as JSON).

**Plain output has no header** — consistent with `puba show md`'s
zero-decoration policy; the assumption is that callers want pipeable text.
`--json` envelope keys: `ok, command, pdf, analysis_dir, name, scope, section,
model, generated_at, chars, output, _provenance`.

**Failure model:** a corrupt `analyses/<name>.yaml` fails the whole `--all`
invocation (not a silent skip) so callers don't unknowingly process a partial
result set. Single-name reads cite the bad file in the error envelope.
When the requested name does not exist, the error message lists available names.

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

### Why version strings for bib/md but content hashing for distill

- bib stage: the effective "prompt" includes the source priority chain, the
  classification cascade, and the LLM title-extraction prompt. No single string
  to hash. A developer bumps `prompt_versions.bib_extract` when they change
  something meaningful.
- md stage: `md.mineru_version` is bumped manually when MinerU is upgraded or
  the rendering pipeline changes. Same operator-communication rationale as bib:
  the "prompt" is the MinerU binary and its configuration, not a string.
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
- **Distillation bundled with bib/md** — distillation is an explicit `puba
  distill` step; it is the most expensive LLM operation and many users
  will want bib+md but not distillation.

---

## Planned future work

- **Map-reduce distillation** — for `scope=full` on very long papers that
  exceed context windows. Section-by-section distillation with a stitching
  pass.
- **MinerU CPU performance** — formula recognition disabled (`-f false`) brings
  CPU time from ~18 min to ~10 min for a 50-page two-column paper. GPU
  (NVIDIA GB10) brings it to ~2 min. No further optimization planned; GPU is
  the recommended path.

---

## Repo layout

```
pub-analysis/
  README.md                  quickstart, CLI reference, output layout
  PLAN.md                    this file — design rationale and decisions log
  LICENSE                    BSD-3-Clause
  pyproject.toml             Python package; console script: puba
  environment.yml            conda env for development
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
                             distill, show bib/md/sections/info/distill,
                             config show/validate/init
    config.py                load + override resolution + show + validate;
                             config.yaml is inside the package at
                             puba/config.yaml and loaded via
                             importlib.resources.files("puba")/"config.yaml"
    io.py                    sha256, atomic writes (adapted from annual-report)
    state.py                 .state.json per-stage cache management
    sidecar.py               bib.yaml read/write + provenance merge
    _common_prompts.py       LLM prompt strings (BIB_EXTRACT_SYSTEM)
    pdf/
      mineru.py              subprocess wrapper for MinerU pipeline extraction
      sections.py            Section dataclass, short-name derivation, JSON I/O
    bib/
      stub.py                orchestrates PDF heuristics, LLM bootstrap, tier-1
                             parallel, fallback chain, provenance, category,
                             review_reasons computation
      classify.py            config-driven category classification cascade
      conflicts.py           tier-1 source conflict detection
      sources/
        _common.py           rate limits, DOI/arXiv extraction, similarity,
                             first_author_surname (comma-aware)
        openalex.py          OpenAlex client (adapted from annual-report)
        crossref.py          CrossRef client (adapted from ref-checker)
        dblp.py              DBLP client (adapted from ref-checker)
        arxiv.py             arXiv client (adapted from annual-report)
        osti.py              OSTI client; handles string and dict author formats
        bibtex.py            BibTeX file parser; raises BibtexParseError on
                             missing/empty/unparseable input
        llm.py               extract_from_initial_pages (first 3 pages, 3k chars)
        semanticscholar.py   Semantic Scholar client (adapted from ref-checker)
    md/
      render.py              MinerU extraction → page markers → section index → paper.md
    llm/
      argo.py                OpenAI-compatible Argo client wrapper + retries
    distill/
      __init__.py
      queries.py             load + validate distillation query definitions
      scope.py               build LLM input for abstract/narrative/full/section
      run.py                 run one query: prompt, LLM call, post-process, cache
  tests/
    fixtures/
      README.md              fixture licensing and criteria
      klasky-5.pdf           CC-BY Frontiers journal article, 4 pp (128 KB)
      zfp-spectral-report.pdf  Public-domain DOE OSTI tech report, 14 pp (7.3 MB)
      dorier-mofka.pdf       CC-BY Frontiers, 42 pp, has abstract for distill
      cruz-zombie-packets.pdf  ACM TOMACS, ANL-affiliated, 19 pp, DOI on page 1
      wan-e3smv2-clouds.pdf  CC-BY GMD journal, 26 pp; exercises OSTI
                             string-format author parsing (OSTI 2587778)
      endeve-thornado.pdf    CC-BY ApJS 2026, 52 pp, two-column, dense math;
                             primary MinerU benchmark fixture (OSTI 3367521)
    test_sections.py           derive_short_name, short_names, collision (detect_sections removed)
    test_sidecar_provenance.py
    test_classify.py
    test_bib_stub_offline.py   includes conflict, needs_review,
                               first_author_surname, and mocked-resolve tests
    test_bibtex_source.py      BibtexParseError on all failure modes
    test_osti_source.py        _summarize author format variants
    test_config_validate.py
    test_config_init.py        packaged config location and copy behavior
    test_distill_offline.py
    test_mineru_offline.py     run_mineru subprocess mock; render() page markers,
                               section offsets, short_names, cache hit
    test_cli_json.py           --json envelope shape on bib, md, run; exit 3
    test_cli_show.py           show bib/md/sections/info/distill (all modes)
    test_e2e_bib.py            network — bib resolution against live APIs
                               (5 fixture classes: Klasky, Zfp, Cruz, Wan, Thornado)
    test_e2e_distill.py        network — bib + distillation (Mofka fixture)
```

---

## Key dependencies and why

| Package | Role | Source |
|---|---|---|
| `pypdf` + `pdfplumber` | PDF text extraction for bib bootstrap (`_first_pages_text`) only | annual-report, ref-checker |
| `mineru[pipeline]>=3.4` | Layout-aware PDF extraction for `puba md` | MinerU project |
| `accelerate>=1.14` | Required by MinerU hybrid-engine for GPU `device_map` | HuggingFace |
| `opencv-python-headless>=4.13` | Required by MinerU (headless variant avoids libGL dependency) | OpenCV |
| `openai` | Argo LLM client (OpenAI-compatible) | annual-report |
| `tenacity` | LLM retry logic | annual-report |
| `pyyaml` | YAML read/write | annual-report |
| `typer` | CLI framework | annual-report |
| `rich` | Terminal output | annual-report |
| `requests` | HTTP for API clients | ref-checker |
| `bibtexparser` | Parse .bib files | annual-report |
| `tiktoken` | Token counting for distill budget check | annual-report |
| `python-dotenv` | Load `.env` | annual-report |

MinerU brings a heavy ML dependency tree (torch, transformers, onnxruntime,
PaddleOCR, ~1.5–3 GB model weights downloaded on first `puba md` run).
pypdf and pdfplumber are retained solely for bib LLM bootstrap
(`_first_pages_text` in `puba/bib/stub.py`).

---

## Code provenance

| Module | Adapted from |
|---|---|
| `puba/io.py` | annual-report `annual_report/io.py` |
| `puba/config.py` | annual-report `annual_report/config.py` |
| `puba/sidecar.py` | annual-report `annual_report/sidecar.py` + `extract_publication_stub_one.py` |
| `puba/pdf/mineru.py` | new — MinerU subprocess wrapper |
| `puba/md/render.py` | rewritten for MinerU (was annual-report-derived layered pipeline) |
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

3. **Caching HTTP responses:** currently no cross-run HTTP cache; every `puba
   bib` invocation hits the APIs. At single-paper volume this is fine. If batch
   use (e.g., 50 papers from the same corpus) becomes common, a
   `requests-cache` layer or a puba-managed on-disk response cache would reduce
   API load and speed up re-runs.

5. **Semantic Scholar authentication:** the `SEMANTICSCHOLAR_API_KEY` env var
   is respected but not required. Unauthenticated requests are rate-limited
   aggressively (~1/sec). Since Semantic Scholar is a last-resort fallback, this
   is acceptable; but if it turns out to be consistently useful for a particular
   paper type, getting an API key improves reliability significantly.

6. **model name in prompt_version cache key for bib:** the bib cache key does
   not include the model name. A user who changes `models.bib_extract` from
   GPT-5.4 to Claude Opus 4.7 without bumping `prompt_versions.bib_extract`
   will get cached output produced by the old model. This is a known limitation;
   documented in `docs/configuration.md`. (The md stage no longer uses an LLM,
   so this issue only applies to bib.)

7. **MinerU backend integration design:** resolved. MinerU `pipeline` is
   the sole `puba md` backend. Formula recognition is disabled (`-f false`).
   Headings are parsed from `#`-prefixed lines in MinerU's markdown output.
   LLM cleanup is not needed. MinerU is a required dependency (not opt-in).
   `md.mineru_version` is the cache invalidation key. See "Markdown rendering"
   section above.

8. **Running header/footer removal:** resolved by switching to MinerU.
   MinerU's layout-aware extraction identifies header/footer bounding boxes
   and excludes them from the body text — no position-band heuristic needed.

9. **`puba/bib/stub.py` extraction order inconsistency:** partially resolved.
   The production md extraction pipeline no longer uses pypdf or pdfplumber.
   `_first_pages_text()` (bib bootstrap only) retains its pdfplumber-first/
   pypdf-fallback ordering; it is the only place either library is called.
   The "inconsistency" between bib bootstrap and md extraction no longer exists
   as a latent bug source because there is no longer a production pypdf/
   pdfplumber path in md.

# puba markdown rendering

`puba md` extracts and renders a clean markdown version of a PDF paper,
producing `paper.md`, `paper.sections.json`, and a set of MinerU intermediate
files in `<pdf>.puba/`. This document describes the rendering pipeline, the
structure of its outputs, and the known quirks you should understand when
using those outputs.

## Prerequisite: resolved bib

`puba md` requires `bib.yaml` to be present and not flagged for review before
it will invoke MinerU. Run `puba bib <pdf>` first and resolve any
`needs_review=true` issues; otherwise `puba md` exits 3 without rendering.
The same gate applies to `puba show md` and `puba show sections` when they
would auto-render.

Figures are extracted separately by `puba figures` — see [figures.md](figures.md).

---

## Pipeline

For each PDF, `puba md` runs the following steps in order:

1. **MinerU extraction** — MinerU (`pipeline` backend, formula recognition
   disabled) is invoked as a subprocess. It produces a raw markdown file and a
   flat JSON block list (`content_list.json`), each block tagged with a
   `page_idx` (0-based physical page index).

2. **Cover-page heading filter** (`_strip_cover_headings`) — Many academic
   PDFs begin with a repository or publisher cover page (LBL eScholarship,
   Frontiers, AAS journals, OSTI deposit pages) that MinerU faithfully
   extracts as level-1 headings with level-2 children (OPEN ACCESS, CITATION,
   COPYRIGHT, DOI, …). This step removes everything up to and including the
   first level-1 heading whose normalized text matches the paper's
   `bib.yaml` title. The caller (`render()`) then prepends its own
   `# {bib_title}` line, so no duplication occurs.

   The search is bounded to the first 6000 characters of the raw markdown
   and the first 20 level-1 headings — whichever comes first. If no matching
   heading is found within the window, the raw markdown is returned unchanged.

3. **Page-marker injection** (`_inject_page_markers`) — Inserts
   `<!-- page N -->` comments into the post-strip markdown to indicate page
   boundaries. See "Page numbering" below for full semantics.

4. **Assembly** — Prepends YAML frontmatter and the puba-generated title /
   author / venue lines, then appends the marker-injected body to produce the
   final `paper.md`.

5. **Section detection** (`_parse_sections`) — Scans the assembled `paper.md`
   for `#`-prefixed heading lines, records each as a section with a
   `short_name`, `level`, and `start_offset` / `end_offset` into `paper.md`.
   Results are written to `paper.sections.json`.

---

## Page numbering

### How markers are assigned

`<!-- page N -->` markers use `N = page_idx + 1`, where `page_idx` is
MinerU's 0-based physical page index. `N` is therefore the **physical PDF
page number**, counted from the first page in the file — including any cover
sheets, repository overlays, or blank pages that precede the article body.
It is not necessarily the printed page number at the bottom of the page.

**Example:** a 52-page APS journal paper preceded by a 1-page eScholarship
overlay has `page_idx` values 0–52 (53 pages total). `<!-- page 1 -->` is the
overlay; `<!-- page 2 -->` is printed page 1 of the journal article.

### Where markers are placed

Markers are placed at the **paragraph (block) boundary nearest the page
break**. MinerU groups each paragraph into a single block belonging to the
page where the paragraph begins. When a paragraph spans a page break, its
full text appears under the marker for the starting page, and the next page's
marker is placed at the next paragraph boundary — typically a sentence or two
after the visible page top.

**Implication:** use page markers for approximate navigation and citation.
They will often lag the visible top of a page by one paragraph when the first
paragraph on that page is a continuation from the previous page.

For exact block-level page attribution, consult
`<pdf>.puba/mineru/<stem>_content_list.json`.

### Gaps in the marker sequence

The marker sequence in `paper.md` may be non-contiguous. **Gaps are
meaningful:**

- A page whose entire content was removed by the cover-page heading filter
  produces no marker. For example, if the eScholarship overlay covers pages
  1–2 of the physical PDF, `paper.md` will contain neither `<!-- page 1 -->`
  nor `<!-- page 2 -->`, and the first marker will be `<!-- page 3 -->`.

- A page consisting entirely of figures, tables, or other elements that
  MinerU does not extract as text also produces no marker (MinerU emits no
  text blocks for that page, so there is no anchor). This is uncommon for
  journal articles but occurs in figure-heavy technical reports.

Do not assume that `<!-- page N -->` exists for every N from 1 to the page
count of the PDF.

### Pure-figure / no-text pages

When a page has no text blocks with at least 8 characters in
`content_list.json` (e.g. a full-page figure or a page with only short
equation labels), no marker is emitted for that page. The marker sequence
skips that page number.

### Cursor overshoot and fallback markers

When MinerU's reading order places a block's text later in the markdown than
expected — most commonly when a figure caption or late-column block is listed
first for a page in `content_list.json` but renders after the next page's
text in `<stem>.md` — the anchor cursor can jump ahead of content that
logically belongs to the next page. Subsequent pages whose anchor blocks were
already consumed by the cursor receive a **fallback marker**: the marker is
emitted at the current cursor position with the correct page number, but
without precise position resolution.

A fallback marker is emitted only when at least one of the page's blocks
exists somewhere in the markdown (search from position 0) — confirming the
page has surviving content. If no block text is found anywhere in the markdown,
the page is silently skipped (no marker).

**Warning:** when more than one fallback marker is emitted for a PDF, `puba md`
prints a warning to stderr listing the affected page numbers and noting that
marker positions are approximate for those pages.

Multiple consecutive fallback markers stack at the same character position in
the markdown with no body text between them. This is rare in practice. If you
observe it for a given paper, inspect
`<pdf>.puba/mineru/<stem>_content_list.json` to understand which blocks
MinerU is producing for those pages.

---

## Cover-page heading filter

### What it does

Removes publisher and repository cover-page content from the start of MinerU's
raw markdown output. The filter locates the first level-1 (`#`) heading whose
normalized text starts with the first min(8, N) words of `bib.yaml`'s `title`
field, then strips everything from the start of the markdown up to and
including that heading line. The `render()` function then prepends its own
`# {bib_title}` line from `bib.yaml`.

### Search window

The filter searches within the first 6000 characters of the raw markdown and
the first 20 level-1 headings, stopping at whichever bound is hit first. Cover
content is always at the start of the file, so this window is generous for all
known fixture PDFs (the furthest title heading seen is at byte ~1200).

### When it fires

The filter fires when all of the following hold:

- `bib.yaml` has a `title` field with at least 2 words.
- A level-1 heading matching the title prefix is found within the search
  window.

### When it does not fire

- `bib.yaml` title is absent, empty, or a single word.
- No matching heading within the search window (the paper has no
  publisher/repository cover, or MinerU did not emit a level-1 heading for
  the title).
- The title appears too deep in the document (beyond 6000 chars or 20 H1s)
  — treated as a no-op to avoid stripping real body content.

### Effect on page markers

Because cover-strip runs on the raw MinerU markdown before page-marker
injection, markers for stripped pages are simply never emitted. This is by
design — see "Gaps in the marker sequence" above.

---

## Section detection

`_parse_sections()` scans the assembled `paper.md` (after frontmatter, cover
strip, and marker injection) for lines matching `^#{1,6} .+$`. Each match
becomes a section entry in `paper.sections.json`:

| Field | Description |
|---|---|
| `short_name` | Slug derived from the heading title (lowercase, first 4 words, collision-disambiguated with `_2`, `_3`, …). Numeric-starting slugs are prefixed `s_`. |
| `title` | Heading text as it appears in `paper.md`. |
| `level` | Heading depth (1 = `#`, 2 = `##`, etc.). |
| `start_offset` | Byte offset of the heading line in `paper.md`. |
| `end_offset` | Byte offset of the next heading line (or end of file). |

Offsets are into the final `paper.md` so `md_text[start:end]` slices
correctly for `puba distill --scope section`.

No heuristic heading-word lists or numbered-section patterns are used; section
detection is entirely driven by MinerU's `#`-prefixed output lines.

---

## MinerU intermediates (`mineru/` subdir)

After each successful `puba md` run, the following MinerU intermediate files
are copied into `<pdf>.puba/mineru/` for debugging:

| File | Contents |
|---|---|
| `<stem>.md` | Raw MinerU markdown before cover-strip or marker injection. |
| `<stem>_content_list.json` | Flat ordered block list; each block has `page_idx`, `text`, `type`, and `bbox`. Primary input to `_inject_page_markers`. |
| `<stem>_content_list_v2.json` | Page-grouped structured block list (1 entry per page). |
| `<stem>_middle.json` | MinerU's internal intermediate representation. |
| `<stem>_layout.pdf` | Annotated PDF showing MinerU's layout detection bounding boxes. Useful for visually verifying page assignments. **Planned for removal once page-marker logic is stable.** |

These files are removed by `puba clean --what md` and regenerated on every
cache-miss `puba md` run. They are left untouched on cache hits.

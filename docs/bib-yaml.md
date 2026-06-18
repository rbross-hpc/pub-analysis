# bib.yaml — schema, provenance, and resolution

`bib.yaml` is the verified bibliographic record for a single paper. It is
written by `puba bib` and read by `puba md`, `puba show info`, and future tools
under `analyses/`.

See also: [configuration.md](configuration.md) for all config knobs that affect
resolution behaviour.

---

## Resolution flow

```
┌─────────────────────────────────────────────────────────┐
│ 1. PDF scan (pages 1–3)                                 │
│    regex sweep for DOI (10.XXXX/...) and arXiv ID      │
│    cheap; no API calls                                  │
└──────────────────────────┬──────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────┐
│ 2. Title bootstrap                                      │
│    if title not yet known:                              │
│      LLM (page-1 text → title + any other fields)      │
│      --no-llm: PDF cover-page heuristic instead         │
└──────────────────────────┬──────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────┐
│ 3. Tier-1 parallel (armed with DOI / arXiv ID / title) │
│    ┌─────────────┐  ┌──────────┐  ┌──────────────────┐ │
│    │  OpenAlex   │  │ CrossRef │  │      OSTI        │ │
│    └──────┬──────┘  └────┬─────┘  └────────┬─────────┘ │
│           └──────────────┴─────────────────┘           │
│    results merged per source priority                   │
│    conflicts → needs_review: true                       │
└──────────────────────────┬──────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────┐
│ 4. arXiv by ID (always, if arXiv ID is known)           │
└──────────────────────────┬──────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────┐
│ 5. Fallback chain (only if tier-1 left core gaps)       │
│    DBLP → arXiv title search → BibTeX → Semantic Scholar│
│    each stops per field as soon as a value is found     │
└──────────────────────────┬──────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────┐
│ 6. Category classification (config-driven cascade)      │
│    CrossRef type → venue patterns → acronyms → fallback │
└─────────────────────────────────────────────────────────┘
```

The title bootstrap (step 2) runs **before** tier-1 so that title-based
searches are armed even for papers with cover-page-only PDFs (e.g. OSTI
technical reports with no DOI on the page). This mirrors the pattern in
annual-report's publication stub generator.

---

## Source priority

```
human > osti > openalex > crossref > dblp > bibtex > arxiv > pdf > llm > semanticscholar > derived > unknown
```

A higher-priority source always wins per field. Tier-1 sources (osti,
openalex, crossref) are always queried in parallel; fallback sources are only
tried when tier-1 left gaps.

---

## Editing `bib.yaml` by hand

All non-underscore fields are free to edit. To make an edit **sticky** so
future `puba bib` runs do not overwrite it, set the corresponding provenance
entry:

```yaml
title: "My corrected title"
_provenance:
  title:
    source: human
    lookup_key: null
    at: "2026-06-17T20:00:00+00:00"
    note: "corrected from OpenAlex which had a truncated title"
```

Any field whose `_provenance` entry has `source: human` will never be
overwritten, regardless of what API sources return.

**Deleting a field** (setting it to `null` or removing the key) causes the
next `puba bib` run to attempt to re-derive it from sources — *unless* its
provenance is also marked `human`.

**Deleting `_provenance` entirely** is safe; the next run rebuilds it
from scratch treating nothing as human-pinned.

---

## Field reference

### Core identity fields

| Field | Type | Notes |
|---|---|---|
| `title` | string | Full title as returned by the winning source |
| `authors` | list of strings | Full names; first-author surname used for conflict detection |
| `year` | integer | Publication year |
| `publication_date` | string | ISO 8601 (e.g. `2026-03-15` or `2026-03`); null if only year known |
| `venue` | string | Journal name, conference name, or null for tech reports |
| `venue_short` | string | Optional short form (not auto-derived) |
| `category` | string | See [category enum](#category-enum) |

### External identifiers

| Field | Type | Notes |
|---|---|---|
| `doi` | string | Bare DOI without URL prefix (e.g. `10.1145/3731599.1234`). The auto-minted arXiv DOI (`10.48550/arxiv.*`) is **not** stored here; use `arxiv_id` instead. |
| `arxiv_id` | string | Bare arXiv ID without version (e.g. `2301.00234`) |
| `osti_id` | string | OSTI record ID |
| `isbn` | string | ISBN for book chapters; from CrossRef |
| `issn` | string | ISSN; from CrossRef |

### Locators

| Field | Type | Notes |
|---|---|---|
| `url` | string | Canonical landing page URL |
| `pages` | dict | `{first, last, total}` — parsed from PDF where available |

### Content

| Field | Type | Notes |
|---|---|---|
| `abstract` | string | Block scalar. From OpenAlex (reconstructed from inverted index), CrossRef, or arXiv. |
| `keywords` | list of strings | From OpenAlex |
| `language` | string | ISO 639-1 (e.g. `en`); from OpenAlex |

### Rights

| Field | Type | Notes |
|---|---|---|
| `license` | string | SPDX identifier (e.g. `CC-BY-4.0`) if OpenAlex knows it |
| `oa_status` | string | Open-access status from OpenAlex (`gold`, `green`, `bronze`, `closed`) |

### Convenience

| Field | Type | Notes |
|---|---|---|
| `bibtex_key` | string | From BibTeX input or derived as `{firstauthorsurname}{year}{firstsignificantword}` |

### Workflow / quality

| Field | Type | Notes |
|---|---|---|
| `references_count` | integer | Number of references parsed from the PDF; informational only; future hand-off to ref-checker |
| `needs_review` | boolean | `true` when tier-1 sources disagreed beyond conflict thresholds. `puba show info` and `puba md` warn loudly. |
| `notes` | string | Free-form; human-written; never overwritten by puba |

---

## Category enum

| Value | When assigned |
|---|---|
| `arxiv preprint` | arXiv ID is set **and** no real DOI (or DOI is the auto-minted `10.48550/arxiv.*`) |
| `preprint` | DOI prefix matches a known preprint server (bioRxiv, medRxiv, ChemRxiv, SSRN, OSF, …) or venue host matches |
| `journal article` | CrossRef/OpenAlex type is `journal-article`, or venue is present with no conference/workshop signal |
| `conference paper` | CrossRef type is `proceedings-article`, or venue matches a conference acronym or venue pattern |
| `workshop paper` | Venue string matches a workshop pattern (checked before conference patterns) |
| `book chapter` | CrossRef type is `book-chapter`, `inbook`, or similar |
| `book` | CrossRef type is `book`, `monograph`, or `edited-book` |
| `technical report` | CrossRef type is `report` / `report-component`, or venue matches a lab-report pattern (ANL-, ORNL/TM-, LLNL-TR-, SAND…, NASA TM, etc.) |
| `thesis` | CrossRef type is `dissertation`, or venue string matches PhD/MS thesis pattern |
| `other` | No rule matched |

### Category classification cascade

The classifier tries rules in this fixed order — first match wins:

1. arXiv preprint — arXiv ID + no real DOI
2. Preprint server — DOI prefix or venue host
3. Book-related — CrossRef type
4. Technical report — CrossRef type
5. Thesis — CrossRef type
6. Thesis — venue pattern
7. Technical report — venue pattern
8. Workshop — venue pattern
9. Conference — venue acronym (whole-word)
10. Conference — venue pattern
11. Journal article — venue present, no other signal matched
12. Other — fallthrough

All pattern lists and acronym lists are in `config.yaml` under
`bib.classification`. See [configuration.md](configuration.md#classification-lists)
for the full set of knobs.

---

## `_provenance` entries

Each field in `_provenance` records how that field's value was determined:

```yaml
_provenance:
  title:
    source: openalex          # source name (see priority list above)
    lookup_key: "10.1145/..."  # DOI or title used as the query key
    at: "2026-06-17T20:00:00+00:00"  # ISO timestamp of the lookup
    similarity: 0.97          # title SequenceMatcher ratio (when title-searched)
    note: null                # human note or error message
```

`similarity` is only present for title-search hits; DOI-confirmed hits omit it
(the DOI is proof enough).

---

## `_conflicts` entries

Present only when `needs_review: true`. Lists the values returned by each
tier-1 source for each conflicting field:

```yaml
_conflicts:
  title:
    - source: openalex
      value: "Attention Is All You Need"
    - source: crossref
      value: "Attention is all you need"
  year:
    - source: openalex
      value: 2017
    - source: crossref
      value: 2016
```

Conflict thresholds are configurable; see
[configuration.md](configuration.md#conflict-detection-thresholds).

---

## `_lookup_log` entries

Records what each source was queried with and what it returned, on the most
recent `puba bib` run:

```yaml
_lookup_log:
  openalex:
    status: hit
    key: "10.1145/..."
    sim: 1.0
    queried_at: "2026-06-17T20:00:00+00:00"
  dblp:
    status: not_attempted
    reason: "all tier-1 sources confirmed"
  llm_bootstrap:
    status: not_attempted
    reason: "title already known"
```

| Status | Meaning |
|---|---|
| `hit` | Source returned a result that met the similarity threshold |
| `low_sim` | Source returned a result but title similarity was below threshold; value not used |
| `no_match` | Source returned no results |
| `not_attempted` | Source was deliberately skipped (reason given) |
| `failed` | Source raised an error (LLM timeout, API error, etc.) |

---

## `_meta` entries

Tool bookkeeping — do not hand-edit:

```yaml
_meta:
  schema_version: 1
  tool_version: "0.1.0"
  prompt_version: "bib-2"      # matches prompt_versions.bib_extract in config.yaml
  generated_at: "2026-06-17T20:00:00+00:00"
  pdf_sha256: "ab12..."
```

`prompt_version` is used by `.state.json` to detect whether the bib stage
needs re-running. See
[configuration.md](configuration.md#prompt-versions-and-cache-invalidation).

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
│    conflicts → may set needs_review: true               │
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
human | tool:* > osti > openalex > crossref > dblp > bibtex > arxiv > pdf > llm > semanticscholar > derived > unknown
```

A higher-priority source always wins per field. Tier-1 sources (osti,
openalex, crossref) are always queried in parallel; fallback sources are only
tried when tier-1 left gaps.

`human` and any `tool:*` source share priority 100. Both are **sticky**: once
a field is tagged with either, no automated source will overwrite it.
`tool:<name>` is the convention for agent or scripted corrections (e.g.
`tool:claude-opus-4`, `tool:fix-bib-script`). The distinction is audit-only;
both behave identically in the resolution pipeline.

---

## Correcting bib.yaml

### Recommended: `puba bib edit`

The safest way to correct fields — for humans and agents — is `puba bib edit`.
It validates the patch, stamps sticky provenance automatically, and appends an
audit entry to `_edit_log`:

```bash
# Fix one field interactively
puba bib edit paper.pdf --set "title=Corrected Title" --note "truncated in OpenAlex"

# Fix multiple fields from a JSON file
puba bib edit paper.pdf --json-file patch.json --clear-review

# Agent/tool workflow: read writable JSON, pipe corrected fields back
puba show bib paper.pdf --writable \
  | jq '.title = "Corrected Title"' \
  | puba bib edit paper.pdf --json-file - --source tool:my-agent --clear-review
```

**`--source`**: defaults to `human`. Pass `tool:<name>` for scripted or
agent-driven corrections (e.g. `--source tool:claude-opus-4`). Either source
marks the field sticky.

**`--clear-review`**: atomically sets `needs_review: false` and removes
`_review_reasons`. Use this once all corrections are applied and verified.

**`--dry-run`**: prints the proposed diff without writing anything.

**`--json`**: emits a JSON envelope on stdout (suitable for agent chaining).

### Fallback: editing `bib.yaml` by hand

If you prefer to edit directly:

```yaml
title: "My corrected title"
_provenance:
  title:
    source: human
    lookup_key: null
    at: "2026-06-17T20:00:00+00:00"
    note: "corrected from OpenAlex which had a truncated title"
```

Any field whose `_provenance` entry has `source: human` (or `source: tool:*`)
will never be overwritten by future `puba bib` runs.

**Deleting a field** (setting it to `null` or removing the key) causes the
next `puba bib` run to attempt to re-derive it from sources — *unless* its
provenance is also marked sticky.

**Deleting `_provenance` entirely** is safe; the next run rebuilds it
from scratch treating nothing as sticky.

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
| `references_count` | integer | Number of references parsed from the PDF; informational only |
| `needs_review` | boolean | `true` when review is required (see triggers below). `puba bib` exits 3. `puba md` exits 3 if `bib.yaml` is missing or has `needs_review: true`. `puba show info` warns loudly. |
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

## `needs_review` triggers

`needs_review: true` is set when **any** of the following are true after the
full resolution pipeline:

| Trigger | `_review_reasons` entry |
|---|---|
| ≥2 good-quality tier-1 sources disagree on a field | `"sources disagreed: <field>, ..."` |
| `title` is missing | `"title missing"` |
| `authors` is missing | `"authors missing"` |
| `year` is missing | `"year missing"` |
| LLM bootstrap failed **and** no DOI **and** no arXiv ID found in PDF | `"no identifiers extracted from PDF (no DOI, no arXiv ID, LLM failed)"` |

"Good-quality" means `sim ≥ bib.min_title_similarity` (default 0.90) or a
DOI-confirmed match (`sim = 1.0`). A low-sim hit from one source does not
constitute a conflict with a high-confidence hit from another.

`_review_reasons` lists all triggered reasons. It is omitted when `needs_review: false`.

When `needs_review: true`, `puba bib` exits with code 3. `puba md` also exits
with code 3 when `bib.yaml` is missing or flagged for review, forcing you to
resolve bib completely before rendering proceeds. Mark corrected fields with
`source: human` in `_provenance` to pin them permanently.

---

## `_conflicts` entries

Present only when tier-1 sources disagreed. Lists the values returned by each
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

## `_edit_log` entries

Present when `puba bib edit` has been run at least once. Append-only list of
every edit session applied to this `bib.yaml`:

```yaml
_edit_log:
  - at: "2026-06-20T14:30:00+00:00"
    source: human
    fields_changed: [title, year]
    note: "corrected truncated title and wrong year"
    cleared_review: true
  - at: "2026-06-21T09:00:00+00:00"
    source: tool:my-agent
    fields_changed: [venue]
    note: null
    cleared_review: false
```

Each entry records `at` (ISO timestamp), `source` (the `--source` value),
`fields_changed` (list of field names touched in that session), `note` (the
`--note` value, or null), and `cleared_review` (whether `--clear-review` was
passed). The log is never truncated by `puba bib edit`; it is reset only if
`puba bib --force` re-resolves from scratch (which does not preserve the edit
log).

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

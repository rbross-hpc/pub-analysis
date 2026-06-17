# Configuration

This file documents all configuration knobs for puba.

To see the *resolved* configuration (packaged defaults merged with any
project-local overrides, with the source of each key shown):

```bash
puba config show
```

To validate that all regexes compile, enums are consistent, and required
environment variables are set:

```bash
puba config validate
```

See also: [bib-yaml.md](bib-yaml.md) for the `bib.yaml` output schema and
resolution flow.

---

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `OPENAI_API_KEY` | **Yes** | Argo API key — your Argo username (e.g. `rross`). Used for LLM title extraction (`puba bib`) and section cleanup (`puba md`). The variable name is itself configurable via `argo.api_key_env` in `config.yaml`. |
| `OPENALEX_MAILTO` | Recommended | Your email address. Enables the OpenAlex + CrossRef "polite pool" for faster, more reliable API access. Without it, requests go through the anonymous pool and are more likely to be rate-limited. |
| `SEMANTICSCHOLAR_API_KEY` | Optional | Semantic Scholar API key. Without one, unauthenticated requests are aggressively rate-limited (~1/sec, frequent 429s). Semantic Scholar is only queried as a last resort when all other sources fail. |
| `OPENALEX_API_KEY` | Optional | OpenAlex API key. Most users will not have one; safe to omit. |

Put sensitive values in a `.env` file at the repo root (already in `.gitignore`):

```
OPENAI_API_KEY=rross
OPENALEX_MAILTO=you@anl.gov
```

`puba` loads `.env` automatically at startup via `python-dotenv`.

---

## Configuration files and load order

Configuration is resolved in this order. Later sources override earlier ones for
any key they specify; keys not overridden fall through to the packaged default.

1. **Packaged `config.yaml`** (in the repo root) — always loaded; contains all
   defaults.
2. **`puba.config.yaml`** in the current working directory — project-local
   overrides. Deep-merged on top of the packaged config. Create this file to
   tune models, rate limits, or classification lists for a specific paper
   corpus without touching the packaged config.
3. **`_provenance.<field>.source: human`** in `bib.yaml` — per-field sticky
   overrides. Any field marked `human` in provenance is never overwritten by
   future `puba bib` runs, regardless of what sources return. See
   [bib-yaml.md](bib-yaml.md#editing-bib-yaml-by-hand).

`puba config show` prints every key with its resolved value and the source that
contributed it (packaged / project-local).

### Example project-local override

```yaml
# ./puba.config.yaml
models:
  bib_extract: "Claude Sonnet 4.6"

bib:
  rate_limits_s:
    semanticscholar: 4.0
```

Only the keys you specify are overridden. All other values remain at their
packaged defaults.

---

## Models

puba uses two model roles:

| Role | Config key | Default | Used by |
|---|---|---|---|
| `bib_extract` | `models.bib_extract` | `GPT-5.4` | LLM page-1 title/metadata extraction in `puba bib` |
| `md_cleanup` | `models.md_cleanup` | `GPT-5.4` | Per-section artifact cleanup in `puba md` |

```yaml
# config.yaml
models:
  bib_extract: "GPT-5.4"
  md_cleanup:  "GPT-5.4"
```

### Available Argo models

```
GPT-5.5
GPT-5.4
GPT-5-mini
Claude Sonnet 4.6
Gemini 2.5 Pro
Claude Opus 4.7
```

puba sends whatever model name is in `config.yaml` directly to the Argo API;
no validation of the model name is done at startup.

### Overriding a model role

In `puba.config.yaml` in your working directory:

```yaml
models:
  bib_extract: "Claude Opus 4.7"
```

This overrides only `bib_extract`; `md_cleanup` falls through to the packaged
default.

> **Cache note:** Changing the model name does *not* automatically invalidate
> the `.state.json` cache. If you want re-extraction after a model change, either
> run `puba bib --force` or bump `prompt_versions.bib_extract` in your
> `puba.config.yaml`.

---

## Argo endpoint

```yaml
argo:
  base_url: "https://apps.inside.anl.gov/argoapi/v1"
  api_key_env: "OPENAI_API_KEY"
```

`api_key_env` is the *name* of the environment variable that holds the API key,
not the key itself. To use a different variable name:

```yaml
# puba.config.yaml
argo:
  api_key_env: "MY_ARGO_KEY"
```

---

## Rate limits

Minimum seconds between successive requests to each source. puba enforces these
per-process using a simple monotonic timer.

```yaml
bib:
  rate_limits_s:
    openalex: 2.0
    crossref: 2.0
    dblp: 1.0
    semanticscholar: 8.0
    arxiv: 3.0
    osti: 2.0
```

Tune these down if you have API keys / polite-pool access; tune them up if you
see intermittent 429s.

---

## Conflict detection thresholds

When tier-1 sources (OpenAlex, CrossRef, OSTI) disagree on a field beyond these
thresholds, `bib.yaml` is written with `needs_review: true` and the conflicting
values are recorded under `_conflicts`.

```yaml
bib:
  conflict_thresholds:
    title_sim_min: 0.85        # pairwise title SequenceMatcher ratio below this → conflict
    year_diff_max: 1           # year difference above this → conflict
    venue_sim_min: 0.70        # pairwise venue similarity below this → conflict
    author_surname_must_match: true   # first-author surname mismatch → conflict
    doi_must_match: true              # any DOI mismatch among non-null values → conflict
```

`puba info` and `puba md` print a loud warning when `needs_review: true`.

---

## Classification lists

Category classification is config-driven. The cascade order is fixed in code
(see [bib-yaml.md](bib-yaml.md#category-classification-cascade)); the *contents*
of each rule — which acronyms count as conferences, which DOI prefixes are
preprints, etc. — are all in `config.yaml` under `bib.classification`. Add or
remove entries in `puba.config.yaml` without touching the packaged config.

### `bib.classification.conference_acronyms`

Whole-word matched against the venue string (case-insensitive). Any match
classifies the paper as `conference paper`.

```yaml
bib:
  classification:
    conference_acronyms:
      - SC
      - NeurIPS
      - ICML
      # ... add domain-specific acronyms here
```

### `bib.classification.conference_venue_patterns`

Regex patterns matched against the venue string. Any match classifies as
`conference paper` (after workshop patterns are checked first).

```yaml
    conference_venue_patterns:
      - "^Proceedings of"
      - "\\bConference\\b"
      - "\\bSymposium\\b"
```

### `bib.classification.workshop_patterns`

Regex patterns matched before conference patterns. Match → `workshop paper`.

```yaml
    workshop_patterns:
      - "\\bWorkshop\\b"
      - "\\bWksp\\b"
```

### `bib.classification.preprint_hosts`

Domain names of preprint servers. Matched against venue string and DOI URL.
Match → `preprint`.

```yaml
    preprint_hosts:
      - biorxiv.org
      - medrxiv.org
      - chemrxiv.org
      - ssrn.com
      - osf.io/preprints
      - researchsquare.com
      - techrxiv.org
```

### `bib.classification.preprint_doi_prefixes`

DOI prefixes for preprint servers. Match → `preprint`.

```yaml
    preprint_doi_prefixes:
      - "10.1101"    # bioRxiv / medRxiv
      - "10.26434"   # ChemRxiv
      - "10.31219"   # OSF
      - "10.2139"    # SSRN
      - "10.21203"   # Research Square
```

Note: `10.48550/arxiv.*` is handled separately (maps to `arxiv preprint`, not
`preprint`) and is not in this list.

### `bib.classification.technical_report_crossref_types`

CrossRef `type` values that map to `technical report`.

```yaml
    technical_report_crossref_types:
      - report
      - report-component
```

### `bib.classification.technical_report_venue_patterns`

Regex patterns matched against venue string. Match → `technical report`.

```yaml
    technical_report_venue_patterns:
      - "Technical Report"
      - "Tech\\. Rep\\."
      - "ANL-"
      - "ORNL/TM-"
      - "LA-UR-"
      - "SAND\\d{4}"
      - "LBNL-"
      - "PNNL-"
      - "NASA[/-]TM"
```

### `bib.classification.book_crossref_types`

CrossRef `type` values that map to `book` or `book chapter`.

```yaml
    book_crossref_types:
      - book-chapter
      - book
      - monograph
      - edited-book
```

### `bib.classification.thesis_crossref_types` and `thesis_venue_patterns`

```yaml
    thesis_crossref_types:
      - dissertation

    thesis_venue_patterns:
      - "\\b(PhD|Ph\\.D\\.|MS|M\\.S\\.|Master'?s|Doctoral)\\s+(Thesis|Dissertation)\\b"
```

---

## Section heading detection

Controls how `puba md` splits the paper body into sections.

```yaml
md:
  section_heading_words:
    - Abstract
    - Introduction
    - Background
    - "Related Work"
    - Methods
    - Methodology
    - Results
    - Discussion
    - Conclusion
    - References
    - Acknowledgments
    # ... add domain-specific headings in puba.config.yaml

  section_numbered_pattern: "^(\\d+(\\.\\d+)*)\\s+[A-Z]"
```

`section_heading_words` are matched case-insensitively as standalone lines (or
the first word of a short line). `section_numbered_pattern` catches numbered
headings like `1 Introduction` or `2.1 Related Work`.

To add domain-specific headings without touching the packaged list:

```yaml
# puba.config.yaml
md:
  section_heading_words:
    - Theorem
    - Proof
    - Algorithm
    - Notation
    - Preliminaries
```

> **Note:** Because the project-local config deep-merges *lists* by replacement
> (not by append), you must include any packaged words you still want when
> overriding `section_heading_words`. To extend rather than replace, copy the
> full packaged list into your `puba.config.yaml` and add to it.

---

## Prompt versions and cache invalidation

```yaml
prompt_versions:
  bib_extract: "bib-2"
  md_cleanup:  "md-cleanup-1"
```

The prompt version for each stage is written into `.state.json` alongside the
PDF sha256. A stage is considered cached when all three match:

- PDF sha256 (file unchanged)
- `prompt_version` for that stage
- `tool_version`

**To force re-extraction after changing a prompt** (without touching every
paper's `--force`): bump the version string in `config.yaml` or your
`puba.config.yaml`. On the next run, all papers that cached under the old
version will be re-processed automatically.

**Changing the model name** does *not* bump the cache key — only the prompt
version does. If you switch from `GPT-5.4` to `Claude Opus 4.7` and want fresh
output, bump `prompt_versions.bib_extract` alongside the model change.

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

### Bootstrapping a local override file

```bash
puba config init                       # writes ./puba.config.yaml (verbatim copy of packaged config)
puba config init --path ./mydir        # writes ./mydir/puba.config.yaml
puba config init --force               # overwrite an existing file
```

The copy is byte-for-byte identical to the packaged `config.yaml` (comments
preserved). Edit it, then run `puba config show` to confirm which keys now
resolve from `project-local`.

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

puba uses one model role for LLM calls:

| Role | Config key | Default | Used by |
|---|---|---|---|
| `bib_extract` | `models.bib_extract` | `GPT-5.4` | LLM page-1 title/metadata extraction in `puba bib` |

```yaml
# config.yaml
models:
  bib_extract: "GPT-5.4"
```

`puba md` uses MinerU (a local ML pipeline), not an LLM — no model config needed for that stage.

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

When ≥2 good-quality tier-1 sources (OpenAlex, CrossRef, OSTI) disagree on a
field beyond these thresholds, `needs_review: true` is set and the conflicting
values are recorded under `_conflicts`. Only sources with `sim ≥ min_title_similarity`
(or a DOI-confirmed match) are considered; low-sim hits do not trigger conflicts.

```yaml
bib:
  conflict_thresholds:
    title_sim_min: 0.85        # pairwise title SequenceMatcher ratio below this → conflict
    year_diff_max: 1           # year difference above this → conflict
    venue_sim_min: 0.70        # pairwise venue similarity below this → conflict
    author_surname_must_match: true   # first-author surname mismatch → conflict
    doi_must_match: true              # any DOI mismatch among non-null values → conflict
```

`needs_review: true` is also set when core fields (`title`, `authors`, `year`)
are missing after the full pipeline, or when LLM bootstrap failed and no DOI
or arXiv ID was found in the PDF. See
[bib-yaml.md](bib-yaml.md#needs_review-triggers) for the full trigger list.

`puba bib` and `puba run` exit with code 3 when `needs_review: true`. `puba
show info` and `puba md` print a warning listing the reasons.

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

## Markdown rendering (`puba md`)

`puba md` uses MinerU's `pipeline` backend with formula recognition
disabled. Section headings are parsed directly from MinerU's `#`-prefixed
markdown output — no heuristic heading-word lists or regex patterns are needed.

```yaml
md:
  mineru_version: "mineru-1"
```

`mineru_version` is the cache-invalidation key for the md stage (analogous to
`prompt_versions.bib_extract` for the bib stage). Bump it when you upgrade
MinerU or when you want all papers to be re-processed on next run.

**First run:** MinerU downloads ~1.5–3 GB of model weights to
`~/.cache/huggingface/` automatically. GPU is strongly recommended; CPU-only
processing of a 50-page paper takes ~10 minutes.

---

## Prompt versions and cache invalidation

```yaml
prompt_versions:
  bib_extract: "bib-2"

md:
  mineru_version: "mineru-1"
```

The version string for each stage is written into `.state.json` alongside the
PDF sha256. A stage is considered cached when all three match:

- PDF sha256 (file unchanged)
- version key for that stage (`prompt_versions.bib_extract` or `md.mineru_version`)
- `tool_version`

**To force re-extraction after changing a prompt or upgrading MinerU** (without
touching every paper's `--force`): bump the relevant version string in
`config.yaml` or your `puba.config.yaml`. On the next run, all papers that
cached under the old version will be re-processed automatically.

**Changing the model name** for `bib_extract` does *not* bump the cache key —
only the prompt version does. If you switch models and want fresh output, bump
`prompt_versions.bib_extract` alongside the model change.

---

## Distillation configuration

```yaml
distill:
  default_model: "Claude Sonnet 4.6"   # fallback when a query has no per-query model
  max_input_tokens: 100000             # hard cap; error if input exceeds this

  narrative_strip_sections:            # section headings stripped for scope=narrative
    - References
    - Bibliography
    - Acknowledgments
    - Acknowledgements
    - Funding
    - "Author contributions"
    - "Conflict of interest"
    - "Generative AI statement"
    - "Publisher's note"
    - Appendix
    - Supplementary

  queries:
    summary:                           # the one packaged default
      scope: abstract
      prompt: |
        Summarize this paper in 3 sentences:
        (1) the problem or question addressed,
        (2) the approach or method used,
        (3) the key result or contribution.
        Be specific; avoid generic claims.
      max_chars: 600
```

Add more queries in `./prompts/<name>.yaml` files in your working directory.
For the full schema, CLI reference, output format, caching semantics, and
examples, see [distillations.md](distillations.md).

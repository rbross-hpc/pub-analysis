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

## LLM backend and authentication

The default `opencode` backend starts and owns a private native OpenCode server
and uses the provider credentials already managed by OpenCode. Each LLM request
gets a new disposable OpenCode session, which puba deletes after the request.
Install OpenCode and authenticate before making an LLM request:

```bash
opencode providers login
```

No OpenAI API key is required for this backend. `puba config validate` checks
that the `opencode` executable is on `PATH`; authentication is checked when the
first request is made.

```yaml
llm:
  backend: opencode
  timeout_s: 120
```

To use a direct OpenAI-compatible API instead, select the `openai` backend in
`puba.config.yaml`:

```yaml
llm:
  backend: openai
```

Direct mode requires these values in the environment or a `.env` file:

```
OPENAI_API_KEY=rross
OPENAI_BASE_URL=https://apps.inside.anl.gov/argoapi/v1
```

Other environment variables are independent of the LLM backend:

| Variable | Required | Description |
|---|---|---|
| `OPENALEX_MAILTO` | Recommended | Your email address for the OpenAlex and CrossRef polite pools. |
| `SEMANTICSCHOLAR_API_KEY` | Optional | Semantic Scholar API key; unauthenticated requests are aggressively rate-limited. |
| `OPENALEX_API_KEY` | Optional | OpenAlex API key. Most users can omit it. |

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
  opencode:
    bib_extract: "openai/gpt-6-luna"

bib:
  rate_limits_s:
    semanticscholar: 4.0
```

Only the keys you specify are overridden. All other values remain at their
packaged defaults.

---

## Models

puba uses three model roles for LLM calls:

| Role | Config key | OpenCode default | OpenAI-compatible default | Used by |
|---|---|---|---|---|
| `bib_extract` | `models.<backend>.bib_extract` | `openai/gpt-5.6-sol` | `GPT-5.4` | LLM page-1 title/metadata extraction in `puba bib` |
| `distill` | `models.<backend>.distill` | `openai/gpt-5.6-sol` | `Claude Sonnet 4.6` | Distillation queries in `puba distill` |
| `claim_verify` | `models.<backend>.claim_verify` | `openai/gpt-5.6-sol` | `Claude Sonnet 4.6` | Separate model-assessed claim-support requests in `evidence_mode: claims` |

```yaml
# config.yaml
models:
  opencode:
    bib_extract: "openai/gpt-5.6-sol"
    distill:     "openai/gpt-5.6-sol"
    claim_verify: "openai/gpt-5.6-sol"
  openai:
    bib_extract: "GPT-5.4"
    distill:     "Claude Sonnet 4.6"
    claim_verify: "Claude Sonnet 4.6"
```

`puba md` uses MinerU, not an LLM, so model-role configuration does not apply
to that stage. MinerU runs as a service by default.

### Model names

OpenCode models must use provider-qualified `provider/model` identifiers. Use
`opencode models` to list identifiers available to the authenticated
installation. The shipped OpenCode default is:

```
openai/gpt-5.6-sol
```

The `openai` backend sends model names unchanged to the configured
OpenAI-compatible endpoint. Its model names do not need `provider/model` form.

### Overriding a model role

In `puba.config.yaml` in your working directory:

```yaml
models:
  opencode:
    bib_extract: "openai/gpt-6-luna"
    distill:     "openai/gpt-5.6-sol"
    claim_verify: "openai/gpt-5.6-sol"
```

Or per-invocation with `--model` (highest precedence, does not require editing config):

```bash
puba bib paper.pdf --model "openai/gpt-6-luna"
puba distill paper.pdf --model "openai/gpt-5.6-sol"
puba distill paper.pdf --verifier-model "openai/gpt-5.6-sol"
```

For answer generation, `--model` overrides query `model`, which overrides
`models.<backend>.distill`. For claim support, `--verifier-model` overrides
query `verifier_model`, which overrides `models.<backend>.claim_verify`. The
atomizer uses the resolved answer-generation model; only the semantic support
assessment uses the verifier model. The two roles remain separate requests and
separate provenance entries even when they resolve to the same identifier.

> **Cache note:** Backend, one-shot protocol version, and model name are included
> in LLM-backed cache identity. Changing any of them causes affected stages to
> re-run rather than return a stale cached result. Current provenance records
> `llm_protocol_version: oneshot-v1`.

---

## Direct OpenAI-compatible endpoint

This section applies only when `llm.backend: openai`. The OpenAI Python SDK
reads the endpoint and API key from environment variables:

| Env var | Purpose |
|---|---|
| `OPENAI_BASE_URL` | API endpoint URL |
| `OPENAI_API_KEY` | API key / credentials |

For Argo:
```
OPENAI_BASE_URL=https://apps.inside.anl.gov/argoapi/v1
OPENAI_API_KEY=<your-argo-username>
```

For real OpenAI:
```
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_API_KEY=sk-...
```

For a local Ollama instance:
```
OPENAI_BASE_URL=http://localhost:11434/v1
OPENAI_API_KEY=ollama
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

`puba bib` exits with code 3 when `needs_review: true`. `puba md` exits with
code 3 when `bib.yaml` is missing or has `needs_review: true`. `puba show info`
prints a warning listing the reasons.

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

`puba md` uses MinerU's `pipeline` backend with formula recognition disabled.
The default extractor submits an asynchronous task to the MinerU service,
polls it to completion, downloads a ZIP, and normalizes the returned files to
the same layout as local MinerU. Section headings are parsed directly from
MinerU's `#`-prefixed markdown output.

```yaml
md:
  extractor: service
  mineru_version: "mineru-6"
  service:
    base_url: "http://host-localhost:8001"
    timeout_s: 1800
    poll_interval_s: 2
```

`mineru_version` is the cache-invalidation key for the md stage (analogous to
`prompt_versions.bib_extract` for the bib stage). Bump it when you upgrade
MinerU or when you want all papers to be re-processed on next run.

The extractor and normalized service URL are also included in the cache key.
Changing either causes re-extraction. `timeout_s` is the total task deadline;
`poll_interval_s` controls status polling.

To use a local MinerU subprocess, install `puba[local-mineru]` and override:

```yaml
md:
  extractor: local
```

There is no automatic fallback from service to local mode. A failed service
task returns an error without silently repeating extraction locally.

---

## Prompt versions and cache invalidation

```yaml
prompt_versions:
  bib_extract: "bib-2"

md:
  mineru_version: "mineru-6"
```

The version string for each stage is written into `.state.json` alongside the
PDF sha256. A stage is considered cached when all three match:

- PDF sha256 (file unchanged)
- version key for that stage (`prompt_versions.bib_extract` or `md.mineru_version`)
- extractor identity (`service` plus URL, or `local`)

**To force re-extraction after changing a prompt or upgrading MinerU** (without
touching every paper's `--force`): bump the relevant version string in
`config.yaml` or your `puba.config.yaml`. On the next run, all papers that
cached under the old version will be re-processed automatically.

**Changing the model name** for `bib_extract` (via
`models.<backend>.bib_extract` or
`--model`) is included in the cache key — `puba bib` re-runs automatically
when the model differs from the cached result.

---

## Distillation configuration

```yaml
distill:
  max_input_tokens: 100000             # hard cap; error if input exceeds this
  default_evidence_mode: claims        # none | quotes | claims

  claims:
    max_claims: 20
    max_evidence_per_claim: 4
    max_quote_chars: 4000
    max_atomizer_input_tokens: 100000

  verification:
    max_context_tokens_per_claim: 4000
    max_total_input_tokens: 50000
    max_rationale_chars: 1000

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

`default_evidence_mode` applies when a query defines neither `evidence_mode`
nor the legacy `evidence` field. The packaged default is `claims`:

- `none` generates free text and omits quote/claim fields.
- `quotes` requests exact quotations and checks their presence locally.
- `claims` generates an answer, atomizes it into claims and proposed quotes,
  checks quote presence locally, then asks the verifier model to assess each
  claim that has uniquely present evidence.

Legacy `evidence: true` means `quotes`; `evidence: false` means `none`. Defining
both `evidence` and `evidence_mode` in one query is an error. The legacy alias
does not select `claims`.

All claim and verification limits must be positive integers. Token limits
include internal instructions and request content. An over-budget atomizer or
verifier request is rejected before that affected call; exact quotations are
not truncated to fit. Exceeding a limit or exhausting retries aborts the query
without replacing its existing artifact or state entry.

Example query-level overrides:

```yaml
distill:
  queries:
    checked_summary:
      scope: abstract
      evidence_mode: claims
      verifier_model: "openai/gpt-5.6-sol"
      prompt: Summarize the paper's principal result.

    quotation_index:
      scope: narrative
      evidence_mode: quotes
      prompt: List the paper's stated contributions.

    format_only:
      scope: abstract
      evidence_mode: none
      prompt: Render the abstract as a compact table.
```

`puba config validate` checks the evidence-mode default, query modes,
legacy-field conflicts, verifier-model strings, the `claim_verify` model role,
and all limits. Full schema, cache/provenance, and trust semantics are in
[distillations.md](distillations.md) and
[ADR 0005](decisions/0005-separate-quote-presence-from-model-assessed-claim-support.md).

Add more queries in `./prompts/<name>.yaml` files in your working directory.
For the full schema, CLI reference, output format, caching semantics, and
examples, see [distillations.md](distillations.md).

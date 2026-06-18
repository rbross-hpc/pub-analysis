# Distillations

A distillation is a named, cached LLM analysis of a paper. Each distillation
asks one question (defined by a prompt) against one slice of the paper (defined
by a scope) and writes the result to `<pdf>.puba/analyses/<name>.yaml`.

See also:
- [configuration.md](configuration.md) for model and token-budget config
- [bib-yaml.md](bib-yaml.md) for the `bib.yaml` schema that distillations read

---

## Quick start

```bash
# Run all defined distillations (default: just 'summary')
puba distill paper.pdf

# Run only a specific distillation
puba distill paper.pdf --only summary

# List all defined distillations and their status for a paper
puba distill paper.pdf --list

# Re-run even if cached
puba distill paper.pdf --force

# See what would run without running it
puba distill paper.pdf --dry-run
```

`puba run` does **not** include distillation — run it explicitly after `puba bib`
and (if needed) `puba md`.

---

## Defining a distillation

Distillation queries are defined in YAML. Each query has:

```yaml
<name>:
  scope: abstract | narrative | full | section
  prompt: |
    <multi-line prompt text>
  max_chars: 600        # optional — soft instruction + hard truncation
  model: "GPT-5.4"     # optional — overrides distill.default_model
```

### Name rules

- Alphanumeric characters and underscores only: `^[a-zA-Z_][a-zA-Z0-9_]*$`
- Used as the YAML key, CLI `--only` selector, and output filename
- Must be filesystem-safe and shell-safe

### Scope

| Scope | Content sent to the LLM | Required |
|---|---|---|
| `abstract` | Bib header (title, authors, venue, year) + abstract from `bib.yaml` | `bib.yaml` with non-empty abstract |
| `narrative` | Bib header + `paper.md` with References, Acknowledgments, Funding, etc. stripped | `bib.yaml` + `paper.md` |
| `full` | Bib header + `paper.md` verbatim | `bib.yaml` + `paper.md` |
| `section` | Bib header + the body of one named section from `paper.md` | `bib.yaml` + `paper.md` (and therefore `paper.sections.json`) |

If the required artifacts are missing, `puba distill` exits with a clear error
pointing to the command needed to generate them.

#### `scope: section` — targeting a specific section

Add a `section:` field naming the section's **short name** (as shown by
`puba sections <pdf>`):

```yaml
# prompts/methods_critique.yaml
methods_critique:
  scope: section
  section: methods      # short_name from puba sections
  prompt: |
    Critique the methodology described in this section. Identify any
    threats to validity, missing controls, or claims that go beyond
    what the methods support.
  max_chars: 1500
```

If the named section does not exist in this paper, `puba distill` reports
`missing-section` status for that query and lists all available section
short names:

```
  methods_critique ... ✗ missing-section
  Error (methods_critique): Section 'methods' not found in this paper.
  Available sections: abstract, introduction, experimental_setup, results, discussion, references
  Run `puba sections <pdf>` to see the full list.
```

Page markers (`<!-- page N -->`) are stripped from the section body before
sending to the LLM.

Use `puba sections <pdf>` to discover the short names available for a
specific paper before writing a `scope: section` query.

**Short-name format:** names are ≤ 4 lowercase words joined by `_`, derived
from the section title by stripping leading numeric prefixes and punctuation,
then keeping the first four words. Examples: `"2.1 Related Work"` →
`related_work`; `"1 FMs become cost-effective when..."` →
`fms_become_cost_effective`. Collisions are disambiguated with `_2`, `_3`
suffixes.

### `max_chars`

Optional. When set:

1. **Soft:** appended to the prompt as
   `"Your response MUST be at most N characters. Be concise."`
2. **Hard:** if the LLM exceeds N characters, the output is truncated at the
   nearest word boundary and `…` is appended. Truncation is logged in
   `_provenance.truncated`.

If omitted, no length enforcement is applied; the LLM produces whatever its
prompt directs.

### `model`

Optional per-query override. Falls back to `distill.default_model` in
`config.yaml` (default: `Claude Sonnet 4.6`).

---

## Where to put distillation definitions

Definition load order (later wins on name collision):

1. **Packaged `config.yaml`** — ships one default query: `summary`.
2. **Project-local `./puba.config.yaml`** — `distill.queries.*` block.
3. **`./prompts/*.yaml`** — scanned in alphabetical filename order from
   the current working directory. Each file defines one or more queries at
   the top level (no `distill.queries:` wrapper needed).

Same-name collisions *within* `prompts/*.yaml` files (two different files
defining the same query name) are a hard validation error caught by
`puba config validate`.

### Single-query file

```yaml
# prompts/contributions.yaml
contributions:
  scope: narrative
  prompt: |
    List the explicit contributions of this paper as a markdown bulleted list.
    Use the paper's own framing. Include only what the authors claim as contributions.
  max_chars: 800
```

### Multi-query file

```yaml
# prompts/critique_suite.yaml
threats_to_validity:
  scope: narrative
  prompt: |
    Identify potential threats to the validity of this paper's claims.
    Consider: internal validity, external validity, construct validity,
    statistical conclusion validity. Return as a markdown bulleted list.

assumptions:
  scope: full
  prompt: |
    List the assumptions this paper makes, both explicit and implicit.
    Return as a markdown numbered list with a one-sentence explanation of
    each assumption and its role in the work.
```

---

## Output format

Each distillation writes `<pdf>.puba/analyses/<name>.yaml`:

```yaml
# puba distill — summary
# generated_at: 2026-06-17T20:42:00+00:00
# scope: abstract  model: Claude Sonnet 4.6

name: summary
scope: abstract
model: Claude Sonnet 4.6
generated_at: "2026-06-17T20:42:00+00:00"
output: |
  Mofka is a persistent event-streaming framework designed for HPC environments
  that combines streaming semantics with RDMA-enabled network support and
  massively multicore optimizations. It achieves up to 8× throughput improvement
  over Kafka and Redpanda on Polaris and Frontier, and demonstrates utility in
  tomographic reconstruction, MOF discovery, and Dask provenance workflows.

_provenance:
  source: "argo/Claude Sonnet 4.6"
  at: "2026-06-17T20:42:00+00:00"
  prompt_sha256: "a1b2c3d4..."
  input_sha256:  "d4e5f6a7..."
  bib_yaml_sha:  "259f78a3..."
  paper_md_sha:  null                  # null for scope=abstract
  tool_version: "0.1.0"
  prompt_source: "config.yaml"
  token_count_estimate: 487
  truncated: false
```

### Output field

The `output:` field is a YAML block scalar containing whatever the LLM
produced. Format is entirely determined by the prompt — prose, markdown
tables, numbered lists, JSON, etc. puba does not parse or validate the
content.

### Storage and encoding

- UTF-8, verbatim. No ASCII coercion, no normalization beyond:
  - Trailing whitespace stripped from each line
  - Leading and trailing blank lines stripped
  - Hard truncation at word boundary if `max_chars` exceeded

### Provenance fields

| Field | Description |
|---|---|
| `source` | `argo/<model-name>` |
| `at` | ISO timestamp of the LLM call |
| `prompt_sha256` | SHA256 (first 16 hex chars) of the resolved prompt string |
| `input_sha256` | SHA256 of the full content sent to the LLM |
| `bib_yaml_sha` | SHA256 of `bib.yaml` at run time |
| `paper_md_sha` | SHA256 of `paper.md`; null for `scope=abstract` |
| `tool_version` | puba version that ran this distillation |
| `prompt_source` | Which file the prompt definition was loaded from |
| `token_count_estimate` | Approximate input token count |
| `truncated` | `true` if `max_chars` hard truncation fired |
| `original_length` | Only present when `truncated: true`; character count before truncation |
| `max_chars` | Only present when `truncated: true` |

---

## Caching

Each distillation is cached in `.state.json` under `stages.distill.<name>`.
Cache key:

```
sha256(input_content) + sha256(resolved_prompt) + model_name
```

Re-run is triggered when any of the three change:

- The PDF changes → input sha changes
- The prompt text is edited → prompt sha changes
- The model is changed in config → model name changes

`--force` bypasses the cache for all selected queries.

Unlike `bib` and `md` stages, the distillation cache key includes the model
name, because model choice meaningfully changes the output and re-running
after a model change should produce fresh results.

---

## CLI reference

| Command | Effect |
|---|---|
| `puba distill <pdf>` | Run all defined distillations |
| `puba distill <pdf> --only NAME` | Run only the named query (repeatable) |
| `puba distill <pdf> --force` | Re-run even if cached |
| `puba distill <pdf> --list` | Rich table: name, scope, model, status |
| `puba distill <pdf> --list --json` | Same as JSON |
| `puba distill <pdf> --dry-run` | Show what would run + token estimate |
| `puba clean <pdf> --what distill` | Remove all `analyses/*.yaml` + cache entries |

Exit codes: 0 = all succeeded; 1 = one or more queries failed; 2 = config error.

One query's failure does not block the others — all selected queries are
attempted, and a summary of failures is printed at the end.

---

## Narrative section stripping

For `scope=narrative`, puba strips sections by heading name before sending to
the LLM. The default strip list covers common venue conventions:

```yaml
distill:
  narrative_strip_sections:
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
```

Add project-specific section names (e.g., "Competing interests",
"Data availability") in `puba.config.yaml` — but note that list overrides
replace the packaged list entirely; copy the full packaged list and extend it.

---

## Validation

`puba config validate` checks:

- Every query name matches `^[a-zA-Z_][a-zA-Z0-9_]*$`
- Every query has a `scope` in `{abstract, narrative, full, section}`
- `scope: section` queries have a non-empty `section:` field matching `^[a-zA-Z_][a-zA-Z0-9_]*$` (syntax only — section existence is checked at runtime per paper)
- Every query has a non-empty `prompt`
- `max_chars`, if set, is a positive integer (warning if < 100)
- No duplicate names across `prompts/*.yaml` files
- `models.distill` is set or every query has a per-query `model`

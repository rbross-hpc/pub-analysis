# puba — publication analysis

Single-paper CLI: given one PDF, resolve its bibliographic information with full
provenance, render a clean markdown version, and run named LLM distillations
against the abstract, narrative, or full paper.

## Documentation

- [docs/configuration.md](docs/configuration.md) — LLM backends, authentication, models,
  rate limits, conflict thresholds, classification lists, prompt versions
- [docs/bib-yaml.md](docs/bib-yaml.md) — bibliographic-record schema, field reference,
  source priority, resolution flow, category enum, provenance entries
- [docs/distillations.md](docs/distillations.md) — defining distillation queries,
  scopes, schema-v2 quote/claim evidence, `prompts/` directory, caching
- [docs/markdown-rendering.md](docs/markdown-rendering.md) — rendering pipeline,
  page-numbering semantics, cover-page filter, section detection, MinerU intermediates
- [docs/figures.md](docs/figures.md) — figure extraction, manifest schema, `show figures` / `show figure`
- [tests/fixtures/README.md](tests/fixtures/README.md) — fixture licensing and
  criteria for adding new test PDFs
- [docs/design-log.md](docs/design-log.md) — retrospective design rationale and
  decisions log (historical background, not a live task list)

This project is developed via [loop-supervisor](https://github.com/rbross-hpc/loop-supervisor),
an autonomous planner/architect/builder/auditor loop. Its current standing
priorities live in [docs/OBJECTIVE.md](docs/OBJECTIVE.md); consequential
design decisions the loop must respect are recorded as ADRs under
[docs/decisions/](docs/decisions/).

---

## Install

```bash
pipx install git+https://github.com/rbross-hpc/pub-analysis.git
```

For development:

```bash
python3.11 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

Python 3.11+ required.

`puba md` uses the MinerU service at `http://host-localhost:8001` by default.
To run MinerU in the puba process instead, install the optional local backend:

```bash
pipx install 'git+https://github.com/rbross-hpc/pub-analysis.git#egg=puba[local-mineru]'
```

## Environment

The default `opencode` LLM backend starts a managed native OpenCode server and
uses your existing OpenCode provider credentials. Install OpenCode and
authenticate once:

```bash
opencode providers login
```

`OPENALEX_MAILTO=you@anl.gov` is recommended for bibliographic lookups and can
be placed in a `.env` file at the repo root (already in `.gitignore`). Each LLM
request uses a new disposable OpenCode session, which is deleted afterward.
OpenCode model IDs must use `provider/model` form. Direct OpenAI-compatible API
access remains available through `llm.backend: openai` using `OPENAI_BASE_URL`
and `OPENAI_API_KEY`. Full details are in
[docs/configuration.md](docs/configuration.md#llm-backend-and-authentication).

---

## Quick start

A typical first-time workflow for a new paper:

```bash
# 1. Resolve bibliographic metadata (title, authors, venue, DOI, …)
puba bib paper.pdf

# 2. Render clean markdown + detect sections
puba md paper.pdf

# 3. Inspect what sections were detected
puba show sections paper.pdf

# 4. Run the built-in summary distillation (scope: abstract)
puba distill paper.pdf --only summary

# 5. Review everything
puba show info paper.pdf
```

### Distillations quick start

Define a query in `prompts/my_queries.yaml`:

```yaml
contributions:
  scope: narrative
  evidence_mode: claims       # default; model-assessed claim support
  prompt: |
    List the explicit contributions of this paper as a markdown bulleted list.
    Use the paper's own framing.
  max_chars: 800

methods_critique:
  scope: section
  section: methods        # short_name from `puba show sections paper.pdf`
  prompt: |
    Critique the methodology. Identify threats to validity and unsupported claims.
  max_chars: 1200
```

Then run:

```bash
puba distill paper.pdf                  # run all defined queries
puba distill paper.pdf --list           # see status of all queries
puba distill paper.pdf --only methods_critique --force   # re-run one
puba distill paper.pdf --verifier-model "openai/gpt-5.6-sol" # claim verifier override

# Export an agent-friendly packet, then apply reviewed claim/prose decisions
puba review export paper.pdf summary > review-export.json
puba review apply paper.pdf summary --json-file review-input.json
puba review list paper.pdf
```

Queries default to `evidence_mode: claims`. Use `quotes` for deterministic
exact-quote presence without semantic claim assessment, or `none` for plain
free-text output. Quote presence and model-assessed claim support are separate:
a uniquely present quote is not proof that it supports a claim, and no support
label is human verification. See [docs/distillations.md](docs/distillations.md)
and [ADR 0005](docs/decisions/0005-separate-quote-presence-from-model-assessed-claim-support.md).

### Other useful commands

```bash
# Bib only (no markdown)
puba bib paper.pdf

# Markdown only (MinerU extraction)
puba md paper.pdf

# Show resolved configuration with per-key source
puba config show

# Validate configuration syntax (regexes, enums, env vars)
puba config validate

# Remove cached outputs and re-run fresh
puba clean paper.pdf
puba bib paper.pdf --force
# inspect the bibliographic record; fix any needs_review=true issues (recommended before puba md)
puba md paper.pdf --force
```

---

## Output layout

Each PDF gets its own analysis directory and, after explicit summarization, a
generated vault next to it:

```
paper.pdf
paper.vault/            # generated point-in-time summary vault (puba summarize; not auto-regenerated)
  README.md             # status, navigation, and every distillation prompt and answer
  bibliography.md       # bibliographic metadata and abstract
  figures.md            # figure captions, metadata, and links to copied images
  distillations/
    <safe-query>.md     # prompt, model, answer, claims, and evidence for one query
  assets/
    paper.pdf           # copied source PDF
    figures/            # copied extracted figure assets
  .obsidian/            # optional user-owned Obsidian settings; preserved on refresh
  notes/                # optional user-owned notes; preserved on refresh
paper.puba/
  bib.json              # resolved bibliographic record + per-field provenance (schema_version: 1)
  paper.md              # MinerU markdown with YAML frontmatter
  paper.sections.json   # section spans {short_name, title, level, start_offset, end_offset}
  paper.figures.json    # figure manifest (after puba figures)
  .state.json           # pdf sha256, stage timestamps, version keys (cache key)
  analyses/             # distillation outputs, one JSON file per named query (new writes: schema_version 2)
  reviews/              # durable human-review YAML overlays; preserved by clean --what all
  figures/              # per-figure JPG crops + JSON sidecars (after puba figures)
  mineru/               # MinerU intermediates (kept for debugging; removed by puba clean --what md)
    paper.md            # raw MinerU markdown before puba post-processing
    paper_content_list.json
    paper_content_list_v2.json
    paper_middle.json
    paper_layout.pdf     # local extractor only; optional
    service.json          # service endpoint, task, and version provenance
    images/             # MinerU raw figure crops (sha-named *.jpg)
```

`puba summarize paper.pdf` is explicit and deterministic: it makes no LLM or
network calls and refreshes `paper.vault/` only when invoked. The generated
Markdown uses relative links so the vault remains navigable after being moved.
On refresh, `.obsidian/` and `notes/` are preserved as user-owned content; all
other vault content is generated and replaced. After a successful refresh, a
legacy `paper.summary.md` carrying puba's generated-summary marker is deleted.
An unrecognized file at that legacy path is preserved and produces a warning.

If the PDF is on a read-only filesystem, `puba` will error. There is no
auto-fallback output directory; use a writable copy of the PDF.

---

## CLI reference

| Command | What it does |
|---|---|
| `puba bib <pdf>` | Resolve and write bibliographic information; exit 3 if `needs_review=true` |
| `puba bib edit <pdf>` | Apply a JSON field patch to the bibliographic record with sticky provenance |
| `puba md <pdf>` | Render clean markdown; warns if the bibliographic record is missing or `needs_review=true` but proceeds. Use `--strict-bib` to restore exit-3 behavior. |
| `puba figures <pdf>` | Extract per-figure JPG crops and manifest from MinerU layout output |
| `puba distill <pdf>` | Run all defined distillation queries |
| `puba distill <pdf> --only NAME` | Run one named distillation |
| `puba distill <pdf> --list` | List query currency plus evidence mode, quote presence, model-assessed claim support, and verifier model |
| `puba summarize <pdf>` | Generate or refresh the human-facing `<stem>.vault/` snapshot; explicit, deterministic, offline, and not auto-regenerated |
| `puba review export <pdf> NAME` | Emit generated claims, evidence, assessments, fingerprints, and existing review as JSON |
| `puba review apply <pdf> NAME --json-file FILE` | Validate and atomically merge human claim decisions and an optional answer override |
| `puba review rebase <pdf> NAME --json-file FILE` | Explicitly carry selected exact-match decisions onto a regenerated artifact |
| `puba review list <pdf>` | List review applicability and progress for all generated distillations |
| `puba review show <pdf> NAME` | Show review progress and the effective answer source; `--json` emits the full resolution |
| `puba review clear <pdf> NAME --claims\|--answer\|--all` | Clear selected active decisions while retaining audit history |
| `puba clean <pdf>` | Remove cached outputs |
| `puba show bib <pdf>` | Read resolved bib fields + provenance; errors if bib not resolved |
| `puba show md <pdf>` | Print rendered markdown to stdout; errors if not yet rendered |
| `puba show sections <pdf>` | List detected sections with short names and full titles |
| `puba show section <pdf> NAME` | Print the markdown content of a named section (includes heading and subsections) |
| `puba show figures <pdf>` | List extracted figures (id, page, type, size, caption) |
| `puba show figure <pdf> ID` | Show detail for one figure; `--path` prints JPG path; `--json --embed` adds base64 data URL |
| `puba show info <pdf>` | Combined status: bib, md, and figures stage status + distillation list, including separate evidence metadata; use `--json` for agent-friendly output |
| `puba show distill <pdf> NAME` | Print effective answer text, using a current human override when present |
| `puba show distill <pdf> NAME --original` | Print original generated answer text regardless of review state |
| `puba show distill <pdf> NAME --json` | Emit generated and effective answers, review resolution, schema-v2 claims/evidence/assessments, and provenance |
| `puba show distill <pdf> --all --json` | Emit all distillations as JSON |
| `puba config show` | Print fully resolved configuration + source of each key |
| `puba config validate` | Validate regexes, enums, required env vars |
| `puba config init` | Copy packaged `puba/config.yaml` into CWD as `puba.config.yaml` |

### Key flags

| Flag | Applies to | Effect |
|---|---|---|
| `--force` | bib, md, distill | Re-run even if stage is cached |
| `--strict-bib` | md | Require a resolved, review-clean bibliographic record before rendering; exit 3 otherwise (restores pre-1.0 behavior) |
| `--model MODEL` | bib, distill | Override the active backend's LLM model for this invocation (e.g. `openai/gpt-5.6-sol`). Invalidates cache if different from cached model. |
| `--verifier-model MODEL` | distill | Override the model used for claim-support assessment. Precedence: CLI, query `verifier_model`, active backend `claim_verify`. Applies to `claims` mode and participates in cache identity. |
| `--no-llm` | bib | Skip LLM title extraction; use PDF cover-page heuristic only |
| `--bibtex FILE` | bib | Provide a `.bib` file as a fallback metadata source. Must exist, be a file (not a directory), and contain at least one parseable entry; otherwise the stage fails. |
| `--dry-run` | bib, md | Print what would run without running it |
| `--only NAME` | distill | Run only the named distillation (repeatable) |
| `--list` | distill | List all defined queries with cached status |
| `--json` | bib, md, distill, review list/show, show bib, show md, show sections, show info, show distill | Emit JSON output; for bib/md/distill implies `--quiet` and errors are also JSON; fresh and cached distill results expose evidence mode and applicable quote/support aggregates, while `--list --json` exposes query metadata; for show distill required for `--all`; mutually exclusive with `--dry-run` |
| `--all` | show distill | Emit every distillation; requires `--json` |
| `--verbose` | show bib | Include `conflicts`, `lookup_log`, and `meta` in JSON output |
| `--writable` | show bib | Emit just the fields dict as JSON; pipe into `puba bib edit --json-file -` |
| `--include-content` | show md | Inline markdown text and sections list into JSON (requires `--json`) |
| `--head N` / `--tail N` | show md | Return at most the first/last N characters; will not split a `<!-- page N -->` marker; in `--json` mode adds `content`, `chars`, `requested_chars`, `total_chars`, `truncated` |
| `--source human\|tool:<name>` | bib edit | Provenance source; both sticky (default: `human`) |
| `--set field=value` | bib edit | Set one field inline; repeatable; `null` deletes |
| `--json-file PATH\|-` | bib edit | Read patch from JSON file or stdin |
| `--clear-review` | bib edit | Set `needs_review=false` and remove `_review_reasons` |
| `--restamp` | bib edit | Force provenance stamp even for fields whose value is unchanged |
| `--what bib\|md\|figures\|state\|distill\|reviews\|all` | clean | What to remove; `all` preserves human reviews, while `reviews` requires `--yes` |
| `--types image,chart,table` | figures | Comma-separated figure types to extract (default: all three) |
| `--embed` | show figure | Add `data_url` field (base64 JPEG data URL, downsampled to ≤2048 px) to JSON output; requires `--json` |
| `--path` | show figure | Print only the absolute JPG path; mutually exclusive with `--json` |

---

## Agent skill

puba ships a bundled [opencode](https://opencode.ai) Agent Skill that teaches
an agent how to use the full `puba` CLI — resolving bib, rendering markdown,
extracting figures, and running distillations.

Install it into opencode's skills directory:

```bash
puba skill export ~/.config/opencode/skills/publication-analysis
```

Print it to stdout:

```bash
puba skill show
```

---

## Markdown rendering

`puba md` sends the PDF to the configured MinerU service by default. A local
subprocess backend remains available through `md.extractor: local`. Both use
MinerU's `pipeline` backend with formula recognition disabled and produce the
same on-disk artifacts. `paper.md` contains YAML frontmatter, a
puba-generated title / author / venue header, MinerU's markdown body with
headings at `##` / `###` / deeper, and page boundaries as HTML comments
(`<!-- page 7 -->`). Section spans are written to `paper.sections.json`.

See [docs/markdown-rendering.md](docs/markdown-rendering.md) for the full
pipeline description, page-numbering semantics (including known quirks around
cover pages and paragraph-spanning page breaks), cover-page filtering behavior,
and the `mineru/` debugging intermediates.

The default service performs model inference outside the puba process. Local
mode downloads ~1.5–3 GB of model weights on first use and can use up to ~8 GB
of memory; a GPU is strongly recommended.

---

## Caching

Each stage records its cache metadata in `<pdf>.puba/.state.json`. A cached
result is reused only when its stage-specific cache key still matches **and**
its output artifact exists and parses. `--force` bypasses the cache.

Status surfaces use four artifact-currency states:

- `current` — the cache key matches and the output artifact exists and parses.
- `stale` — a parseable output exists, but its cache key no longer matches.
- `never-run` — neither a usable state entry nor an output artifact exists.
- `invalid` — an output artifact is unparseable, or a state entry references an
  output artifact that has disappeared.

A malformed or structurally unusable state file is not evidence that an
artifact was never run: when a parseable output remains, it is reported as
`stale` and will be regenerated on the next run.

Bib, markdown, and figures keys include the source PDF and their relevant
prompt/configuration versions. LLM-backed stages include the active backend
and model. A distillation key additionally includes the input-content hash,
resolved query-prompt hash, generation model, verifier model where applicable,
and an `effective_instruction` hash. The latter covers evidence mode,
generation/atomizer/verifier schema and instruction versions, local result and
support/context algorithm versions, and claim/verification limits. Canonical
source and permitted-span inputs are hashed too. Thus changing any
request-affecting setting, including `--verifier-model`, makes an old artifact
stale rather than silently reusing it. Records preserve per-call provenance for
generation, atomization, and verification without storing internal prompts.

New runs write schema v2. Schema-v1 JSON/YAML records and records with no
`schema_version` remain readable through an in-memory compatibility boundary;
read-only commands do not rewrite them. Unknown future versions and records
whose stored aggregates disagree with their items are invalid.

To invalidate all papers for a stage after changing the packaged bibliographic
prompt, bump `prompt_versions.bib_extract` in packaged `puba/config.yaml`; a
project can override that key in `./puba.config.yaml`.
For the md stage, bump `md.mineru_version`. See
[docs/configuration.md](docs/configuration.md#prompt-versions-and-cache-invalidation) and
[docs/distillations.md](docs/distillations.md#caching).

---

## Multi-paper batch

```bash
for f in *.pdf; do puba bib "$f"; done
# human review pass: inspect any bibliographic record flagged with needs_review=true,
# correct conflicts, then re-run puba bib on that paper until it is clean
for f in *.pdf; do puba md "$f"; done
```

`puba md` runs regardless of bib state — a missing or review-flagged bibliographic record
produces a warning on stderr but does not block rendering. The rendered markdown
will use the PDF stem as the title if bib is absent. For strict pipeline control
(exit 3 on unresolved bib), pass `--strict-bib`.

---

## Development

Install the development dependencies with `pip install -e ".[dev]"`. Add the
`local-mineru` extra only for local MinerU tests.

| Suite | Command | Prerequisites |
|---|---|---|
| CI / offline | `pytest tests/ -m "not network and not gpu" -q` | Development dependencies only; this is the GitHub Actions matrix on Python 3.11 and 3.12. |
| GPU / local MinerU | `pytest tests/test_e2e_md_mineru.py tests/test_e2e_figures_mineru.py -m "gpu and not exhaustive" -v` | `pip install -e ".[dev,local-mineru]"`, a CUDA-capable GPU, and enough disk/network access for MinerU's first-run model download. |
| Exhaustive local MinerU | `pytest tests/test_e2e_md_mineru.py -m "gpu and exhaustive" -v` | Same local MinerU/GPU prerequisites; runs the extended fixture sweep. |
| MinerU service | `pytest tests/test_e2e_md_mineru_service.py -v` | A reachable MinerU service at `http://host-localhost:8001`; this test currently asserts the default endpoint. |
| Live Mofka LLM | `pytest tests/test_e2e_distill.py -v` | Internet access and authenticated OpenCode (`opencode providers login`). This makes paid/live model calls and currently contains OpenCode-specific provenance assertions. |

Running `pytest tests/ -v` is intentionally not the default: it combines
offline, network, local-GPU, service, and live-LLM requirements.

`tests/test_e2e_bib.py` is temporarily excluded from the supported command
matrix because its legacy output assertions still expect `bib.yaml` while
production writes `bib.json`; migrating that suite is tracked in
`docs/OBJECTIVE.md`.

---

## Troubleshooting

### `ImportError: libGL.so.1` from MinerU / OpenCV

This section applies only when `md.extractor: local` is selected.

If `puba md` fails with a traceback ending in:

```
File ".../cv2/__init__.py", line 153, in bootstrap
  native_module = importlib.import_module("cv2")
ImportError: libGL.so.1: cannot open shared object file: No such file or directory
```

the cause is that `mineru` declares `opencv-python>=4.11` as a transitive
dependency. puba pins `opencv-python-headless` (which does not need `libGL`),
but pip installs both packages side-by-side into the same `cv2` namespace and
the non-headless build wins. On headless containers (no `libGL.so.1`) this
breaks `import cv2`.

Fix:

```bash
pip uninstall -y opencv-python
pip install --force-reinstall opencv-python-headless
```

This may need to be re-run after any `pip install -U mineru` or a fresh
environment build that re-pulls `opencv-python` as a transitive dependency.

### `CUDA available: False` / `Failed to initialize NVML` after container restart

If GPU is not visible after restarting the container (`nvidia-smi` returns
"Failed to initialize NVML: Unknown Error" and
`torch.cuda.is_available()` returns `False`):

```bash
nvidia-smi          # confirms NVML failure
python3 -c "import torch; print(torch.cuda.is_available())"  # False
```

This is a container GPU passthrough issue unrelated to puba. Fix: restart the
container again. GPU access is restored on a clean container start. `puba md`
will still run on CPU in the meantime (~10 min per 50-page paper vs ~2 min on
GPU).

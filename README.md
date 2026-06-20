# puba — publication analysis

Single-paper CLI: given one PDF, resolve its bibliographic information with full
provenance, render a clean markdown version, and run named LLM distillations
against the abstract, narrative, or full paper.

## Documentation

- [docs/configuration.md](docs/configuration.md) — env vars, models, OpenAI endpoint,
  rate limits, conflict thresholds, classification lists, prompt versions
- [docs/bib-yaml.md](docs/bib-yaml.md) — `bib.yaml` schema, field reference,
  source priority, resolution flow, category enum, provenance entries
- [docs/distillations.md](docs/distillations.md) — defining distillation queries,
  scopes, `prompts/` directory, output schema, caching
- [docs/markdown-rendering.md](docs/markdown-rendering.md) — rendering pipeline,
  page-numbering semantics, cover-page filter, section detection, MinerU intermediates
- [docs/figures.md](docs/figures.md) — figure extraction, manifest schema, `show figures` / `show figure`
- [tests/fixtures/README.md](tests/fixtures/README.md) — fixture licensing and
  criteria for adding new test PDFs

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

> **First run of `puba md`:** MinerU downloads ~1.5–3 GB of model weights into
> `~/.cache/huggingface/` on first use. GPU is strongly recommended; CPU-only
> processing of a 50-page paper takes ~10 minutes.

## Environment

At minimum:

```
OPENAI_API_KEY=rross
OPENALEX_MAILTO=you@anl.gov
```

Put these in a `.env` file at the repo root (already in `.gitignore`). Full
details including optional variables in
[docs/configuration.md](docs/configuration.md#environment-variables).

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
```

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
# inspect bib.yaml; fix any needs_review=true issues
puba md paper.pdf --force
```

---

## Output layout

Each PDF gets its own analysis directory next to it:

```
paper.pdf
paper.puba/
  bib.yaml              # verified bibliographic record + per-field provenance
  paper.md              # MinerU markdown with YAML frontmatter
  paper.sections.json   # section spans {short_name, title, level, start, end}
  paper.figures.json    # figure manifest (after puba figures)
  .state.json           # pdf sha256, stage timestamps, version keys (cache key)
  analyses/             # distillation outputs, one YAML file per named query
  figures/              # per-figure JPG crops + JSON sidecars (after puba figures)
  mineru/               # MinerU intermediates (kept for debugging; removed by puba clean --what md)
    paper.md            # raw MinerU markdown before puba post-processing
    paper_content_list.json
    paper_content_list_v2.json
    paper_middle.json
    paper_layout.pdf
    images/             # MinerU raw figure crops (sha-named *.jpg)
```

If the PDF is on a read-only filesystem, `puba` will error. There is no
auto-fallback output directory; use a writable copy of the PDF.

---

## CLI reference

| Command | What it does |
|---|---|
| `puba bib <pdf>` | Resolve and write bibliographic information; exit 3 if `needs_review=true` |
| `puba bib edit <pdf>` | Apply a JSON field patch to bib.yaml with sticky provenance |
| `puba md <pdf>` | Render clean markdown; exit 3 if `bib.yaml` is missing or `needs_review=true` |
| `puba figures <pdf>` | Extract per-figure JPG crops and manifest from MinerU layout output |
| `puba distill <pdf>` | Run all defined distillation queries |
| `puba distill <pdf> --only NAME` | Run one named distillation |
| `puba distill <pdf> --list` | List defined queries and their cached status |
| `puba clean <pdf>` | Remove cached outputs |
| `puba show bib <pdf>` | Read resolved bib fields + provenance; errors if bib not resolved |
| `puba show md <pdf>` | Print rendered markdown to stdout; errors if not yet rendered |
| `puba show sections <pdf>` | List detected sections with short names and full titles |
| `puba show section <pdf> NAME` | Print the markdown content of a named section (includes heading and subsections) |
| `puba show figures <pdf>` | List extracted figures (id, page, type, size, caption) |
| `puba show figure <pdf> ID` | Show detail for one figure; `--path` prints JPG path; `--json --embed` adds base64 data URL |
| `puba show info <pdf>` | Combined status: bib summary, stage cache, distillations |
| `puba show distill <pdf> NAME` | Print the raw text of a named distillation |
| `puba show distill <pdf> NAME --json` | Emit distillation text + provenance as JSON |
| `puba show distill <pdf> --all --json` | Emit all distillations as JSON |
| `puba config show` | Print fully resolved configuration + source of each key |
| `puba config validate` | Validate regexes, enums, required env vars |
| `puba config init` | Copy packaged config.yaml into CWD as puba.config.yaml |

### Key flags

| Flag | Applies to | Effect |
|---|---|---|
| `--force` | bib, md, distill | Re-run even if stage is cached |
| `--model MODEL` | bib, distill | Override LLM model for this invocation (e.g. `'Claude Sonnet 4.6'`). Invalidates cache if different from cached model. |
| `--no-llm` | bib | Skip LLM title extraction; use PDF cover-page heuristic only |
| `--bibtex FILE` | bib | Provide a `.bib` file as a fallback metadata source. Must exist, be a file (not a directory), and contain at least one parseable entry; otherwise the stage fails. |
| `--dry-run` | bib, md | Print what would run without running it |

| `--only NAME` | distill | Run only the named distillation (repeatable) |
| `--list` | distill | List all defined queries with cached status |
| `--json` | bib, md | Emit a JSON result object on stdout; implies `--quiet`; errors are also JSON. Mutually exclusive with `--dry-run`. |
| `--json` | show bib, show md, show sections, show info, show distill | Output as JSON instead of Rich table; required for `--all` in show distill |
| `--all` | show distill | Emit every distillation; requires `--json` |
| `--verbose` | show bib | Include `conflicts`, `lookup_log`, and `meta` in JSON output |
| `--writable` | show bib | Emit just the fields dict as JSON; pipe into `puba bib edit --json-file -` |
| `--include-content` | show md | Inline markdown text and sections list into JSON (requires `--json`) |
| `--source human\|tool:<name>` | bib edit | Provenance source; both sticky (default: `human`) |
| `--set field=value` | bib edit | Set one field inline; repeatable; `null` deletes |
| `--json-file PATH\|-` | bib edit | Read patch from JSON file or stdin |
| `--clear-review` | bib edit | Set `needs_review=false` and remove `_review_reasons` |
| `--what bib\|md\|figures\|state\|distill\|all` | clean | What to remove |
| `--types image,chart,table` | figures | Comma-separated figure types to extract (default: all three) |
| `--embed` | show figure | Add `data_url` field (base64 JPEG data URL, downsampled to ≤2048 px) to JSON output; requires `--json` |
| `--path` | show figure | Print only the absolute JPG path; mutually exclusive with `--json` |

---

## Markdown rendering

`puba md` uses MinerU (`pipeline` backend, formula recognition disabled)
to extract and render `paper.md`. `paper.md` contains YAML frontmatter, a
puba-generated title / author / venue header, MinerU's markdown body with
headings at `##` / `###` / deeper, and page boundaries as HTML comments
(`<!-- page 7 -->`). Section spans are written to `paper.sections.json`.

See [docs/markdown-rendering.md](docs/markdown-rendering.md) for the full
pipeline description, page-numbering semantics (including known quirks around
cover pages and paragraph-spanning page breaks), cover-page filtering behavior,
and the `mineru/` debugging intermediates.

**First run:** MinerU downloads ~1.5–3 GB of model weights to
`~/.cache/huggingface/`. GPU strongly recommended (~2 min for a 50-page paper
on NVIDIA GB10); CPU-only is ~10 min for the same paper.

**Memory:** MinerU uses up to ~8 GB of RAM during extraction (model weights +
working buffers). On the NVIDIA GB10 (128 GB unified memory) this is
comfortably within budget; on a standard workstation ensure at least 16 GB
total RAM is available.

---

## Caching

Each stage is cached in `<pdf>.puba/.state.json`, keyed by PDF sha256,
`prompt_version`, and `tool_version`. A run is a no-op when all three match.
`--force` bypasses the cache. `.state.json` corruption is treated as "no prior
run" and the stage re-runs cleanly. Cached no-ops are shown as `(cached)` next
to the output path in non-JSON output.

To invalidate all papers for a stage after changing a prompt, bump
`prompt_versions.bib_extract` in `config.yaml` or `puba.config.yaml`.
For the md stage, bump `md.mineru_version`. See
[docs/configuration.md](docs/configuration.md#prompt-versions-and-cache-invalidation).

---

## Multi-paper batch

```bash
for f in *.pdf; do puba bib "$f"; done
# human review pass: inspect any bib.yaml flagged with needs_review=true,
# correct conflicts, then re-run puba bib on that paper until it is clean
for f in *.pdf; do puba md "$f"; done
```

`puba md` refuses to render until `bib.yaml` exists and is not flagged for
review, so the first loop and the human-review pass are mandatory before the
second loop.

---

## Development

```bash
# Offline tests only (no network calls)
pytest tests/ -m 'not network'

# All tests including live API calls
OPENAI_API_KEY=rross pytest tests/ -v

# End-to-end bib resolution tests only
OPENAI_API_KEY=rross pytest tests/test_e2e_bib.py -v
```

---

## Troubleshooting

### `ImportError: libGL.so.1` from MinerU / OpenCV

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

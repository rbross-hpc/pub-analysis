# Objective

`puba` is a single-paper CLI: given one PDF, it resolves bibliographic
metadata with full provenance, renders clean markdown, extracts
figures, and runs named LLM "distillation" queries against the
abstract, narrative, or full paper text. See `README.md` for the full
CLI reference and `docs/design-log.md` for the historical design
rationale (architecture, source-priority chain, caching strategy,
etc.) — treat `docs/design-log.md` as a *retrospective log*, not a
live task list; the current priorities are stated here instead.

This is a long-lived tool with no final "done" state. The immediate priority
is distillation schema v2: replace misleading evidence-verification terminology
with deterministic quote-presence results, then add model-based claim-support
assessment. The JSON, cache-status, evidence, summarize, and edit-log
milestones that follow define the foundation already delivered. Where the
schema-v1 evidence milestone conflicts with this immediate priority, the v2
plan below is authoritative.

## Immediate priority - Quote presence and claim-support assessment

The existing `verified` evidence state proves only that a model-supplied quote
occurs exactly once in the permitted source. It does not prove that the quote
supports the answer. Distillation schema v2 must represent these as separate
facts:

1. **Quote presence** is deterministic and computed locally.
2. **Claim support** is a structured assessment by a separate LLM call.
3. **Human review**, if added later, is a third independent state and is not
   part of this objective.

Claim support must always be described as model-assessed, not as formal proof
or human verification.

### Locked design decisions

- Newly written distillation records use `schema_version: 2`.
- Claim assessment is enabled by default for all distillation queries.
- Individual queries may explicitly choose `evidence_mode: none`, `quotes`, or
  `claims`.
- Legacy `evidence: true` maps to quote-only mode and `evidence: false` maps to
  no evidence. A query defining both fields is invalid.
- Claim mode retains all proposed quotes and checks their presence before any
  semantic assessment.
- Claims are assessed independently with one verifier request per claim.
- The verifier has a separate backend-specific `claim_verify` model role and
  optional per-query/CLI override. It may resolve to the same model identifier
  as answer generation, but remains a separate request and provenance role.
- Support labels are `supported`, `partially_supported`, `unsupported`,
  `contradicted`, `insufficient_evidence`, and locally assigned
  `not_assessed`.
- Exhausted answer-generation, atomization, or verifier failures abort the run
  atomically. Existing output and state remain untouched. Semantic outcomes
  such as `unsupported` or `contradicted` are successful results and persist.
- Schema-v1 records remain readable and are never rewritten by read-only
  commands. Unsupported future schema versions are invalid rather than guessed.

### Schema-v2 shape

A claim-enabled record has this conceptual shape (ordinary provenance and
distillation fields are omitted here for brevity):

```json
{
  "schema_version": 2,
  "evidence_mode": "claims",
  "output": "The persisted answer.",
  "claims": [
    {
      "id": "c1",
      "claim": "Mofka achieves higher throughput than Kafka.",
      "evidence": [
        {
          "id": "c1e1",
          "quote": "Mofka achieves up to 8x higher throughput...",
          "quote_status": "present",
          "offset": 8124,
          "section": "Performance Evaluation",
          "page": 13
        }
      ],
      "support_assessment": {
        "status": "supported",
        "rationale": "The passage directly reports the claimed comparison.",
        "evidence_ids": ["c1e1"]
      }
    }
  ],
  "quote_presence_status": "complete",
  "quote_presence_counts": {
    "total": 1,
    "present": 1,
    "not_found": 0,
    "ambiguous": 0
  },
  "claim_support_status": "supported",
  "claim_support_counts": {
    "total": 1,
    "supported": 1,
    "partially_supported": 0,
    "unsupported": 0,
    "contradicted": 0,
    "insufficient_evidence": 0,
    "not_assessed": 0
  }
}
```

Per-quote `quote_status` values are:

- `present` - exactly one match exists in the permitted canonical-source span;
  local offset, section, and approximate page may be recorded.
- `not_found` - no exact match exists in the permitted span.
- `ambiguous` - multiple exact matches exist, so no unique location can be
  attributed.

Top-level `quote_presence_status` is `complete` only for a non-empty set where
every quote is uniquely present; otherwise it is `partial`. Counts retain the
distinction between empty, missing, and ambiguous evidence.

Top-level `claim_support_status` is:

- `supported` when every non-empty claim is supported;
- `contradicted` when any claim is contradicted;
- `unsupported` when every non-empty claim is unsupported;
- `partial` for mixed, partial, insufficient, or unassessed results;
- `not_applicable` when atomization finds no factual claims.

### Query and model configuration

Add a global default and per-query override:

```yaml
distill:
  default_evidence_mode: claims

  queries:
    summary:
      scope: abstract
      evidence_mode: claims
      prompt: Summarize the paper.

    format_only:
      scope: abstract
      evidence_mode: none
      prompt: Reformat the abstract as a table.
```

Add `models.<backend>.claim_verify`, query-level `verifier_model`, and CLI
`--verifier-model`. Resolution order is CLI override, query override, then the
active backend's `claim_verify` role. The atomizer uses the answer-generation
model; semantic assessment uses `claim_verify`.

### Processing pipeline

Implement claim mode as an ordered, atomic pipeline:

1. Generate and post-process the answer normally. The exact persisted answer
   is authoritative for subsequent atomization.
2. Make a structured atomizer call with that answer and the permitted canonical
   source. Require a list of non-blank atomic claims, each with zero or more
   non-blank exact quotes. Assign stable claim/evidence IDs locally; never trust
   model-generated IDs.
3. Locate every quote exactly in the canonical permitted span. Retain missing
   and ambiguous quotes, but never send them to the semantic verifier as source
   evidence.
4. For each claim with at least one present quote, build deterministic context
   by expanding to paragraph boundaries, clipping to the permitted scope,
   merging overlaps, preserving source order, and never truncating the quote.
5. Send each claim and its present quote/context records to the verifier. Its
   JSON response must contain one allowed label, a non-blank bounded rationale,
   and only known evidence IDs. A claim with no present evidence is assigned
   `not_assessed` locally without an LLM call.
6. Aggregate quote and support statuses, construct the complete record in
   memory, atomically write it, then update state.

Quote-only mode retains the schema-v1 model response contract (`answer` plus a
flat quote list) but writes schema-v2 quote terminology. No-evidence mode keeps
free-text generation and omits quote/claim fields.

### Canonical source and scope boundaries

Refactor scope construction to return model content plus the canonical source,
allowed character span, sections, and source hashes. Matching and verifier
context must obey these boundaries:

- `abstract` - literal bibliographic abstract;
- `full` - complete raw `paper.md`;
- `section` - only the selected section's `[start_offset, end_offset)` span;
- `narrative` - raw `paper.md` only up to the first configured stripped
  trailing section.

Page markers may be removed from model content but remain in canonical source
for offset/page derivation. Bibliographic prompt headers are not evidence.

### Limits and request accounting

Add validated configuration limits, initially:

```yaml
distill:
  claims:
    max_claims: 20
    max_evidence_per_claim: 4
    max_quote_chars: 4000
    max_atomizer_input_tokens: 100000
  verification:
    max_context_tokens_per_claim: 4000
    max_total_input_tokens: 50000
    max_rationale_chars: 1000
```

Token accounting must cover internal instructions, answer, canonical source,
and verifier contexts. Exact quotes are never truncated. Reject over-budget
requests before issuing the affected LLM call.

### Cache identity and provenance

The effective cache identity must include evidence mode, generation and
verifier models, atomizer/verifier instruction and response-schema versions,
schema-v2 local-result version, support-taxonomy version, context algorithm
version, all claim/context limits, and canonical source/section inputs. The new
global default intentionally makes existing plain-query cache entries stale.

Persist structured call provenance for answer generation, atomization, and
verification: backend, model, instruction version, response-schema version,
input-token estimate, and verifier request count. Continue to store internal
prompts by version identifier rather than persisting their full text.

### Schema-v1 compatibility

Add a schema-aware artifact compatibility boundary. New writes are v2; v1
files remain unchanged on disk. Current read/presentation code may normalize a
v1 record in memory using:

- item `status: verified` -> `quote_status: present`;
- `status: unverified, reason: no_match` -> `quote_status: not_found`;
- `status: unverified, reason: ambiguous_match` ->
  `quote_status: ambiguous`.

Recompute aggregate quote presence from items rather than mapping the lossy v1
`evidence_status` directly. Read-only `show` and `summarize` operations must
never rewrite an artifact. Add an explicit migration command later only if a
concrete need emerges.

### CLI and summary surfaces

- `puba distill --list` and `puba show info` report evidence mode, quote
  presence, claim support, and verifier model as separate fields/columns.
- Fresh and cached `puba distill --json` results expose quote/support aggregate
  statuses and counts.
- Interactive warnings describe missing/ambiguous quotes and partial,
  unsupported, contradicted, insufficient, or unassessed claims factually.
- Plain `puba show distill NAME` remains answer-only for pipe compatibility.
- JSON show commands expose complete claims, quote records, support
  assessments, and provenance.
- `puba summarize` renders every claim, rationale, linked quote, and source
  location. Its attention section flags non-complete quote presence and any
  claim result other than fully supported/not-applicable.

Currency (`current`, `stale`, `never-run`, `invalid`), quote presence, claim
support, and any future human review status remain orthogonal.

### Implementation order

1. Record schema-v2 terminology, compatibility, and trust boundaries in a new
   ADR that refines ADR 0004 without rewriting its history.
2. Add schema-aware v1/v2 artifact validation and in-memory normalization.
3. Replace quote-verification vocabulary with quote-presence vocabulary.
4. Add evidence mode, defaults, legacy aliases, and configuration validation.
5. Refactor scope construction to expose canonical permitted spans.
6. Implement atomization, stable local IDs, quote matching, and aggregates.
7. Implement deterministic verifier context and per-claim support assessment.
8. Add verifier model resolution, CLI override, cache identity, and provenance.
9. Update list/show/info/summarize surfaces and the bundled agent skill.
10. Update README, configuration, distillation documentation, design log, and
    ADR cross-references.
11. Run the full offline suite and an opt-in live Mofka structural test. Live
    tests assert schema/provenance, not a particular model-dependent label.

### Required tests

Cover every quote and aggregate state; every support label and aggregate;
empty claims/quotes; missing, ambiguous, and out-of-scope quotes; atomizer and
verifier malformed JSON/retries; unknown evidence IDs; deterministic context
clipping/merging; exact-quote preservation; strict atomic failure behavior;
schema-v1 compatibility without file mutation; schema-v2 validation; all
request-affecting cache settings; CLI/summary presentation; and live structural
provenance. Unsupported or contradicted model assessments must persist as
successful, current artifacts.

## Milestone 1 — JSON-capable artifact I/O layer

Add a small internal helper layer for reading and writing generated
artifacts (`bib.yaml`/`bib.json`, `analyses/<name>.yaml`/`.json`).
Readers must prefer the `.json` form and transparently fall back to
the legacy `.yaml` form when `.json` is absent. Writers must support
producing either format, but this milestone does **not** change which
format any existing call site actually writes — that switch is
Milestone 2. Get the compatibility shim right and unit-tested in
isolation first, against both an old-format and a new-format fixture,
before anything downstream depends on it.

**If both a `.json` and a legacy `.yaml` form of the same artifact
exist on disk** (this should only happen if a prior write was
interrupted after writing JSON but before removing the YAML — see
Milestone 2), the reader must treat `.json` as authoritative and
ignore the `.yaml` file's content, but must still surface this as an
attention-worthy condition (a warning; and, once Milestone 5 lands, a
line in `puba summarize`'s "Missing / Needs Attention" section) rather
than silently ignoring the stale file forever.

There is no user-facing migration command. Once Milestone 2 lands, a
paper's artifact ends up in exactly one of three states: legacy
`.yaml` only (untouched since before this change), current `.json`
only (rewritten at least once since), or — transiently, only after an
interrupted write — both, which is handled per the paragraph above.
No code path ever writes both formats as part of a single successful
write.

Human-authored configuration (`config.yaml`, `puba.config.yaml`,
`prompts/*.yaml`) is explicitly **out of scope** for this migration —
it stays YAML. Only tool-generated artifacts move to JSON.

## Milestone 2 — Migrate generated artifacts to JSON

Switch `puba bib` and `puba distill` to write `bib.json` and
`analyses/<name>.json` respectively, using the Milestone 1 writer.
Order of operations for each write: (1) atomically write the new
`.json` file — using the same atomic-rename primitive `puba/io.py`
already uses for text/YAML — then (2) delete the corresponding legacy
`.yaml` file if one existed. If the process is interrupted between
those two steps, the result is the "both exist" case Milestone 1
already handles on next read; it self-heals the next time that
artifact is rewritten. `paper.sections.json`, `paper.figures.json`,
and `.state.json` are already JSON and need no format change, but
should be swept for any code that assumed sibling artifacts were
YAML.

Add a top-level `"schema_version"` integer field to both `bib.json`
and each `analyses/<name>.json` record (bib's existing `_meta` block
already has a precedent for this — see `puba/sidecar.py`). Version 1
is the JSON-format equivalent of the pre-migration YAML shape (plus
whatever Milestone 3/4 fields are already present at the time this
lands). A reader encountering no `schema_version` (i.e., a record
written before this concept existed) treats it as implicitly version
1.

Every CLI command that reads these artifacts (`show bib`, `show
info`, `show distill`, `bib edit`, `distill --list`, `clean`, etc.)
must keep working unchanged against both an old all-YAML paper
directory and a freshly-migrated one, via the Milestone 1 reader.
Extend the existing test fixtures (tests already write synthetic
`bib.yaml` fixtures) to cover both the legacy and new formats, rather
than rewriting every fixture to JSON outright.

## Milestone 3 — Accurate cache/artifact status

Before building anything that depends on "is this artifact current,"
harden the existing cache-validity checks, and introduce one shared
status vocabulary used everywhere status is reported (Milestone 3
itself, `puba distill --list`, `puba show info`, and Milestone 5's
`puba summarize`):

```
current    — cache key matches, output artifact exists and parses
stale      — output artifact exists but cache key no longer matches
             (input, prompt, model, or config changed since)
never-run  — stage has not been run yet (no state entry, no output)
invalid    — output artifact exists but fails to parse, or a state
             entry references a missing output artifact
```

Concretely:
- Verify the corresponding output artifact actually still exists on
  disk and parses before trusting `.state.json` — extend the
  hardening `is_distill_current` already has to `is_bib_current`,
  `is_md_current`, and `is_figures_current`. A missing or unparseable
  output artifact is `invalid`, not silently treated as `never-run`.
- Fix `puba distill`'s cache key: today only the raw query prompt
  text is hashed, separately from a hash of the input content. Both
  must remain part of the key, plus a new **effective-instruction
  hash** covering everything else that affects the actual request
  sent to the model: `max_chars`, the system/format-instruction
  version identifier (see Milestone 4), and — once Milestone 4
  lands — whether evidence was requested and the evidence
  response-schema version. Canonicalize this as a stable JSON object
  (sorted keys) before hashing, so the hash is reproducible across
  runs. This is already a latent bug independent of the evidence
  feature; fix it here so Milestone 4 doesn't have to work around it.
- `puba distill --list`, `puba show info`, and any other status
  surface must report one of the four states above through a single
  shared helper (not duplicate ad hoc "does the file exist" checks),
  reusing the same check as `run_query`. `puba show info`'s current
  `rendered`/`extracted`-style file-existence-only labels for
  bib/md/figures should be replaced by this same shared vocabulary in
  this milestone, since Milestone 5 depends on `show info` already
  reporting accurately.
- `needs_review` (bib) and `evidence_status` (distill, Milestone 4)
  are orthogonal to this four-state currency vocabulary, not values
  of it — an artifact can be simultaneously `current` and
  `needs_review: true`, or `current` and `evidence_status: partial`.
  Report them as separate fields alongside the currency status, never
  folded into it.

This milestone exists because both the evidence feature and the
summarize command need a trustworthy, uniformly-reported notion of
"is this actually current," which does not exist yet.

## Milestone 4 — Evidence-backed distillations

**Historical schema-v1 contract.** This milestone documents the delivered
quote-evidence foundation. The immediate schema-v2 priority above supersedes
its `verified`/`unverified` terminology, opt-in default, aggregate status, and
new-write schema. Its canonical-source, atomic-failure, exact-matching, and
no-human-signoff principles remain in force.

Add an opt-in `evidence: true` field to a distillation query
definition. When set, `puba distill` requests a structured JSON
response — `{"answer": "...", "evidence": [{"quote": "..."}, ...]}`
— instead of free text, and independently verifies each quotation
against the canonical source before persisting it — never trusting
the model's own offsets, page numbers, or section attribution.

**Canonical source per scope** (this is the exact string each quote
is matched against, and the coordinate system offsets are reported
in):
- `abstract` scope — the literal value of `bib.json`'s `abstract`
  field (from the compatibility layer, so this also works for a
  paper whose bib record is still legacy `bib.yaml`). Offsets are
  character offsets into that string. There is no page/section for
  this scope; both are always `null`.
- `narrative` / `full` / `section` scope — the raw, unmodified
  `paper.md` file on disk (not the bib-header-prefixed, page-marker-
  stripped, or trailing-section-stripped text actually sent to the
  model as the prompt's content — the model may quote from what it
  was shown, which is a transformation of `paper.md`, so verification
  re-locates each quote in the untransformed file). For `section`
  scope specifically, a quote must additionally fall within that
  section's own `[start_offset, end_offset)` span from
  `paper.sections.json`; a quote that matches `paper.md` only
  *outside* that span counts as unverified with reason `no_match`
  for this scope. Offsets are character offsets into `paper.md`.
  Section is derived by locating the containing entry in
  `paper.sections.json`; page is derived from the nearest preceding
  `<!-- page N -->` marker in `paper.md`.

**Structured-response failure handling:** if, after the existing
retry policy `puba/llm/openai_client.py` already applies, the model's
response still isn't valid JSON matching the required shape (missing
`answer`, `evidence` not a list, an evidence item missing `quote`,
etc.), treat this the same as any other LLM-call failure: return
`status: error`, do **not** write any output file, and do **not**
touch the existing cached artifact (if one exists) or `.state.json`.
This is different from the "quote doesn't match the source" case
below, which does still produce a valid persisted result.

**Empty evidence list:** a structurally valid response with a
non-empty `answer` but a zero-length `evidence` array is not an
error. Persist it with `evidence: []` and `evidence_status:
unverified` (same status value as a response where every item failed
to match — an empty-evidence and an all-unmatched-evidence record are
indistinguishable to a caller and should surface identically), and
surface the same warning as any other unverified case.

**Verification outcome, once a structurally valid response is in
hand:**
- If a quoted passage cannot be matched exactly and unambiguously
  against the canonical source for its scope (see above), do **not**
  fail the run and do **not** discard the produced answer. Persist
  the record anyway with that evidence item marked `unverified`
  (reason `no_match` if the quote isn't found at all — including
  "found outside the allowed section span" for `section` scope —
  `ambiguous_match` if it matches more than once within the allowed
  range), and surface a clear warning (stderr for interactive use, a
  structured field in `--json` output) — never silently drop it.
- A record with any unverified item, or with an empty evidence list,
  gets an overall `evidence_status: partial`. A record where every
  item verified gets `evidence_status: verified`. A record from a
  query where `evidence` was not requested at all has no
  `evidence_status` field (omitted, not `null` — this distinguishes
  "evidence wasn't asked for" from "evidence was asked for and came
  back empty").
- Matched evidence items also record exact source offsets, the
  containing section (when known), and an approximate page number as
  described per-scope above. Offsets and the quote text are
  authoritative; page numbers are a navigation aid only (see
  `docs/markdown-rendering.md`'s existing caveats about page-marker
  placement).

**`max_chars` applies only to `answer`,** exactly as it already
applies to the whole response text in the non-evidence case today.
Evidence quotes are never truncated. The existing post-processing
step (`_post_process` in `puba/distill/run.py`) runs on `answer` only
when evidence is requested; the response's JSON structure itself is
never subjected to character-truncation.

Add a `"schema_version"` to the persisted evidence-enabled record
(same field introduced for all `analyses/<name>.json` records in
Milestone 2) so a future format change to the evidence shape doesn't
need to guess whether a given file predates it.

Persist the resolved, user-authored query prompt text alongside the
result (not just its hash) — Milestone 5's summary needs to display
the exact prompt that produced each distillation. This applies to
every distillation record going forward (evidence-enabled or not),
not only evidence-enabled ones. Do not persist puba's internal
system/format instructions verbatim; assign them a version identifier
(e.g. `"instruction_version": "distill-v1"` for the plain case,
`"distill-evidence-v1"` for the evidence case) and persist that
instead — this identifier is also part of Milestone 3's
effective-instruction cache-key hash.

Trust model (already decided during planning, do not relitigate):
evidence remains **model-generated**, not a human-verification
workflow — there is no "verified" sign-off step for puba, unlike the
`wake` project's heavier provisional→proposed→verified lifecycle.

This is the single largest milestone. Get the response schema and
verification logic right; Milestone 3 already fixed the cache-key gap
this feature would otherwise have hit.

**Explicitly deferred, do not build:** map-reduce distillation for
inputs exceeding the model's context window. This has been discussed
and intentionally shelved; do not let evidence work motivate
revisiting it.

## Milestone 5 — `puba summarize`: deterministic sanity-check artifact

Add a new command, `puba summarize <pdf>`, that renders a single
Markdown file, `<pdf-stem>.summary.md`, written **next to the PDF**
(not inside `<pdf-stem>.puba/`) using the PDF's own filename stem so
multiple papers in the same directory don't collide. It is generated
**only when explicitly invoked** — never as a side effect of `bib`,
`md`, `figures`, or `distill`.

**"Deterministic" means:** the command makes no LLM call and no
network call — it only reads and formats data already computed by
prior stages, using the Milestone 3 status helpers to determine what
is current, stale, missing, or invalid. Given an unchanged set of
input artifacts, two invocations produce byte-identical output with
the sole exception of the frontmatter's own generation timestamp
(which necessarily reflects wall-clock time at generation). This is
the same sense in which `puba bib`/`puba md` are "deterministic" for
a given cached input — not "byte-identical including the clock."

The output file is written using the same atomic-write primitive
(`atomic_write_text` in `puba/io.py`) the rest of the codebase already
uses, so a rerun replaces it cleanly with no partial-write window.

**Exit behavior:** `puba summarize` exits `0` whenever it successfully
produces a report, regardless of how much content ends up in
"Missing / Needs Attention" — a paper with no stages run yet still
gets a (mostly-empty, mostly-attention-flagged) report, not an error.
It exits non-zero only for a precondition it cannot work around at
all: the given PDF path doesn't exist, or the output path can't be
written (e.g. permission denied, read-only filesystem — consistent
with the existing behavior documented in README.md's "Output layout"
section for other commands).

Required contents:
- An OKF-style YAML frontmatter block (artifact type, generation
  timestamp, source PDF, puba tool version, and each stage's status
  using Milestone 3's four-state vocabulary).
- A bibliography section (from `bib.json`, or a note that no bib
  record exists yet).
- A processing-status summary (bib/md/figures/distill stage status,
  using the same Milestone-3-hardened status logic `show info --json`
  itself now uses, per Milestone 3).
- A figures section (counts, captions, from `paper.figures.json`, or
  a note that no figures have been extracted).
- For every distillation on disk: the user-authored query prompt text
  (Milestone 4) and model used, the answer text, and — when the
  record has an `evidence_status` field at all — its evidence list,
  including the `partial`/`unverified` state rather than hiding it.
  A distillation record written before Milestone 4 (no persisted
  prompt text, e.g. an un-migrated legacy record) is rendered with
  its prompt shown as "not recorded (generated before prompt
  persistence was added)" rather than omitted or left blank, and
  this itself is one of the conditions flagged in "Missing / Needs
  Attention" below.
- A prominent **"Missing / Needs Attention"** section surfacing: any
  stage that is `never-run` or `stale` (per Milestone 3), any stage
  that is `invalid` (failed to parse), `needs_review: true` on the
  bib record, any distillation with `evidence_status: partial`, any
  distillation missing persisted prompt text (see above), and any
  artifact where a legacy `.yaml` and current `.json` form were both
  found on disk (see Milestone 1's transient-coexistence case).

This command is meant as a human-facing sanity check across
everything puba has produced for one paper, so favor completeness and
clear structure over brevity in this first version.

## Milestone 6 — Remaining reliability item

- Preserve `_edit_log` across `puba bib --force` instead of silently
  discarding accumulated edit history when bib is regenerated from
  scratch.

## Explicitly out of scope for the immediate priority

- **Map-reduce distillation** (see Milestone 4) — deferred.
- **`puba md edit` / `puba figures edit`** — no concrete need has
  emerged; do not build these speculatively. `puba bib edit` is
  staying as-is; it enforces validation, sticky provenance, and an
  audit log, which a plain YAML/JSON edit would not.
- **`hl-gen` porting** — already complete (done in a separate
  project); not part of this codebase's work.
- **GPU figure-extraction regression tests, downsampling notices,
  figure-quality metadata, `narrative_strip_sections` deep-merge
  ergonomics, bib model name in the cache key** — real backlog items,
  genuinely lower priority than the above; pick these up only after
  the streamed-completion priority is complete, and only if there is
  no other coherent next unit of work.
- **CI/lint tooling (ruff, mypy)** — not currently configured for this
  project and not part of this objective; do not add it as a side
  effect of unrelated work.

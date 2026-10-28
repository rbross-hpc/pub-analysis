# Archived Objective

> **Archive notice:** This document preserves the complete former
> `docs/OBJECTIVE.md` planning record. It is historical and must not be used as
> the current task list. See `docs/OBJECTIVE.md` for the living roadmap.

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
is replacing the OpenAI-shaped OpenCode bridge with an internal, native
one-shot LLM module. The module must support both native OpenCode and the
existing direct OpenAI-compatible backend without exposing either provider's
client protocol to puba workflows. The JSON, cache-status, schema-v2 evidence,
summarize, and edit-log milestones that follow define the foundation already
delivered.

## Immediate priority - Native one-shot LLM module

Puba's LLM usage consists entirely of independent one-shot requests: one
system instruction, one user input, and one completed text or structured
result. The current OpenCode backend presents a synthetic
`OpenAI().chat.completions.create()` interface, including a one-chunk stream,
even though it actually creates a disposable OpenCode session and makes one
native synchronous request. Replace that compatibility facade with an honest
internal API while retaining direct OpenAI-compatible access for Argo and
API-key workflows.

### Locked design decisions

- The implementation is internal to puba. It is designed with a clean boundary
  so it can be extracted later, but it is not a separately published package.
- The supported backends are `opencode` and `openai`.
- Rename `llm.backend: bridge` and `models.bridge` to `opencode`. Do not provide
  a `bridge` compatibility alias. Configuration still using `bridge` is invalid
  and receives an actionable migration error.
- OpenCode uses its native HTTP session/message API. It does not use an
  OpenAI-compatible facade.
- OpenCode is managed locally by puba. Attached external-server mode is not
  part of the initial implementation.
- One managed OpenCode process is reused for the lifetime of the puba process.
  Each model request creates and deletes an independent disposable session.
- Direct OpenAI-compatible mode remains supported inside the same one-shot
  abstraction. Its existing streaming transport may remain internal for Argo
  compatibility, but callers receive only completed results.
- Structured calls request JSON text, parse it locally, and run a caller-supplied
  puba validator inside the retry boundary. Native OpenCode JSON Schema mode is
  deferred.
- Model-role and override resolution remain puba workflow concerns. The
  one-shot module receives an already resolved model identifier.
- OpenCode model identifiers must use explicit `provider/model` syntax. Direct
  OpenAI-compatible model names are sent unchanged.
- The public interface is synchronous and thread-safe. Async calls, public
  token streaming, persistent conversations, and parallel claim verification
  are deferred.
- Valid semantic outcomes such as `unsupported`, `contradicted`, and
  `insufficient_evidence` are successful structured responses. Validation
  checks the response contract, not whether its conclusion is positive.

### Internal module boundary

Use this initial layout:

```text
puba/llm/
  oneshot.py       Public client, results, errors, retries, and JSON handling
  opencode.py      Managed OpenCode process and native session transport
  openai.py        Direct OpenAI-compatible adapter
```

`oneshot.py` exposes a small client rather than provider-specific objects:

```python
client.text(
    system=system_prompt,
    user=user_prompt,
    model="openai/gpt-5.6-sol",
)

client.json_object(
    system=system_prompt,
    user=user_prompt,
    model="openai/gpt-5.6-sol",
    validate=validator,
)
```

Puba's configuration layer constructs and owns one process-wide client for the
active backend. Changing backend in a process closes the old client before
constructing the new one. An `atexit` hook closes the active client.

Calls return a small normalized result object with:

- `value` - completed text or parsed JSON value;
- `backend` and resolved `model`;
- normalized token usage and cost when available;
- provider request/message identifiers when available;
- finish reason when available.

Callers must not receive OpenAI stream chunks, OpenCode message-part objects, or
raw provider responses. Raw provider responses and internal system prompts are
not persisted.

### Managed OpenCode runtime

The native OpenCode adapter must:

1. Locate the configured `opencode` executable and start it lazily with
   `opencode serve --pure --hostname 127.0.0.1 --port 0`.
2. Generate a random password, expose the server only on loopback, and use HTTP
   Basic Auth for every request.
3. Run the child in an isolated temporary working directory while inheriting
   user-level OpenCode credentials.
4. Inject restrictive OpenCode configuration that disables plugins and project
   instructions, selects a minimal agent, and denies all permissions.
5. Capture a bounded amount of startup output, discover the announced URL, and
   require a successful `/global/health` response before use.
6. Serialize startup and shutdown with a lock. Once healthy, support concurrent
   synchronous requests through independent sessions.
7. For every completion, create a session with a catch-all deny permission,
   post one synchronous native message with no tools, and concatenate returned
   text parts in source order.
8. Split the required `provider/model` identifier into OpenCode's `providerID`
   and `modelID` fields without silently defaulting a provider.
9. On timeout, attempt to abort the session. Always attempt to delete the
   session in `finally`.
10. Close HTTP resources and terminate the managed child at shutdown, escalating
    to kill after a bounded wait and cleaning the temporary directory.

A session-cleanup failure must not replace a successful result or mask the
original request exception. Startup and protocol diagnostics should include
bounded, sanitized server output without exposing credentials.

### Direct OpenAI-compatible adapter

The direct adapter retains support for Argo, OpenAI, and local compatible
endpoints using `OPENAI_BASE_URL` and `OPENAI_API_KEY`. It may continue to use
the existing streaming request internally, fully assembling the stream before
returning. Streaming is not part of the public one-shot API.

The adapter sends the resolved model unchanged and normalizes completed text,
usage, request ID, and finish reason into the same result type as OpenCode. Do
not expose temperature initially; all current puba calls use zero. Provider
authentication and SDK objects remain adapter details.

### Structured output and validation

`json_object` performs these operations within one retry attempt:

1. Add a versioned instruction requiring one JSON object and no surrounding
   prose.
2. Request and completely receive the model output.
3. Strip whitespace and one optional enclosing Markdown JSON fence. Do not
   heuristically extract JSON from arbitrary prose.
4. Parse with `json.loads`.
5. Require a JSON object and run the supplied puba validator.
6. Return the parsed object only when parsing and validation both succeed.

The one-shot module is schema-agnostic. Bib extraction, quote response,
atomizer, and verifier schemas remain defined and versioned by their puba
workflows. Their validators execute inside the one-shot retry boundary so a
malformed or contract-invalid response receives a fresh model attempt.

### Retry and error contract

Expose backend-neutral exceptions:

```text
OneShotConfigurationError
OneShotAuthenticationError
OneShotConnectionError
OneShotTimeoutError
OneShotEmptyResponseError
OneShotDecodeError
OneShotValidationError
OneShotCompletionError
```

Retain three attempts with exponential backoff and a timeout per attempt.
Retry connection interruptions, request timeouts, rate limits, transient server
errors, empty responses, invalid JSON, and validator rejection. Do not retry a
missing executable, invalid configuration, authentication failure, invalid
OpenCode model syntax, unsupported provider/model errors, or programmer errors.

Adapters translate native failures into this exception vocabulary and preserve
the original exception as the cause. Workflow policy remains outside the
module: bib extraction may treat exhausted failure as a fallback miss, while a
distillation failure preserves the previous artifact and state atomically.

### Configuration migration

The packaged defaults become:

```yaml
llm:
  backend: opencode
  timeout_s: 120

models:
  opencode:
    bib_extract: "openai/gpt-5.6-sol"
    distill: "openai/gpt-5.6-sol"
    claim_verify: "openai/gpt-5.6-sol"
  openai:
    bib_extract: "GPT-5.4"
    distill: "Claude Sonnet 4.6"
    claim_verify: "Claude Sonnet 4.6"
```

Configuration validation accepts only `opencode` and `openai`, verifies all
three model roles for the active backend, checks that OpenCode models contain a
non-empty provider and model component, requires the `opencode` executable for
native mode, and retains direct-backend environment validation. Authentication
is checked by the first real request rather than config validation.

### Cache identity and provenance

Export a one-shot protocol version, initially `oneshot-v1`. Include it in bib
and distillation cache identity and in persisted LLM call provenance. Renaming
the backend from `bridge` to `opencode` intentionally makes bridge-produced
cache entries stale; do not rewrite historical artifacts merely because they
are read.

Per-call provenance continues to record backend, model, instruction version,
response-schema version, and input-token estimate. Add the one-shot protocol
version and normalized actual usage, cost, provider request ID, and finish
reason when available. Do not persist random server credentials, temporary
paths, session contents, raw provider responses, or internal prompt text.

### Dependencies and compatibility

Remove `oc-openai-bridge` from project dependencies and regenerate `uv.lock`.
Retain `openai`, `httpx`, and `tenacity`. Remove `puba/llm/openai_client.py`
after all call sites and tests migrate to the one-shot API.

Historical records whose backend is `bridge` remain readable and display their
stored provenance. They are stale against configured `opencode` queries because
the backend and protocol identity changed. No artifact migration command is
needed.

### Implementation order

1. Add an ADR superseding the OpenCode bridge transport while preserving the
   reasons for process reuse, credential ownership, isolation, and cache
   identity.
2. Add normalized result and exception types plus adapter protocols.
3. Implement and unit-test the managed OpenCode process lifecycle.
4. Implement native health, session, message, abort, and deletion operations.
5. Implement the direct OpenAI-compatible adapter behind the same result type.
6. Implement the one-shot facade, classified retries, JSON parsing, and
   caller-supplied validation.
7. Migrate bib extraction and every distillation call site. Keep prompts,
   schemas, token budgets, and workflow failure policy in their current layers.
8. Rename backend/model configuration and add protocol versioning to cache and
   provenance.
9. Remove the bridge dependency, old wrapper, and facade-specific tests; update
   the lockfile.
10. Update README, configuration, distillation documentation, design log,
    bundled agent skill, and CLI help/status wording.
11. Run the complete offline suite and opt-in live OpenCode text, structured,
    and Mofka claim-mode tests.

### Required tests

Cover managed-server command/environment construction, loopback binding,
random authentication, temporary-directory isolation, restrictive OpenCode
configuration, bounded startup diagnostics, health checks, lazy reuse,
thread-safe startup/close, termination and kill fallback, and cleanup. Cover
native session creation, strict model parsing, no-tools messages, text-part
assembly, provider errors, timeout abort, deletion in every outcome, and
cleanup errors that do not mask results.

Cover direct OpenAI stream assembly behind the one-shot API; normalized result
metadata; optional JSON-fence removal; strict object parsing; validator success,
rejection, and retry exhaustion; and every retryable/non-retryable error class.
Use fake one-shot clients for bib/distillation orchestration tests rather than
mocking provider protocols. Preserve existing tests for bib fallback,
distillation atomicity, every model-assessed support label, cache invalidation,
and artifact/provenance structure. Add one focused live native-OpenCode smoke
test and retain the model-agnostic live Mofka structural test.

### Explicit non-goals

- Attached OpenCode servers.
- Persistent conversational sessions.
- Public or real-time token streaming.
- Async APIs.
- Parallel claim verification.
- OpenCode tools or project-agent behavior.
- Native OpenCode JSON Schema output.
- A separately published one-shot package.
- A `bridge` configuration alias.

## Delivered foundation - Quote presence and claim-support assessment

The schema-v2 evidence objective below is implemented and remains the
authoritative behavioral contract for distillation artifacts. The one-shot
transport work must preserve its validation, retry, cache, provenance, and
atomic-failure semantics.

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

**Structured-response failure handling:** if, after the one-shot LLM
module's retry policy is exhausted, the model's
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

## Milestone 5 — `puba summarize`: deterministic sanity-check vault

`puba summarize <pdf>` renders a generated `<pdf-stem>.vault/`
directory **next to the PDF** (not inside `<pdf-stem>.puba/`), using
the PDF's own filename stem so multiple papers in the same directory
do not collide. It is generated **only when explicitly invoked** —
never as a side effect of `bib`, `md`, `figures`, or `distill`.

**"Deterministic" means:** the command makes no LLM call and no
network call — it only reads and formats data already computed by
prior stages, using the Milestone 3 status helpers to determine what
is current, stale, missing, or invalid. Given an unchanged set of
input artifacts, two invocations produce byte-identical generated
content with the sole exception of each note's frontmatter generation
timestamp (which necessarily reflects wall-clock time at generation).
This is
the same sense in which `puba bib`/`puba md` are "deterministic" for
a given cached input — not "byte-identical including the clock."

The complete vault is staged and installed as one directory replacement,
with individual generated notes written atomically. A refresh preserves
only the user-owned `.obsidian/` and `notes/` paths from the prior vault;
all other vault content is generated and replaced.

**Exit behavior:** `puba summarize` exits `0` whenever it successfully
produces a vault, regardless of how much content ends up in
"Missing / Needs Attention" — a paper with no stages run yet still
gets a (mostly-empty, mostly-attention-flagged) vault, not an error.
It exits non-zero only for a precondition it cannot work around at
all: the given PDF path doesn't exist, or the output path can't be
written (e.g. permission denied, read-only filesystem — consistent
with the existing behavior documented in README.md's "Output layout"
section for other commands).

Required layout and contents:
- `README.md` with OKF-style YAML frontmatter (artifact type,
  generation timestamp, relative source-PDF path, puba tool version,
  and each stage's status using Milestone 3's four-state vocabulary; the
  aggregate distill status is `mixed` when its per-query statuses differ),
  processing status, the prominent attention section, relative
  Markdown navigation, and every distillation's prompt and answer.
- `bibliography.md`, populated from `bib.json` or stating that no
  bibliographic record exists yet.
- `figures.md`, with counts, captions, metadata, and relative links to
  copied figure assets, or a note that no figures have been extracted.
- `distillations/<safe-query>.md` for every configured or on-disk distillation. Each
  deterministic filesystem-safe query note includes the user-authored
  prompt text (Milestone 4), model, processed-text scope, answer, and applicable claims and
  evidence, including partial/unverified states rather than hiding them.
  A distillation record written before Milestone 4 (no persisted
  prompt text, e.g. an un-migrated legacy record) is rendered with
  its prompt shown as "not recorded (generated before prompt
  persistence was added)" rather than omitted or left blank, and
  this itself is one of the conditions flagged in "Missing / Needs
  Attention" below.
- `assets/paper.pdf`, copied from the source PDF, and
  `assets/figures/`, containing available extracted figure assets.
- Relative Markdown links between the README, bibliography, figures,
  distillation notes, source PDF, and figure assets so the vault is
  self-contained and navigable.

The prominent **"Missing / Needs Attention"** section surfaces any
stage that is `never-run`, `stale`, or `mixed`, any stage that
is `invalid` (failed to parse), each non-current query behind a mixed distill
status, `needs_review: true` on the bib record,
any distillation with `evidence_status: partial`, any distillation
missing persisted prompt text (see above), and any artifact where a
legacy `.yaml` and current `.json` form were both found on disk (see
Milestone 1's transient-coexistence case).

After the vault replacement succeeds, an existing legacy
`<pdf-stem>.summary.md` is deleted only if its frontmatter identifies
it as a generated puba summary. An unrecognized file at that path is
user content: preserve it and emit a warning.

This command is meant as a human-facing sanity check across
everything puba has produced for one paper, so favor completeness and
clear structure over brevity.

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

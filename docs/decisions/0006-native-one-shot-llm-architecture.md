# Native one-shot LLM architecture

## Status

Accepted

## Context

Puba's LLM operations are independent one-shot requests: one system
instruction, one user input, and one completed text or structured result. The
existing OpenCode bridge transport exposes an OpenAI-shaped completion and
one-chunk stream even though it manages a local OpenCode process, creates a
disposable session, and makes one native request. That compatibility facade
leaks a provider protocol into workflows without matching the operation puba
actually needs.

This ADR supersedes the OpenCode bridge transport decision; it does not rewrite
that decision or its history. The reasons for local credential ownership,
managed-process reuse, runtime isolation, and transport-aware cache identity
remain valid. Existing artifacts whose provenance names `bridge` remain
readable as historical records.

## Decision

- Introduce an internal one-shot LLM module with a small synchronous,
  thread-safe API for completed text and validated JSON-object calls. Its
  boundary is designed to be extractable later, but it is not a separately
  published package. Workflows resolve model roles and overrides before calling
  it and never receive provider clients, streams, message parts, or raw
  responses.
- Support exactly two active backends: `opencode` and `openai`. Rename
  `llm.backend: bridge` and `models.bridge` to `opencode`; do not provide a
  `bridge` alias. Configuration still using `bridge` is invalid and receives an
  actionable migration error. OpenCode model identifiers require explicit
  `provider/model` syntax, while direct OpenAI-compatible model names are sent
  unchanged.
- The `opencode` adapter uses OpenCode's native HTTP health, session, message,
  abort, and deletion APIs, not an OpenAI-compatible facade. Puba lazily starts
  one `opencode serve --pure` child on loopback with an ephemeral port and
  reuses that process for the lifetime of the puba process. Every model request
  creates an independent disposable session, sends one synchronous no-tools
  message, assembles text parts in source order, attempts abort on timeout, and
  always attempts session deletion. Startup and shutdown are serialized;
  healthy operation may serve concurrent requests. Closing the client closes
  HTTP resources, terminates and, after a bounded wait, kills the child, and
  removes temporary resources. Backend replacement closes the old client, and
  process exit closes the active client.
- The managed server binds only to `127.0.0.1`, uses a fresh random password and
  HTTP Basic Auth on every request, and runs in an isolated temporary working
  directory while retaining user-level OpenCode credentials. Puba injects
  restrictive configuration that disables plugins and project instructions,
  selects a minimal agent, and denies all permissions; each session also has a
  catch-all deny rule. Startup output and diagnostics are bounded and sanitized.
  Credentials, temporary paths, session contents, raw responses, and internal
  prompts are not persisted.
- Retain the direct OpenAI-compatible adapter for Argo, OpenAI, and compatible
  local endpoints. Its existing streaming request may remain an adapter detail,
  but it fully assembles the response before returning the same normalized
  result type as the native adapter. Results may include normalized usage,
  cost, request/message identifiers, and finish reason when available.
- A structured call adds a versioned JSON-only instruction, receives the entire
  output, strips whitespace and at most one enclosing Markdown JSON fence,
  parses it locally with `json.loads`, requires an object, and invokes the
  caller-supplied puba validator. Parsing and validation are inside each retry
  attempt, so malformed JSON and contract-invalid output cause a fresh model
  request. The module remains schema-agnostic, does not extract JSON
  heuristically from prose, and treats valid negative or inconclusive semantic
  outcomes as successful responses.
- Expose backend-neutral typed errors for configuration, authentication,
  connection, timeout, empty response, decode, validation, and completion
  failures. Adapters translate native failures and preserve their causes. Use
  three bounded attempts with exponential backoff for connection interruptions,
  timeouts, rate limits, transient server errors, empty responses, decode
  failures, and validator rejection. Do not retry invalid configuration,
  missing executables, authentication failures, invalid OpenCode model syntax,
  unsupported provider/model errors, or programmer errors. Workflow-specific
  exhausted-failure policy remains outside the module.
- Export a one-shot protocol identity, initially `oneshot-v1`, and include it
  with backend and model identity in LLM cache keys and persisted call
  provenance. The `bridge` to `opencode` rename intentionally makes old bridge
  cache entries stale without rewriting historical artifacts. Persist
  normalized result metadata when available, but not provider-native payloads.
- Remove `oc-openai-bridge` and the old OpenAI-shaped wrapper after all call
  sites migrate. Retain `openai`, `httpx`, and `tenacity` for the two native
  adapters and shared retry behavior.

## Consequences

- Puba workflows depend on one operation-shaped contract and can use either
  backend without knowing its client protocol. Tests can use fake one-shot
  clients instead of mocking provider APIs.
- Native OpenCode operation keeps credentials under OpenCode's ownership and
  amortizes process startup while isolating every request in a disposable,
  denied-permission session. Cleanup failure must not replace a successful
  result or mask the original request exception.
- Existing `bridge` provenance remains displayable, but old configuration must
  be migrated and old cache entries are stale by design. No artifact migration
  command is introduced.
- Explicit non-goals are attached external OpenCode servers, persistent
  conversations, public or real-time token streaming, async APIs, parallel
  claim verification, OpenCode tools or project-agent behavior, native
  OpenCode JSON Schema output, a separately published one-shot package, and a
  `bridge` compatibility alias.

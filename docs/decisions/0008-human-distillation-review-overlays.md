# Store human distillation review as auditable overlays

## Status

Accepted

## Context

Generated distillation artifacts preserve model output, deterministic quote
location, and model-assessed claim support. Human reviewers need to accept,
correct, or remove individual claims and improve answer prose without erasing
that generated evidence trail or making model judgments look human-verified.
Regeneration can also change claim IDs, text, evidence, or assessments, so
silently applying old decisions to new output would be unsafe.

## Decision

- Generated `analyses/<name>.json` artifacts remain immutable during review.
  Human decisions are stored separately in `reviews/<name>.yaml` under the
  paper's analysis directory.
- Review packets are strict JSON inputs intended for batch and agent-driven
  workflows. Claim decisions are `accept`, `correct`, or `remove`; corrected
  decisions record a human support status, rationale, and references to present
  generated evidence. Removal records a reason and rationale.
- Human answer overrides retain the generated answer, require a rationale and
  an explicit `no_new_claims: true` attestation, and identify claims removed
  from the answer. This attestation is an audit assertion, not an automated
  semantic proof.
- Each sidecar records the complete generated-artifact digest, output digest,
  generation timestamp, claim fingerprints, model-assessment digests,
  timestamps, optional reviewer identities, and append-only action history.
- A sidecar applies only to its exact generated artifact. Regeneration makes it
  stale and disables all decisions and answer overrides until an explicit
  rebase. Rebase can carry a claim decision only when its fingerprint and model
  assessment still match; answer overrides must be resubmitted.
- Read surfaces distinguish generated values, model assessments, and human
  decisions. Effective answer text is the default for `show distill` and the
  generated summary vault, while `--original` and detail notes retain access to
  model output. Human aggregate support is emitted only after every current
  claim has a decision.
- `puba clean --what all` preserves human review sidecars. Deleting them
  requires the explicit `puba clean --what reviews --yes` command.

## Consequences

- Model output and human judgment remain independently inspectable and
  auditable instead of one overwriting the other.
- Reviews fail closed after regeneration; stale prose or claim decisions cannot
  silently affect effective output.
- Exact matching and strict packet validation make rebases conservative. A
  reviewer must submit new decisions when generated claim identity, evidence,
  or model assessment changes.
- Review sidecars are human-authored durable data rather than disposable cache,
  so routine cleanup and forced distillation runs do not remove them.
- Human support labels and the no-new-claims attestation remain reviewer
  judgments. They do not constitute formal verification of the paper or prose.

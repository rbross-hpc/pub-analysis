# BSD 3-Clause License
# Copyright (c) 2026, UChicago Argonne, LLC, Argonne National Laboratory.
"""Focused integration coverage for the schema-v2 claim pipeline."""
from __future__ import annotations

import json
from pathlib import Path
import pytest
import yaml

from puba.distill.queries import DistillQuery
from puba.llm.oneshot import OneShotClient, OneShotResult


def _result(value, **metadata):
    return OneShotResult(value, "opencode", metadata.pop("model", "test-model"), **metadata)


def _paper(tmp_path: Path, abstract: str = "Exact evidence. Other source text.") -> Path:
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    ad = tmp_path / "paper.puba"
    ad.mkdir()
    (ad / "bib.yaml").write_text(yaml.dump({
        "title": "Paper", "abstract": abstract, "needs_review": False,
    }), encoding="utf-8")
    return pdf


def _query(name: str = "claims", mode: str = "claims", **kwargs) -> DistillQuery:
    return DistillQuery(
        name, "abstract", "Analyze.", kwargs.pop("max_chars", None),
        kwargs.pop("model", "generation-model"), None, "test",
        evidence_mode=mode, verifier_model=kwargs.pop("verifier_model", "verifier-model"),
        **kwargs,
    )


def _record(tmp_path: Path, name: str = "claims") -> dict:
    return json.loads(
        (tmp_path / "paper.puba" / "analyses" / f"{name}.json").read_text(encoding="utf-8")
    )


def test_none_mode_writes_v2_without_evidence_fields(tmp_path, monkeypatch):
    import puba.distill.run as run

    pdf = _paper(tmp_path)
    monkeypatch.setattr(run.llm, "text", lambda **_: _result("  Answer.  "))
    result = run.run_query(pdf, _query("plain", "none"))
    record = _record(tmp_path, "plain")

    assert result["evidence_mode"] == "none"
    assert record["schema_version"] == 2
    assert record["output"] == "Answer."
    assert not ({"evidence", "claims", "quote_presence_status", "claim_support_status"} & record.keys())


def test_quotes_mode_uses_narrative_permitted_span(tmp_path, monkeypatch):
    import puba.distill.run as run

    md = "# Intro\n\nPermitted quote.\n\n## References\n\nLeaked quote."
    pdf = _paper(tmp_path)
    ad = tmp_path / "paper.puba"
    (ad / "paper.md").write_text(md, encoding="utf-8")
    (ad / "paper.sections.json").write_text("[]", encoding="utf-8")
    query = DistillQuery(
        "quotes", "narrative", "Analyze.", None, "generation-model", None, "test",
        evidence_mode="quotes",
    )
    monkeypatch.setattr(run.llm, "json_object", lambda **_: _result({
        "answer": "Answer.",
        "evidence": [{"quote": "Permitted quote."}, {"quote": "Leaked quote."}],
    }))

    result = run.run_query(pdf, query)
    record = _record(tmp_path, "quotes")
    assert result["quote_presence_counts"] == {
        "total": 2, "present": 1, "not_found": 1, "ambiguous": 0,
    }
    assert [item["quote_status"] for item in record["evidence"]] == ["present", "not_found"]


def test_claim_pipeline_order_models_local_ids_and_support_persistence(tmp_path, monkeypatch):
    import puba.distill.run as run

    pdf = _paper(tmp_path)
    calls = []

    def chat_text(**kwargs):
        calls.append(("generation", kwargs["model"]))
        return _result("Authoritative answer.")

    def chat_json(**kwargs):
        calls.append(("json", kwargs["model"]))
        if kwargs["model"] == "generation-model":
            return _result({"claims": [
                {"claim": "A supported fact.", "evidence": [{"quote": "Exact evidence."}]},
                {"claim": "A fact without evidence.", "evidence": [{"quote": "Missing quote."}]},
            ]})
        return _result({
            "status": "unsupported", "rationale": "The passage does not establish the claim.",
            "evidence_ids": ["c1e1"],
        })

    monkeypatch.setattr(run.llm, "text", chat_text)
    monkeypatch.setattr(run.llm, "json_object", chat_json)
    result = run.run_query(pdf, _query())
    record = _record(tmp_path)

    assert calls == [
        ("generation", "generation-model"),
        ("json", "generation-model"),
        ("json", "verifier-model"),
    ]
    assert record["output"] == "Authoritative answer."
    assert [claim["id"] for claim in record["claims"]] == ["c1", "c2"]
    assert record["claims"][0]["evidence"][0]["id"] == "c1e1"
    assert record["claims"][0]["support_assessment"]["status"] == "unsupported"
    assert record["claims"][1]["support_assessment"]["status"] == "not_assessed"
    assert result["claim_support_status"] == "partial"
    assert record["_provenance"]["calls"]["verification"]["request_count"] == 1
    assert "system" not in json.dumps(record["_provenance"])


def test_actual_result_metadata_is_persisted_without_raw_response(tmp_path, monkeypatch):
    import puba.distill.run as run

    pdf = _paper(tmp_path)
    monkeypatch.setattr(run.llm, "text", lambda **_: _result(
        "Answer.", usage={"input_tokens": 7, "output_tokens": 2}, cost=0.25,
        request_id="generation-1", finish_reason="stop",
    ))
    result = run.run_query(pdf, _query("plain", "none"))
    call = _record(tmp_path, "plain")["_provenance"]["calls"]["generation"]

    assert result["status"] == "distilled"
    assert call["llm_protocol_version"] == "oneshot-v1"
    assert call["usage"] == {"input_tokens": 7, "output_tokens": 2}
    assert call["cost"] == 0.25
    assert call["request_id"] == "generation-1"
    assert call["finish_reason"] == "stop"
    assert "raw" not in json.dumps(call).lower()


def test_verifier_metadata_is_aggregated_and_bounded(tmp_path, monkeypatch):
    import puba.distill.run as run

    pdf = _paper(tmp_path, "First evidence. Second evidence.")
    monkeypatch.setattr(run.llm, "text", lambda **_: _result("Answer."))
    verifier_count = 0

    def json_object(**kwargs):
        nonlocal verifier_count
        if kwargs["model"] == "generation-model":
            return _result({"claims": [
                {"claim": "first", "evidence": [{"quote": "First evidence."}]},
                {"claim": "second", "evidence": [{"quote": "Second evidence."}]},
            ]})
        verifier_count += 1
        return _result(
            {"status": "supported", "rationale": "Supported.", "evidence_ids": [f"c{verifier_count}e1"]},
            usage={"input_tokens": verifier_count, "output_tokens": 2},
            cost=0.1 * verifier_count, request_id=f"request-{verifier_count}",
            finish_reason="stop",
        )

    monkeypatch.setattr(run.llm, "json_object", json_object)
    assert run.run_query(pdf, _query())["status"] == "distilled"
    verification = _record(tmp_path)["_provenance"]["calls"]["verification"]
    assert verification["request_count"] == 2
    assert verification["usage"] == {"input_tokens": 3, "output_tokens": 4}
    assert verification["cost"] == pytest.approx(0.3)
    assert verification["request_ids"] == ["request-1", "request-2"]
    assert verification["finish_reasons"] == ["stop", "stop"]


@pytest.mark.parametrize("label", [
    "supported", "partially_supported", "unsupported", "contradicted",
    "insufficient_evidence",
])
def test_every_verifier_outcome_is_a_successful_persisted_result(tmp_path, monkeypatch, label):
    from puba.distill import run

    pdf = _paper(tmp_path)
    monkeypatch.setattr(run.llm, "text", lambda **_: _result("Answer."))
    monkeypatch.setattr(run.llm, "json_object", lambda **kwargs: _result(
        {"claims": [{"claim": "fact", "evidence": [{"quote": "Exact evidence."}]}]}
        if kwargs["model"] == "generation-model"
        else {"status": label, "rationale": "Bounded rationale.", "evidence_ids": ["c1e1"]}
    ))

    result = run.run_query(pdf, _query())
    assert result["status"] == "distilled"
    assert _record(tmp_path)["claims"][0]["support_assessment"]["status"] == label


def test_malformed_atomizer_and_verifier_are_retried(tmp_path, monkeypatch):
    import puba.distill.run as run

    pdf = _paper(tmp_path)
    class Adapter:
        def __init__(self):
            self.values = iter([
                "Authoritative answer.",
                '{"claims":[{"claim":"fact","evidence":[],"id":"model-id"}]}',
                '{"claims":[{"claim":"fact","evidence":[{"quote":"Exact evidence."}]}]}',
                '{"status":"supported","rationale":"ok","evidence_ids":["unknown"]}',
                '{"status":"supported","rationale":"ok","evidence_ids":["c1e1"]}',
            ])
            self.calls = 0
        def complete(self, **kwargs):
            self.calls += 1
            return _result(next(self.values), model=kwargs["model"])
        def close(self):
            pass
    adapter = Adapter()
    client = OneShotClient(adapter, wait=lambda _: None)
    monkeypatch.setattr(run.llm, "text", client.text)
    monkeypatch.setattr(run.llm, "json_object", client.json_object)

    result = run.run_query(pdf, _query())
    assert result["status"] == "distilled"
    assert result["claim_support_status"] == "supported"
    assert adapter.calls == 5


@pytest.mark.parametrize("failure_stage", ["generation", "atomization", "verification"])
def test_claim_stage_failure_preserves_artifact_and_state(tmp_path, monkeypatch, failure_stage):
    import puba.distill.run as run

    pdf = _paper(tmp_path)
    ad = tmp_path / "paper.puba"
    (ad / "analyses").mkdir()
    artifact = ad / "analyses" / "claims.json"
    artifact.write_text('{"output":"prior"}', encoding="utf-8")
    state = ad / ".state.json"
    state.write_text('{"prior":true}', encoding="utf-8")

    def chat_text(**_):
        if failure_stage == "generation":
            raise RuntimeError("generation failed")
        return _result("Answer.")

    json_calls = 0

    def chat_json(**kwargs):
        nonlocal json_calls
        json_calls += 1
        if kwargs["model"] == "generation-model":
            if failure_stage == "atomization":
                raise RuntimeError("atomization failed")
            return _result({"claims": [{"claim": "fact", "evidence": [{"quote": "Exact evidence."}]}]})
        if failure_stage == "verification":
            raise RuntimeError("verification failed")
        return _result({"status": "supported", "rationale": "ok", "evidence_ids": ["c1e1"]})

    monkeypatch.setattr(run.llm, "text", chat_text)
    monkeypatch.setattr(run.llm, "json_object", chat_json)
    result = run.run_query(pdf, _query(), force=True)

    assert result["status"] == "error"
    assert artifact.read_text(encoding="utf-8") == '{"output":"prior"}'
    assert state.read_text(encoding="utf-8") == '{"prior":true}'


def test_verifier_identity_invalidates_cache_and_envelopes_match(tmp_path, monkeypatch):
    import puba.distill.run as run

    pdf = _paper(tmp_path)
    verifier_models = []
    monkeypatch.setattr(run.llm, "text", lambda **_: _result("Answer."))

    def chat_json(**kwargs):
        if kwargs["model"] == "generation-model":
            return _result({"claims": [{"claim": "fact", "evidence": [{"quote": "Exact evidence."}]}]})
        verifier_models.append(kwargs["model"])
        return _result({"status": "contradicted", "rationale": "The passage conflicts.", "evidence_ids": ["c1e1"]})

    monkeypatch.setattr(run.llm, "json_object", chat_json)
    query = _query()
    fresh = run.run_query(pdf, query)
    cached = run.run_query(pdf, query)
    changed = run.run_query(pdf, query, verifier_model_override="override-verifier")

    for result in (fresh, cached, changed):
        assert result["evidence_mode"] == "claims"
        assert result["claim_support_status"] == "contradicted"
        assert result["quote_presence_counts"]["present"] == 1
    assert cached["status"] == "cached"
    assert changed["status"] == "distilled"
    assert verifier_models == ["verifier-model", "override-verifier"]
    state = json.loads((tmp_path / "paper.puba" / ".state.json").read_text())
    assert state["stages"]["distill"]["claims"]["verifier_model"] == "override-verifier"


def test_atomizer_budget_rejected_before_atomizer_call(tmp_path, monkeypatch):
    import puba.distill.run as run

    pdf = _paper(tmp_path)
    configured = run.cfg.distill()
    monkeypatch.setattr(run.cfg, "distill", lambda: {
        **configured,
        "claims": {**configured["claims"], "max_atomizer_input_tokens": 1},
    })
    monkeypatch.setattr(run.llm, "text", lambda **_: _result("Answer."))
    monkeypatch.setattr(
        run.llm, "json_object",
        lambda **_: pytest.fail("atomizer call should be rejected before dispatch"),
    )

    result = run.run_query(pdf, _query())
    assert result["status"] == "error"
    assert "max_atomizer_input_tokens" in result["error"]
    assert not (tmp_path / "paper.puba" / "analyses" / "claims.json").exists()


def test_generation_budget_change_invalidates_cache_before_call(tmp_path, monkeypatch):
    import puba.distill.run as run

    pdf = _paper(tmp_path)
    configured = run.cfg.distill()
    calls = []
    monkeypatch.setattr(
        run.llm, "text", lambda **_: calls.append("generation") or _result("Answer."),
    )
    query = _query("plain", "none")

    assert run.run_query(pdf, query)["status"] == "distilled"
    monkeypatch.setattr(run.cfg, "distill", lambda: {
        **configured,
        "max_input_tokens": 1,
    })
    result = run.run_query(pdf, query)

    assert result["status"] == "error"
    assert "max_input_tokens" in result["error"]
    assert calls == ["generation"]


def test_invalid_evidence_mode_fails_before_llm_call(tmp_path, monkeypatch):
    import puba.distill.run as run

    pdf = _paper(tmp_path)
    monkeypatch.setattr(
        run.llm, "text", lambda **_: pytest.fail("LLM call must not run"),
    )
    result = run.run_query(pdf, _query("invalid", "invalid"))

    assert result["status"] == "error"
    assert "evidence_mode" in result["error"]


@pytest.mark.parametrize(
    ("setting", "expected"),
    [
        ("max_context_tokens_per_claim", "max_context_tokens_per_claim"),
        ("max_total_input_tokens", "max_total_input_tokens"),
    ],
)
def test_verifier_budgets_reject_before_verifier_call(
    tmp_path, monkeypatch, setting, expected,
):
    import puba.distill.run as run

    pdf = _paper(tmp_path)
    configured = run.cfg.distill()
    monkeypatch.setattr(run.cfg, "distill", lambda: {
        **configured,
        "verification": {
            **configured["verification"],
            setting: 1,
        },
    })
    monkeypatch.setattr(run.llm, "text", lambda **_: _result("Answer."))

    def chat_json(**kwargs):
        if kwargs["model"] == "verifier-model":
            pytest.fail("verifier call should be rejected before dispatch")
        return _result({"claims": [{
            "claim": "fact", "evidence": [{"quote": "Exact evidence."}],
        }]})

    monkeypatch.setattr(run.llm, "json_object", chat_json)
    result = run.run_query(pdf, _query())

    assert result["status"] == "error"
    assert expected in result["error"]
    assert not (tmp_path / "paper.puba" / "analyses" / "claims.json").exists()


@pytest.mark.parametrize(("group", "key"), [
    ("claims", "max_claims"),
    ("claims", "max_evidence_per_claim"),
    ("claims", "max_quote_chars"),
    ("claims", "max_atomizer_input_tokens"),
    ("verification", "max_context_tokens_per_claim"),
    ("verification", "max_total_input_tokens"),
    ("verification", "max_rationale_chars"),
])
def test_every_claim_limit_changes_cache_identity(monkeypatch, group, key):
    import puba.distill.run as run

    query = _query()
    configured = run.cfg.distill()
    before = run.effective_instruction_sha(query)
    monkeypatch.setattr(run.cfg, "distill", lambda: {
        **configured,
        group: {**configured[group], key: configured[group][key] + 1},
    })

    assert run.effective_instruction_sha(query) != before


def test_distill_protocol_is_in_effective_identity_and_state(tmp_path, monkeypatch):
    import puba.distill.run as run

    query = _query()
    before = run.effective_instruction_sha(query)
    assert run.effective_instruction_payload(query)["llm_protocol_version"] == "oneshot-v1"
    monkeypatch.setattr(run, "PROTOCOL_VERSION", "oneshot-v2")
    assert run.effective_instruction_sha(query) != before
    monkeypatch.setattr(run, "PROTOCOL_VERSION", "oneshot-v1")
    pdf = _paper(tmp_path)
    monkeypatch.setattr(run.llm, "text", lambda **_: _result("Answer."))
    monkeypatch.setattr(run.llm, "json_object", lambda **kwargs: _result(
        {"claims": []} if kwargs["model"] == "generation-model" else pytest.fail("unexpected verifier")
    ))
    assert run.run_query(pdf, query)["status"] == "distilled"
    state = json.loads((tmp_path / "paper.puba" / ".state.json").read_text())
    assert state["stages"]["distill"]["claims"]["llm_protocol_version"] == "oneshot-v1"
    assert _record(tmp_path)["_provenance"]["llm_protocol_version"] == "oneshot-v1"


def test_list_distillations_exposes_claim_metadata(tmp_path, monkeypatch):
    import puba.distill.run as run

    pdf = _paper(tmp_path)
    query = _query()
    monkeypatch.setattr(run.llm, "text", lambda **_: _result("Answer."))
    monkeypatch.setattr(run.llm, "json_object", lambda **kwargs: _result(
        {"claims": []} if kwargs["model"] == "generation-model" else pytest.fail("unexpected verifier")
    ))
    assert run.run_query(pdf, query)["status"] == "distilled"

    listed = run.list_distillations(pdf, {"claims": query})[0]
    assert listed["evidence_mode"] == "claims"
    assert listed["claim_support_status"] == "not_applicable"
    assert listed["claim_support_counts"]["total"] == 0
    assert listed["verifier_model"] == "verifier-model"

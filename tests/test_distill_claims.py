# BSD 3-Clause License
# Copyright (c) 2026, UChicago Argonne, LLC, Argonne National Laboratory.
"""Focused integration coverage for the schema-v2 claim pipeline."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml
from tenacity import wait_none

from puba.distill.queries import DistillQuery


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
    monkeypatch.setattr(run.openai_client, "chat_text", lambda **_: "  Answer.  ")
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
    monkeypatch.setattr(run.openai_client, "chat_json", lambda **_: {
        "answer": "Answer.",
        "evidence": [{"quote": "Permitted quote."}, {"quote": "Leaked quote."}],
    })

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
        calls.append(("generation", kwargs["model"], kwargs["model_role"]))
        return "Authoritative answer."

    def chat_json(**kwargs):
        calls.append(("json", kwargs["model"], kwargs["model_role"]))
        if kwargs["model_role"] == "distill":
            return {"claims": [
                {"claim": "A supported fact.", "evidence": [{"quote": "Exact evidence."}]},
                {"claim": "A fact without evidence.", "evidence": [{"quote": "Missing quote."}]},
            ]}
        return {
            "status": "unsupported", "rationale": "The passage does not establish the claim.",
            "evidence_ids": ["c1e1"],
        }

    monkeypatch.setattr(run.openai_client, "chat_text", chat_text)
    monkeypatch.setattr(run.openai_client, "chat_json", chat_json)
    result = run.run_query(pdf, _query())
    record = _record(tmp_path)

    assert calls == [
        ("generation", "generation-model", "distill"),
        ("json", "generation-model", "distill"),
        ("json", "verifier-model", "claim_verify"),
    ]
    assert record["output"] == "Authoritative answer."
    assert [claim["id"] for claim in record["claims"]] == ["c1", "c2"]
    assert record["claims"][0]["evidence"][0]["id"] == "c1e1"
    assert record["claims"][0]["support_assessment"]["status"] == "unsupported"
    assert record["claims"][1]["support_assessment"]["status"] == "not_assessed"
    assert result["claim_support_status"] == "partial"
    assert record["_provenance"]["calls"]["verification"]["request_count"] == 1
    assert "system" not in json.dumps(record["_provenance"])


@pytest.mark.parametrize("label", [
    "supported", "partially_supported", "unsupported", "contradicted",
    "insufficient_evidence",
])
def test_every_verifier_outcome_is_a_successful_persisted_result(tmp_path, monkeypatch, label):
    from puba.distill import run

    pdf = _paper(tmp_path)
    monkeypatch.setattr(run.openai_client, "chat_text", lambda **_: "Answer.")
    monkeypatch.setattr(run.openai_client, "chat_json", lambda **kwargs: (
        {"claims": [{"claim": "fact", "evidence": [{"quote": "Exact evidence."}]}]}
        if kwargs["model_role"] == "distill"
        else {"status": label, "rationale": "Bounded rationale.", "evidence_ids": ["c1e1"]}
    ))

    result = run.run_query(pdf, _query())
    assert result["status"] == "distilled"
    assert _record(tmp_path)["claims"][0]["support_assessment"]["status"] == label


def _stream(content: str) -> list[MagicMock]:
    choice = MagicMock()
    choice.delta.content = content
    chunk = MagicMock()
    chunk.choices = [choice]
    return [chunk]


def test_malformed_atomizer_and_verifier_are_retried(tmp_path, monkeypatch):
    import puba.distill.run as run

    pdf = _paper(tmp_path)
    client = MagicMock()
    client.chat.completions.create.side_effect = [
        _stream("Authoritative answer."),
        _stream('{"claims":[{"claim":"fact","evidence":[],"id":"model-id"}]}'),
        _stream('{"claims":[{"claim":"fact","evidence":[{"quote":"Exact evidence."}]}]}'),
        _stream('{"status":"supported","rationale":"ok","evidence_ids":["unknown"]}'),
        _stream('{"status":"supported","rationale":"ok","evidence_ids":["c1e1"]}'),
    ]
    monkeypatch.setattr(run.openai_client, "_client", lambda: client)
    monkeypatch.setattr(
        run.openai_client, "chat_text",
        getattr(run.openai_client.chat_text, "retry_with")(wait=wait_none()),
    )
    monkeypatch.setattr(
        run.openai_client, "chat_json",
        getattr(run.openai_client.chat_json, "retry_with")(wait=wait_none()),
    )

    result = run.run_query(pdf, _query())
    assert result["status"] == "distilled"
    assert result["claim_support_status"] == "supported"
    assert client.chat.completions.create.call_count == 5


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
        return "Answer."

    json_calls = 0

    def chat_json(**kwargs):
        nonlocal json_calls
        json_calls += 1
        if kwargs["model_role"] == "distill":
            if failure_stage == "atomization":
                raise RuntimeError("atomization failed")
            return {"claims": [{"claim": "fact", "evidence": [{"quote": "Exact evidence."}]}]}
        if failure_stage == "verification":
            raise RuntimeError("verification failed")
        return {"status": "supported", "rationale": "ok", "evidence_ids": ["c1e1"]}

    monkeypatch.setattr(run.openai_client, "chat_text", chat_text)
    monkeypatch.setattr(run.openai_client, "chat_json", chat_json)
    result = run.run_query(pdf, _query(), force=True)

    assert result["status"] == "error"
    assert artifact.read_text(encoding="utf-8") == '{"output":"prior"}'
    assert state.read_text(encoding="utf-8") == '{"prior":true}'


def test_verifier_identity_invalidates_cache_and_envelopes_match(tmp_path, monkeypatch):
    import puba.distill.run as run

    pdf = _paper(tmp_path)
    verifier_models = []
    monkeypatch.setattr(run.openai_client, "chat_text", lambda **_: "Answer.")

    def chat_json(**kwargs):
        if kwargs["model_role"] == "distill":
            return {"claims": [{"claim": "fact", "evidence": [{"quote": "Exact evidence."}]}]}
        verifier_models.append(kwargs["model"])
        return {"status": "contradicted", "rationale": "The passage conflicts.", "evidence_ids": ["c1e1"]}

    monkeypatch.setattr(run.openai_client, "chat_json", chat_json)
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
    monkeypatch.setattr(run.openai_client, "chat_text", lambda **_: "Answer.")
    monkeypatch.setattr(
        run.openai_client, "chat_json",
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
        run.openai_client, "chat_text", lambda **_: calls.append("generation") or "Answer.",
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
        run.openai_client, "chat_text", lambda **_: pytest.fail("LLM call must not run"),
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
    monkeypatch.setattr(run.openai_client, "chat_text", lambda **_: "Answer.")

    def chat_json(**kwargs):
        if kwargs["model_role"] == "claim_verify":
            pytest.fail("verifier call should be rejected before dispatch")
        return {"claims": [{
            "claim": "fact", "evidence": [{"quote": "Exact evidence."}],
        }]}

    monkeypatch.setattr(run.openai_client, "chat_json", chat_json)
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


def test_list_distillations_exposes_claim_metadata(tmp_path, monkeypatch):
    import puba.distill.run as run

    pdf = _paper(tmp_path)
    query = _query()
    monkeypatch.setattr(run.openai_client, "chat_text", lambda **_: "Answer.")
    monkeypatch.setattr(run.openai_client, "chat_json", lambda **kwargs: (
        {"claims": []} if kwargs["model_role"] == "distill" else pytest.fail("unexpected verifier")
    ))
    assert run.run_query(pdf, query)["status"] == "distilled"

    listed = run.list_distillations(pdf, {"claims": query})[0]
    assert listed["evidence_mode"] == "claims"
    assert listed["claim_support_status"] == "not_applicable"
    assert listed["claim_support_counts"]["total"] == 0
    assert listed["verifier_model"] == "verifier-model"

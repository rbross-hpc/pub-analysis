# BSD 3-Clause License
# Copyright (c) 2026, UChicago Argonne, LLC, Argonne National Laboratory.
"""Deterministic evidence cases grounded in the pinned Mofka extraction."""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest
import yaml

from puba.distill.evidence import process_quote_presence
from puba.distill.queries import DistillQuery
from puba.llm.oneshot import OneShotResult


FIXTURES = Path(__file__).parent / "fixtures"
MOFKA_SIDECAR = FIXTURES / "dorier-mofka.puba"
MOFKA_MD = MOFKA_SIDECAR / "paper.md"
MOFKA_SECTIONS = MOFKA_SIDECAR / "paper.sections.json"

PERSISTENT_QUOTE = (
    "We introduce Mofka, a persistent event-streaming framework designed specifically "
    "for HPC environments."
)
MOCHI_QUOTE = "Mofka builds on the Mochi suite of technologies for developing HPC data services [49]."
COMPARISON_QUOTE = (
    "We begin our empirical evaluation using carefully controlled synthetic benchmarks to compare "
    "Mofka with two existing persistent messaging systems, Kafka and Redpanda, on Argonne's Improv "
    "and Polaris, described hereafter."
)
THROUGHPUT_QUOTE = (
    "Experiments on Argonne's Polaris and Improv supercomputers and Oak Ridge's Frontier exascale "
    "supercomputer show up to 8× improvement in throughput in some scenarios."
)
APPLICATIONS_QUOTE = (
    "These use cases are 3D tomography reconstruction (TekApp), the discovery of novel metal-organic "
    "frameworks (MOFs) with AI-driven molecular simulations (MOFA), and the performance characterization "
    "and provenance of HPC workflows (Flowcept)."
)
MULTILINE_QUOTE = (
    "Mofka partition managers keep track of which consumer is subscribed to them and actively fetch batches "
    "of metadata from the log provider to send to the consumer. However, it does not proactively send them "
    "the data part of each event, as will be explained next.\n\n## 3.2.3 Consuming events"
)


def _source() -> str:
    return MOFKA_MD.read_text(encoding="utf-8")


def _sections() -> list[dict]:
    return json.loads(MOFKA_SECTIONS.read_text(encoding="utf-8"))


def _result(value, *, model="test-model"):
    return OneShotResult(value, "opencode", model)


def _paper(tmp_path: Path) -> Path:
    pdf = tmp_path / "dorier-mofka.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    sidecar = tmp_path / "dorier-mofka.puba"
    sidecar.mkdir()
    shutil.copy2(MOFKA_MD, sidecar / "paper.md")
    shutil.copy2(MOFKA_SECTIONS, sidecar / "paper.sections.json")
    (sidecar / "bib.yaml").write_text(yaml.dump({
        "title": "Toward a persistent event-streaming system for high-performance computing applications",
        "abstract": PERSISTENT_QUOTE,
        "needs_review": False,
    }), encoding="utf-8")
    return pdf


def _query() -> DistillQuery:
    return DistillQuery(
        "mofka-evidence", "full", "Analyze Mofka.", None,
        "generation-model", None, "test", evidence_mode="claims",
        verifier_model="verifier-model",
    )


def _record(tmp_path: Path) -> dict:
    path = tmp_path / "dorier-mofka.puba" / "analyses" / "mofka-evidence.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_pinned_mofka_extraction_is_complete_and_stable():
    source = _source()
    sections = _sections()

    assert hashlib.sha256(source.encode()).hexdigest() == (
        "306ac6de4d96e7401f7c90a31f1bf63c2a1a64b6c5ce94c85f284670b8001d86"
    )
    assert hashlib.sha256(MOFKA_SECTIONS.read_bytes()).hexdigest() == (
        "7935eb8f29cbe76d262b538893b6aa6dc1d4bf9a82d42dbe0e821cdd90ee7861"
    )
    assert len(source) >= 120_000
    assert len(sections) >= 50
    assert {"2 ABSTRACT", "3.2 Overview of Mofka's architecture", "4 EVALUATION",
            "5 APPLICATION USE CASES", "7 CONCLUSION", "REFERENCES"} <= {
        section["title"] for section in sections
    }
    assert sections[-1]["end_offset"] == len(source)


@pytest.mark.parametrize("quote,expected_section", [
    (PERSISTENT_QUOTE, "2 ABSTRACT"),
    (MOCHI_QUOTE, "3.2 Overview of Mofka's architecture"),
    (COMPARISON_QUOTE, "4 EVALUATION"),
    (
        "The reconstruction of 3D tomography datasets is a core application in X-ray imaging that "
        "provides valuable information about imaged samples and their morphologies.",
        "5.1 Use Case 1: TekApp",
    ),
    (MULTILINE_QUOTE, None),
])
def test_exact_mofka_quotes_are_present(quote, expected_section):
    result = process_quote_presence(
        [{"id": "c1e1", "quote": quote}], _source(), sections=_sections(),
    )["evidence"][0]

    assert result["quote_status"] == "present"
    assert result["section"] == expected_section
    assert result["page"] is not None


@pytest.mark.parametrize("quote", [
    PERSISTENT_QUOTE.replace("persistent", "transient"),
    "Mofka is a durable streaming platform purpose-built for supercomputers.",
    "We introduce Mofka, a persistent event-streaming framework designed specifically for HPC environments. "
    "Mofka builds on the Mochi suite of technologies for developing HPC data services [49].",
    THROUGHPUT_QUOTE.replace("up to 8×", "exactly 8×"),
    PERSISTENT_QUOTE.replace("a persistent", "a non-persistent"),
    "Mofka uses quantum networking to replicate every event across all supercomputers.",
])
def test_altered_paraphrased_stitched_and_fabricated_quotes_are_absent(quote):
    result = process_quote_presence([{"id": "c1e1", "quote": quote}], _source())

    assert result["evidence"][0]["quote_status"] == "not_found"
    assert result["quote_presence_status"] == "partial"


@pytest.mark.parametrize("quote", [
    MULTILINE_QUOTE.replace("\n\n", "\n"),
    "We repeated a subset of our experiments on OLCF's Frontier.",
    PERSISTENT_QUOTE.replace("event-streaming", "event streaming"),
    PERSISTENT_QUOTE.replace("Mofka", "mofka", 1),
])
def test_quote_matching_does_not_normalize_formatting(quote):
    assert process_quote_presence(
        [{"id": "c1e1", "quote": quote}], _source(),
    )["evidence"][0]["quote_status"] == "not_found"


SUPPORTED_CASES = [
    ("Mofka is a persistent event-streaming framework for HPC.", PERSISTENT_QUOTE),
    ("Mofka is built using Mochi components.", MOCHI_QUOTE),
    ("The evaluation compares Mofka with Kafka and Redpanda.", COMPARISON_QUOTE),
    ("The paper reports up to an 8× throughput improvement in some scenarios.", THROUGHPUT_QUOTE),
    (
        "The paper discusses tomography, metal-organic-framework discovery, and workflow provenance.",
        APPLICATIONS_QUOTE,
    ),
]


def test_supported_mofka_claims_keep_quote_and_support_outcomes_separate(tmp_path, monkeypatch):
    from puba.distill import run

    pdf = _paper(tmp_path)
    monkeypatch.setattr(run.llm, "text", lambda **_: _result("Supported Mofka claims."))

    def json_object(**kwargs):
        if kwargs["model"] == "generation-model":
            return _result({"claims": [
                {"claim": claim, "evidence": [{"quote": quote}]}
                for claim, quote in SUPPORTED_CASES
            ]}, model=kwargs["model"])
        request = json.loads(kwargs["user"])
        return _result({
            "status": "supported",
            "rationale": "The exact passage entails the bounded claim.",
            "evidence_ids": [item["id"] for item in request["evidence"]],
        }, model=kwargs["model"])

    monkeypatch.setattr(run.llm, "json_object", json_object)
    result = run.run_query(pdf, _query())
    record = _record(tmp_path)

    assert result["quote_presence_status"] == "complete"
    assert result["quote_presence_counts"]["present"] == len(SUPPORTED_CASES)
    assert result["claim_support_status"] == "supported"
    assert all(
        claim["support_assessment"]["status"] == "supported"
        for claim in record["claims"]
    )


UNSUPPORTED_CASES = [
    ("Mofka is always 8× faster than Kafka and Redpanda.", THROUGHPUT_QUOTE, "unsupported"),
    ("Every benchmark achieved an 8× improvement.", THROUGHPUT_QUOTE, "unsupported"),
    ("Mofka reduced latency by up to 8× in some scenarios.", THROUGHPUT_QUOTE, "unsupported"),
    (
        "The TekApp evaluation was performed on Frontier.",
        "We evaluated TekApp on the Polaris supercomputer.",
        "contradicted",
    ),
    ("MOFA is the paper's tomography reconstruction application.", APPLICATIONS_QUOTE, "contradicted"),
    ("Mofka is a non-persistent framework.", PERSISTENT_QUOTE, "contradicted"),
    ("Mofka achieved 8× higher throughput in every benchmark.", MOCHI_QUOTE, "unsupported"),
]


def test_genuine_quotes_can_fail_to_support_mofka_claims(tmp_path, monkeypatch):
    from puba.distill import run

    pdf = _paper(tmp_path)
    expected = {claim: status for claim, _, status in UNSUPPORTED_CASES}
    monkeypatch.setattr(run.llm, "text", lambda **_: _result("Unsupported Mofka claims."))

    def json_object(**kwargs):
        if kwargs["model"] == "generation-model":
            return _result({"claims": [
                {"claim": claim, "evidence": [{"quote": quote}]}
                for claim, quote, _ in UNSUPPORTED_CASES
            ]}, model=kwargs["model"])
        request = json.loads(kwargs["user"])
        status = expected[request["claim"]]
        return _result({
            "status": status,
            "rationale": "The genuine passage does not support the distorted claim.",
            "evidence_ids": [item["id"] for item in request["evidence"]],
        }, model=kwargs["model"])

    monkeypatch.setattr(run.llm, "json_object", json_object)
    result = run.run_query(pdf, _query())
    record = _record(tmp_path)

    assert result["quote_presence_status"] == "complete"
    assert result["claim_support_status"] == "contradicted"
    assert [claim["support_assessment"]["status"] for claim in record["claims"]] == [
        status for _, _, status in UNSUPPORTED_CASES
    ]


@pytest.mark.parametrize("claim,quote,verifier_status,quote_status,support_status", [
    (
        "Mofka is a persistent event-streaming framework for HPC.",
        "Mofka persistently streams every HPC event.", None, "not_found", "not_assessed",
    ),
    (
        "Mofka reduced latency by up to 8× in some scenarios.",
        THROUGHPUT_QUOTE, "unsupported", "present", "unsupported",
    ),
    (
        "Mofka is universally faster than every streaming system.",
        "Mofka is universally the fastest streaming system.", None, "not_found", "not_assessed",
    ),
    (
        "The paper reports up to an 8× throughput improvement in some scenarios.",
        THROUGHPUT_QUOTE.replace("some scenarios", "all scenarios"),
        None, "not_found", "not_assessed",
    ),
])
def test_quote_presence_gates_semantic_verification(
    tmp_path, monkeypatch, claim, quote, verifier_status, quote_status, support_status,
):
    from puba.distill import run

    pdf = _paper(tmp_path)
    verifier_calls = 0
    monkeypatch.setattr(run.llm, "text", lambda **_: _result("One Mofka claim."))

    def json_object(**kwargs):
        nonlocal verifier_calls
        if kwargs["model"] == "generation-model":
            return _result({"claims": [{
                "claim": claim, "evidence": [{"quote": quote}],
            }]}, model=kwargs["model"])
        verifier_calls += 1
        request = json.loads(kwargs["user"])
        return _result({
            "status": verifier_status,
            "rationale": "The passage does not entail the claim.",
            "evidence_ids": [item["id"] for item in request["evidence"]],
        }, model=kwargs["model"])

    monkeypatch.setattr(run.llm, "json_object", json_object)
    run.run_query(pdf, _query())
    record = _record(tmp_path)
    persisted = record["claims"][0]

    assert persisted["evidence"][0]["quote_status"] == quote_status
    assert persisted["support_assessment"]["status"] == support_status
    assert verifier_calls == (1 if quote_status == "present" else 0)

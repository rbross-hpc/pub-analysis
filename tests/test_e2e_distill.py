# BSD 3-Clause License
# Copyright (c) 2026, UChicago Argonne, LLC, Argonne National Laboratory.
"""End-to-end distillation tests using the dorier-mofka fixture.

Requires an authenticated OpenCode installation for the default `opencode` backend,
or direct API credentials when `llm.backend: openai` is configured.
Run with:   pytest tests/test_e2e_distill.py -v
Skip with:  pytest -m 'not network'
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"

pytestmark = pytest.mark.network


def test_native_opencode_one_shot_text_and_json() -> None:
    from puba import config, llm

    if config.llm_backend() != "opencode":
        pytest.skip("native OpenCode smoke test requires llm.backend=opencode")
    model = config.models()["distill"]
    text = llm.text(
        system="Reply concisely.", user="Reply with the word ready.", model=model,
    )
    structured = llm.json_object(
        system="Return the requested JSON object.",
        user='Return {"ready": true}.',
        model=model,
        validate=lambda value: value == {"ready": True},
    )

    assert text.value.strip()
    assert text.backend == structured.backend == "opencode"
    assert structured.value == {"ready": True}


def _copy_pdf(src_name: str, tmp_path: Path) -> Path:
    src = FIXTURES / src_name
    dst = tmp_path / src_name
    shutil.copy2(src, dst)
    return dst


def _load_bib(pdf_path: Path) -> dict:
    bib_json = pdf_path.parent / f"{pdf_path.stem}.puba" / "bib.json"
    assert bib_json.exists(), f"bib.json not found at {bib_json}"
    return json.loads(bib_json.read_text(encoding="utf-8"))


def _load_distillation(pdf_path: Path, name: str) -> dict:
    f = pdf_path.parent / f"{pdf_path.stem}.puba" / "analyses" / f"{name}.json"
    assert f.exists(), f"distillation file not found: {f}"
    return json.loads(f.read_text(encoding="utf-8"))


_PERSISTENT_QUOTE = (
    "We introduce Mofka, a persistent event-streaming framework designed specifically "
    "for HPC environments."
)
_MOCHI_QUOTE = "Mofka builds on the Mochi suite of technologies for developing HPC data services [49]."
_COMPARISON_QUOTE = (
    "We begin our empirical evaluation using carefully controlled synthetic benchmarks to compare "
    "Mofka with two existing persistent messaging systems, Kafka and Redpanda, on Argonne's Improv "
    "and Polaris, described hereafter."
)
_THROUGHPUT_QUOTE = (
    "Experiments on Argonne's Polaris and Improv supercomputers and Oak Ridge's Frontier exascale "
    "supercomputer show up to 8× improvement in throughput in some scenarios."
)
_APPLICATIONS_QUOTE = (
    "These use cases are 3D tomography reconstruction (TekApp), the discovery of novel metal-organic "
    "frameworks (MOFs) with AI-driven molecular simulations (MOFA), and the performance characterization "
    "and provenance of HPC workflows (Flowcept)."
)


@pytest.mark.parametrize("claim,quote,expected", [
    ("Mofka is a persistent event-streaming framework for HPC.", _PERSISTENT_QUOTE, "supported"),
    ("Mofka is built using Mochi components.", _MOCHI_QUOTE, "supported"),
    ("The evaluation compares Mofka with Kafka and Redpanda.", _COMPARISON_QUOTE, "supported"),
    (
        "The paper reports up to an 8× throughput improvement in some scenarios.",
        _THROUGHPUT_QUOTE, "supported",
    ),
    (
        "The paper discusses tomography, metal-organic-framework discovery, and workflow provenance.",
        _APPLICATIONS_QUOTE, "supported",
    ),
    ("Mofka is always 8× faster than Kafka and Redpanda.", _THROUGHPUT_QUOTE, "unsupported"),
    ("Every benchmark achieved an 8× improvement.", _THROUGHPUT_QUOTE, "unsupported"),
    ("Mofka reduced latency by up to 8× in some scenarios.", _THROUGHPUT_QUOTE, "unsupported"),
    (
        "The TekApp evaluation was performed on Frontier.",
        "We evaluated TekApp on the Polaris supercomputer.", "contradicted",
    ),
    ("MOFA is the paper's tomography reconstruction application.", _APPLICATIONS_QUOTE, "contradicted"),
    ("Mofka is a non-persistent framework.", _PERSISTENT_QUOTE, "contradicted"),
    ("Mofka achieved 8× higher throughput in every benchmark.", _MOCHI_QUOTE, "unsupported"),
])
def test_mofka_live_verifier_semantics(claim, quote, expected):
    from puba import config, llm
    from puba.distill.evidence import (
        build_verifier_context,
        is_valid_verifier_response,
        process_quote_presence,
    )
    from puba.distill.run import _VERIFIER_SYSTEM

    source = (FIXTURES / "dorier-mofka.puba" / "paper.md").read_text(encoding="utf-8")
    evidence = process_quote_presence([{"id": "c1e1", "quote": quote}], source)["evidence"]
    assert evidence[0]["quote_status"] == "present"
    contexts = build_verifier_context(source, evidence)
    user = json.dumps({
        "claim": claim,
        "evidence": [{"id": "c1e1", "quote": quote}],
        "contexts": contexts,
    }, sort_keys=True, separators=(",", ":"))

    result = llm.json_object(
        system=_VERIFIER_SYSTEM,
        user=user,
        model=config.models()["claim_verify"],
        validate=lambda value: is_valid_verifier_response(value, {"c1e1"}),
    )

    assert result.value["status"] == expected, result.value["rationale"]


# ---------------------------------------------------------------------------
# Mofka paper: dorier-mofka.pdf
# ANL Frontiers journal article, CC-BY 4.0
# DOI: 10.3389/fhpcp.2025.1638203  OSTI: 3002321
# Tests: bib resolution + distillation (summary, scope=abstract)
# ---------------------------------------------------------------------------

class TestDorierMofka:
    @pytest.fixture
    def pdf(self, tmp_path):
        return _copy_pdf("dorier-mofka.pdf", tmp_path)

    # --- Bib resolution ---

    @pytest.fixture(autouse=True)
    def _resolved_bib(self, pdf):
        from puba.bib.stub import resolve
        resolve(pdf, force=True)

    def test_bib_resolves(self, pdf):
        pass

    def test_doi(self, pdf):
        bib = _load_bib(pdf)
        assert bib["doi"] == "10.3389/fhpcp.2025.1638203"

    def test_osti_id(self, pdf):
        bib = _load_bib(pdf)
        assert bib["osti_id"] == "3002321"

    def test_year(self, pdf):
        bib = _load_bib(pdf)
        assert bib["year"] == 2025

    def test_category_journal(self, pdf):
        bib = _load_bib(pdf)
        assert bib["category"] == "journal article"

    def test_venue_frontiers(self, pdf):
        bib = _load_bib(pdf)
        assert "frontiers" in (bib["venue"] or "").lower()

    def test_author_dorier(self, pdf):
        bib = _load_bib(pdf)
        assert any(
            "dorier" in (author.get("name", "") if isinstance(author, dict) else author).lower()
            for author in (bib["authors"] or [])
        )

    def test_abstract_present(self, pdf):
        bib = _load_bib(pdf)
        assert bib.get("abstract") and len(bib["abstract"]) > 50

    def test_analysis_dir_layout(self, pdf):
        ad = pdf.parent / "dorier-mofka.puba"
        assert ad.is_dir()
        assert (ad / "bib.json").exists()
        assert (ad / ".state.json").exists()
        assert (ad / "analyses").is_dir()

    # --- Distillation ---

    def test_distill_summary_runs(self, pdf):
        from puba.distill.queries import load_queries
        from puba.distill.run import run_query
        queries = load_queries()
        assert "summary" in queries
        result = run_query(pdf, queries["summary"], force=True)
        assert result["status"] == "distilled", f"Expected distilled, got: {result}"

    def test_distill_summary_output_file_exists(self, pdf):
        from puba.distill.queries import load_queries
        from puba.distill.run import run_query
        queries = load_queries()
        run_query(pdf, queries["summary"], force=True)
        d = _load_distillation(pdf, "summary")
        assert d["schema_version"] == 2
        assert d["name"] == "summary"
        assert d["scope"] == "abstract"
        assert d["evidence_mode"] == "claims"
        assert d["output"].strip()
        assert d["generated_at"]
        assert isinstance(d["claims"], list)
        assert d["_provenance"]["backend"] == "opencode"
        assert d["_provenance"]["llm_protocol_version"] == "oneshot-v1"
        assert d["quote_presence_status"] in {"complete", "partial"}
        assert d["claim_support_status"] in {
            "supported", "partial", "unsupported", "contradicted", "not_applicable",
        }
        for claim in d["claims"]:
            assert claim["id"]
            assert claim["claim"].strip()
            assert isinstance(claim["evidence"], list)
            assessment = claim["support_assessment"]
            assert assessment["status"] in {
                "supported", "partially_supported", "unsupported", "contradicted",
                "insufficient_evidence", "not_assessed",
            }

    def test_distill_summary_output_within_max_chars(self, pdf):
        from puba.distill.queries import load_queries
        from puba.distill.run import run_query
        queries = load_queries()
        q = queries["summary"]
        run_query(pdf, q, force=True)
        d = _load_distillation(pdf, "summary")
        if q.max_chars:
            assert len(d["output"]) <= q.max_chars

    def test_distill_summary_provenance(self, pdf):
        from puba.distill.queries import load_queries
        from puba.distill.run import run_query
        queries = load_queries()
        run_query(pdf, queries["summary"], force=True)
        d = _load_distillation(pdf, "summary")
        prov = d["_provenance"]
        assert prov["prompt_sha256"]
        assert prov["input_sha256"]
        assert prov["bib_sha"]
        assert prov["at"]
        assert prov["tool_version"]
        assert prov["calls"]["generation"]["model"] == d["model"]
        assert prov["calls"]["atomization"]["model"] == d["model"]
        assert prov["calls"]["verification"]["model"] == d["verifier_model"]
        assert prov["backend"] == "opencode"
        assert prov["llm_protocol_version"] == "oneshot-v1"
        assert isinstance(prov["calls"]["verification"]["request_count"], int)

    def test_distill_summary_cached_on_rerun(self, pdf):
        from puba.distill.queries import load_queries
        from puba.distill.run import run_query
        queries = load_queries()
        run_query(pdf, queries["summary"], force=True)
        result2 = run_query(pdf, queries["summary"], force=False)
        assert result2["status"] == "cached"

    def test_distill_summary_utf8_output(self, pdf):
        from puba.distill.queries import load_queries
        from puba.distill.run import run_query
        queries = load_queries()
        run_query(pdf, queries["summary"], force=True)
        raw = (
            pdf.parent / "dorier-mofka.puba" / "analyses" / "summary.json"
        ).read_bytes()
        raw.decode("utf-8")

# BSD 3-Clause License
# Copyright (c) 2026, UChicago Argonne, LLC, Argonne National Laboratory.
"""Orchestrate bibliographic resolution: tier-1 parallel + fallback chain + provenance."""
from __future__ import annotations

import concurrent.futures
from pathlib import Path
from typing import Any

from .. import config as cfg
from ..io import now_iso
from ..sidecar import set_field, make_prov, load_bib, save_bib
from ..state import analysis_dir, ensure_analysis_dir, is_stage_current, mark_stage_complete
from ..bib.classify import classify
from ..bib.conflicts import detect_conflicts
from ..bib.sources._common import extract_doi, extract_arxiv_id
from ..bib.sources import openalex, crossref, arxiv, osti
from .. import __version__


def _first_pages_text(pdf_path: Path, n: int = 3) -> str:
    """Extract text from the first n pages, joined. Used for DOI/arXiv/title heuristics."""
    texts = []
    try:
        import pdfplumber
        with pdfplumber.open(str(pdf_path)) as pdf:
            for page in pdf.pages[:n]:
                t = page.extract_text() or ""
                if t.strip():
                    texts.append(t)
    except Exception:
        pass
    if not texts:
        try:
            from pypdf import PdfReader
            reader = PdfReader(str(pdf_path))
            for page in reader.pages[:n]:
                t = page.extract_text() or ""
                if t.strip():
                    texts.append(t)
        except Exception:
            pass
    return "\n\n".join(texts)


def _heuristic_title(text: str) -> str | None:
    """Extract a candidate title from PDF cover-page text heuristically.

    Looks for a short (10–120 char), non-all-caps, non-disclaimer line that
    appears early in the document. Scores lines by position (earlier = better)
    and title-case quality. Used when DOI/arXiv aren't on the page and LLM
    is disabled.
    """
    import re
    import unicodedata

    lines = [l.strip() for l in text.splitlines() if l.strip()]
    stopwords = {"abstract", "disclaimer", "preface", "contents", "acknowledgment",
                 "acknowledgements", "acknowledgement", "introduction", "references",
                 "appendix", "copyright", "rights"}
    skip_patterns = [
        re.compile(r'^(LLNL|ANL|ORNL|PNNL|SAND|NREL|BNL|SLAC|LA-UR|LA-|ANL-|ORNL/TM)\b'),
        re.compile(r'^(January|February|March|April|May|June|July|August|'
                   r'September|October|November|December)\s+\d{4}', re.IGNORECASE),
        re.compile(r'^\d{4}[-/]'),
        re.compile(r'^\d+$'),
        re.compile(r'^doi:', re.IGNORECASE),
        re.compile(r'^\d+\s+[a-z]'),  # numbered list items
    ]

    def _score(line: str, pos: int) -> float:
        if len(line) < 10 or len(line) > 150:
            return 0.0
        if line.isupper() and len(line) > 20:
            return 0.0
        first_word = line.split()[0].rstrip(':').lower() if line.split() else ''
        if first_word in stopwords:
            return 0.0
        for pat in skip_patterns:
            if pat.match(line):
                return 0.0
        # Prefer lines in first 15 lines, title-cased, medium length
        position_score = max(0.0, 1.0 - pos / 15.0)
        words = line.split()
        titled = sum(1 for w in words if w and w[0].isupper()) / max(len(words), 1)
        length_score = 1.0 - abs(len(line) - 50) / 100.0  # sweet spot ~50 chars
        return position_score * 0.5 + titled * 0.3 + length_score * 0.2

    scored = [(line, _score(line, i)) for i, line in enumerate(lines[:30])]
    scored = [(line, s) for line, s in scored if s > 0.1]
    if not scored:
        return None
    return max(scored, key=lambda x: x[1])[0]


def _apply_source(
    fields: dict[str, Any],
    prov: dict[str, Any],
    data: dict[str, Any],
    source: str,
    key: str,
    sim: float | None = None,
) -> None:
    for field in ("title", "authors", "year", "publication_date", "venue",
                  "doi", "url", "abstract", "keywords", "language",
                  "oa_status", "isbn", "issn"):
        val = data.get(field)
        set_field(fields, prov, field, val, source, key, sim)

    if data.get("arxiv_id"):
        set_field(fields, prov, "arxiv_id", data["arxiv_id"], source, key, sim)
    if data.get("osti_id"):
        set_field(fields, prov, "osti_id", data["osti_id"], source, key, sim)
    if data.get("bibtex_key"):
        set_field(fields, prov, "bibtex_key", data["bibtex_key"], source, key, sim)


def _derive_bibtex_key(fields: dict[str, Any]) -> str | None:
    authors = fields.get("authors") or []
    year = fields.get("year")
    title = fields.get("title") or ""
    if not authors or not year:
        return None
    surname = authors[0].split()[-1].lower() if authors[0].split() else "unknown"
    import re
    stopwords = {"a", "an", "the", "of", "in", "on", "for", "with", "and", "or"}
    words = re.sub(r"[^\w\s]", " ", title.lower()).split()
    sig_words = [w for w in words if w not in stopwords and w.isalpha()]
    first_word = sig_words[0] if sig_words else "paper"
    return f"{surname}{year}{first_word}"


def resolve(
    pdf_path: Path,
    force: bool = False,
    no_llm: bool = False,
    bibtex_file: Path | None = None,
) -> Path:
    """Resolve bibliographic information for pdf_path. Returns path to bib.yaml."""
    bib_cfg = cfg.bib()
    prompt_version = cfg.prompt_versions().get("bib_extract", "bib-1")

    ad = ensure_analysis_dir(pdf_path)

    if not force and is_stage_current(ad, pdf_path, "bib", prompt_version):
        return ad / "bib.yaml"

    # Load existing bib (preserves human-pinned fields)
    fields, prov = load_bib(ad)

    # Ensure all base fields exist
    for f in ("title", "authors", "year", "publication_date", "venue", "venue_short",
              "category", "doi", "arxiv_id", "osti_id", "isbn", "issn", "url",
              "abstract", "bibtex_key", "keywords", "language", "license", "oa_status",
              "references_count", "pages"):
        fields.setdefault(f, None)
    fields.setdefault("notes", "")

    lookup_log: dict[str, Any] = {}

    # --- PDF heuristics (always run; scan first 3 pages for DOI/arXiv) ---
    page1 = _first_pages_text(pdf_path, n=3)
    filename = pdf_path.name

    doi_from_pdf = extract_doi(page1)
    arxiv_from_pdf = extract_arxiv_id(page1, filename)

    if doi_from_pdf:
        set_field(fields, prov, "doi", doi_from_pdf, "pdf", f"pages-1-3 regex: {doi_from_pdf}")
    if arxiv_from_pdf:
        set_field(fields, prov, "arxiv_id", arxiv_from_pdf, "pdf", f"filename/pages-1-3: {arxiv_from_pdf}")

    # --- Title bootstrap: LLM first, heuristic as --no-llm fallback ---
    # Matches annual-report's pattern: get a title before tier-1 queries so
    # title-based searches are armed even when no DOI/arXiv is on the page.
    if not fields.get("title"):
        if not no_llm:
            from .sources import llm as llm_src
            llm_data = llm_src.extract_from_page1(page1)
            if llm_data and llm_data.get("title"):
                set_field(fields, prov, "title", llm_data.get("title"), "llm", "page-1 text")
                # Grab any other fields the LLM returned while we have it
                for field in ("authors", "year", "venue", "doi", "arxiv_id"):
                    if llm_data.get(field):
                        set_field(fields, prov, field, llm_data[field], "llm", "page-1 text")
                lookup_log["llm_bootstrap"] = {"status": "hit", "queried_at": now_iso()}
            else:
                lookup_log["llm_bootstrap"] = {"status": "failed", "queried_at": now_iso()}
        else:
            # --no-llm: fall back to PDF cover-page heuristic
            heuristic_title = _heuristic_title(page1)
            if heuristic_title:
                set_field(fields, prov, "title", heuristic_title, "pdf", "cover-page heuristic")
            lookup_log["llm_bootstrap"] = {"status": "not_attempted", "reason": "--no-llm"}
    else:
        lookup_log["llm_bootstrap"] = {"status": "not_attempted", "reason": "title already known"}

    # --- Tier-1: OpenAlex, CrossRef, OSTI in parallel ---
    doi_key = fields.get("doi")
    arxiv_key = fields.get("arxiv_id")
    title_key = fields.get("title")

    def _query_openalex() -> tuple[dict | None, float | None, str]:
        if doi_key:
            r, s = openalex.get_by_doi(doi_key)
            if r:
                return r, s, doi_key
        if arxiv_key:
            r, s = openalex.get_by_arxiv_id(arxiv_key)
            if r:
                return r, s, arxiv_key
        if title_key:
            r, s = openalex.search_by_title(title_key, fields.get("year"))
            return r, s, title_key
        return None, None, ""

    def _query_crossref() -> tuple[dict | None, float | None, str]:
        if doi_key:
            r, s = crossref.get_by_doi(doi_key)
            if r:
                return r, s, doi_key
        if title_key:
            r, s = crossref.search_by_title(title_key, fields.get("year"))
            return r, s, title_key
        return None, None, ""

    def _query_osti() -> tuple[dict | None, float | None, str]:
        if doi_key:
            r, s = osti.search_by_doi(doi_key)
            if r:
                return r, s, doi_key
        if title_key:
            r, s = osti.search_by_title(title_key)
            return r, s, title_key or ""
        return None, None, ""

    min_sim = bib_cfg.get("min_title_similarity", 0.90)

    tier1_results: dict[str, tuple[dict | None, float | None, str]] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as ex:
        fut_oa = ex.submit(_query_openalex)
        fut_cr = ex.submit(_query_crossref)
        fut_os = ex.submit(_query_osti)
        tier1_results["openalex"] = fut_oa.result()
        tier1_results["crossref"] = fut_cr.result()
        tier1_results["osti"] = fut_os.result()

    # Apply tier-1 in priority order; log all
    for src in ("osti", "openalex", "crossref"):
        data, sim, key = tier1_results[src]
        if data and (sim is None or sim >= min_sim):
            _apply_source(fields, prov, data, src, key, sim)
            lookup_log[src] = {"status": "hit", "key": key, "sim": sim, "queried_at": now_iso()}
        elif data:
            lookup_log[src] = {"status": "low_sim", "key": key, "sim": sim, "queried_at": now_iso()}
        else:
            lookup_log[src] = {"status": "no_match", "queried_at": now_iso()}

    # Detect conflicts among tier-1
    tier1_data = {src: tier1_results[src][0] for src in ("osti", "openalex", "crossref")}
    conflicts = detect_conflicts(tier1_data)

    # --- arXiv by ID (always run if we have an ID) ---
    if fields.get("arxiv_id"):
        arxiv_data = arxiv.get_by_id(fields["arxiv_id"])
        if arxiv_data:
            _apply_source(fields, prov, arxiv_data, "arxiv", fields["arxiv_id"])
            lookup_log["arxiv"] = {"status": "hit", "key": fields["arxiv_id"], "queried_at": now_iso()}
        else:
            lookup_log["arxiv"] = {"status": "no_match", "queried_at": now_iso()}
    else:
        lookup_log["arxiv"] = {"status": "not_attempted", "reason": "no arXiv ID available"}

    # Determine if tier-1 was sufficient (all three agreed via DOI)
    tier1_sufficient = (
        all(tier1_results[s][0] is not None for s in ("openalex", "crossref"))
        and all(tier1_results[s][1] == 1.0 for s in ("openalex", "crossref"))
        and not conflicts
        and fields.get("title")
        and fields.get("authors")
        and fields.get("year")
    )

    # --- Fallback chain (only if tier-1 left gaps) ---
    missing_core = not fields.get("title") or not fields.get("authors") or not fields.get("year")

    if not tier1_sufficient or missing_core:
        # DBLP
        if fields.get("title"):
            from .sources import dblp
            dblp_data, dblp_sim = dblp.search_by_title(fields["title"])
            if dblp_data and (dblp_sim is None or dblp_sim >= min_sim):
                _apply_source(fields, prov, dblp_data, "dblp", fields["title"], dblp_sim)
                lookup_log["dblp"] = {"status": "hit", "key": fields["title"], "sim": dblp_sim, "queried_at": now_iso()}
            else:
                lookup_log["dblp"] = {"status": "no_match", "queried_at": now_iso()}
        else:
            lookup_log["dblp"] = {"status": "not_attempted", "reason": "no title available"}

        # arXiv title search (only if still missing and no ID-based hit)
        if missing_core and fields.get("title") and "arxiv" not in lookup_log or lookup_log.get("arxiv", {}).get("status") == "not_attempted":
            arxiv_data, arxiv_sim = arxiv.search_by_title(fields.get("title", ""), fields.get("year"))
            if arxiv_data and (arxiv_sim is None or arxiv_sim >= min_sim):
                _apply_source(fields, prov, arxiv_data, "arxiv", fields.get("title", ""), arxiv_sim)
                lookup_log["arxiv_title"] = {"status": "hit", "sim": arxiv_sim, "queried_at": now_iso()}
            else:
                lookup_log["arxiv_title"] = {"status": "no_match", "queried_at": now_iso()}

        # BibTeX
        if bibtex_file:
            from .sources import bibtex as bib_src
            bib_sim_thresh = bib_cfg.get("bibtex_title_similarity", 0.85)
            bib_match = None
            bib_sim = None
            if fields.get("doi"):
                bib_match = bib_src.lookup_by_doi(fields["doi"], bibtex_file)
                if bib_match:
                    bib_sim = 1.0
            if not bib_match and fields.get("title"):
                bib_match, bib_sim = bib_src.lookup_by_title(fields["title"], bibtex_file, bib_sim_thresh)
            if bib_match:
                _apply_source(fields, prov, bib_match, "bibtex", bib_match.get("bibtex_key", ""), bib_sim)
                lookup_log["bibtex"] = {"status": "hit", "key": bib_match.get("bibtex_key"), "sim": bib_sim, "queried_at": now_iso()}
            else:
                lookup_log["bibtex"] = {"status": "no_match", "queried_at": now_iso()}
        else:
            lookup_log["bibtex"] = {"status": "not_attempted", "reason": "no --bibtex argument"}

        # LLM was already called at bootstrap (before tier-1); no second call here.
        if "llm" not in lookup_log:
            lookup_log["llm"] = {"status": "not_attempted", "reason": "handled at bootstrap"}

        # Semantic Scholar — absolute last resort
        if not fields.get("title") or not fields.get("authors"):
            from .sources import semanticscholar as ss
            ss_data, ss_sim = None, None
            if fields.get("doi"):
                ss_data, ss_sim = ss.get_by_doi(fields["doi"])
            if not ss_data and fields.get("title"):
                ss_data, ss_sim = ss.search_by_title(fields["title"], fields.get("year"))
            if ss_data and (ss_sim is None or ss_sim >= min_sim):
                _apply_source(fields, prov, ss_data, "semanticscholar", fields.get("doi") or fields.get("title", ""), ss_sim)
                lookup_log["semanticscholar"] = {"status": "hit", "sim": ss_sim, "queried_at": now_iso()}
            else:
                lookup_log["semanticscholar"] = {"status": "no_match", "queried_at": now_iso()}
        else:
            lookup_log["semanticscholar"] = {"status": "not_attempted", "reason": "tier-1 sufficient"}
    else:
        for src in ("dblp", "bibtex", "semanticscholar"):
            lookup_log[src] = {"status": "not_attempted", "reason": "all tier-1 sources confirmed"}
        if "llm" not in lookup_log:
            lookup_log["llm"] = {"status": "not_attempted", "reason": "all tier-1 sources confirmed"}

    # --- Category classification ---
    if prov.get("category", {}).get("source") not in ("human",):
        crossref_type = (tier1_results.get("crossref", (None, None, ""))[0] or {}).get("raw_type")
        cat, rule = classify(
            doi=fields.get("doi"),
            arxiv_id=fields.get("arxiv_id"),
            venue=fields.get("venue"),
            crossref_type=crossref_type,
        )
        set_field(fields, prov, "category", cat, "derived", f"derived:{rule}")

    # --- Bibtex key derivation ---
    if not fields.get("bibtex_key"):
        bk = _derive_bibtex_key(fields)
        if bk:
            set_field(fields, prov, "bibtex_key", bk, "derived", "derived:author-year-word")

    # --- Fill unknown provenance ---
    for field in ("title", "authors", "year", "publication_date", "venue", "category",
                  "doi", "arxiv_id", "osti_id", "url", "abstract"):
        if field not in prov:
            prov[field] = make_prov("unknown", None, note="not found in any source")

    # --- Save ---
    save_bib(
        analysis_dir=ad,
        pdf_path=pdf_path,
        fields=fields,
        prov=prov,
        lookup_log=lookup_log,
        conflicts=conflicts,
        tool_version=__version__,
        prompt_version=prompt_version,
    )

    mark_stage_complete(ad, pdf_path, "bib", prompt_version)
    return ad / "bib.yaml"

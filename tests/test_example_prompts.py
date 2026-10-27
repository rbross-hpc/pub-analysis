# BSD 3-Clause License
# Copyright (c) 2026, UChicago Argonne, LLC, Argonne National Laboratory.
"""Validate copyable distillation examples through the production loader."""
from __future__ import annotations

import shutil
from pathlib import Path

from puba.distill.queries import load_queries, validate_queries


_EXAMPLE = (
    Path(__file__).parents[1]
    / "examples"
    / "prompts"
    / "publication_assessment.yaml"
)
_QUERY_NAMES = {
    "publication_accomplishment",
    "discipline_impact",
    "domain_impact",
    "domain_classification",
}


def test_publication_assessment_example_loads_and_validates(tmp_path: Path) -> None:
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    shutil.copy2(_EXAMPLE, prompts / _EXAMPLE.name)

    loaded = load_queries(cwd=tmp_path)
    examples = {
        name: query
        for name, query in loaded.items()
        if Path(query.source).name == _EXAMPLE.name
    }

    assert set(examples) == _QUERY_NAMES
    assert validate_queries(examples) == []
    assert all(query.scope == "narrative" for query in examples.values())
    assert all(query.evidence_mode == "claims" for query in examples.values())
    assert all(query.prompt.strip() for query in examples.values())

# BSD 3-Clause License
# Copyright (c) 2026, UChicago Argonne, LLC, Argonne National Laboratory.
from puba.sidecar import set_field, priority


def test_priority_order():
    assert priority("human") > priority("openalex")
    assert priority("openalex") > priority("crossref")
    assert priority("crossref") > priority("dblp")
    assert priority("dblp") > priority("bibtex")
    assert priority("arxiv") > priority("pdf")
    assert priority("pdf") > priority("llm")
    assert priority("llm") > priority("derived")
    assert priority("derived") > priority("unknown")


def test_set_field_higher_priority_wins():
    fields: dict = {}
    prov: dict = {}
    set_field(fields, prov, "title", "Title from LLM", "llm")
    set_field(fields, prov, "title", "Title from OpenAlex", "openalex")
    assert fields["title"] == "Title from OpenAlex"
    assert prov["title"]["source"] == "openalex"


def test_set_field_lower_priority_does_not_overwrite():
    fields: dict = {}
    prov: dict = {}
    set_field(fields, prov, "title", "Title from OpenAlex", "openalex")
    set_field(fields, prov, "title", "Title from LLM", "llm")
    assert fields["title"] == "Title from OpenAlex"


def test_human_is_sticky():
    fields: dict = {}
    prov: dict = {}
    set_field(fields, prov, "title", "Human title", "human")
    set_field(fields, prov, "title", "OpenAlex title", "openalex")
    assert fields["title"] == "Human title"
    assert prov["title"]["source"] == "human"


def test_none_value_not_set():
    fields: dict = {}
    prov: dict = {}
    result = set_field(fields, prov, "title", None, "openalex")
    assert result is False
    assert "title" not in fields


def test_empty_list_not_set():
    fields: dict = {}
    prov: dict = {}
    result = set_field(fields, prov, "authors", [], "openalex")
    assert result is False


def test_invalid_category_rejected():
    fields: dict = {}
    prov: dict = {}
    result = set_field(fields, prov, "category", "not a valid category", "openalex")
    assert result is False

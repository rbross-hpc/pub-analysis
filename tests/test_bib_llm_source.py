from puba.bib.sources.llm import extract_from_initial_pages
from puba.llm.oneshot import OneShotResult


def test_bib_extraction_uses_resolved_model_and_one_shot_value(monkeypatch):
    captured = {}

    def json_object(**kwargs):
        captured.update(kwargs)
        assert kwargs["validate"]({"title": "Paper"})
        return OneShotResult({"title": "Paper"}, "opencode", kwargs["model"])

    monkeypatch.setattr("puba.bib.sources.llm.llm.json_object", json_object)
    assert extract_from_initial_pages("cover text", model="provider/model") == {
        "title": "Paper",
    }
    assert captured["model"] == "provider/model"


def test_bib_extraction_preserves_fallback_failure_policy(monkeypatch):
    monkeypatch.setattr(
        "puba.bib.sources.llm.llm.json_object",
        lambda **_: (_ for _ in ()).throw(RuntimeError("unavailable")),
    )
    assert extract_from_initial_pages("cover text", model="provider/model") is None

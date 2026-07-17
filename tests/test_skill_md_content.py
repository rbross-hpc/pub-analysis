# BSD 3-Clause License
# Copyright (c) 2026, UChicago Argonne, LLC, Argonne National Laboratory.
"""
Presence tests for puba/skills/publication-analysis/SKILL.md.

Mirrors the approach used in ref-checker's test_schema_prompt.py:
check that key names appear in both the doc and the code, so drift
between SKILL.md and cli.py is caught without a full schema validator.
"""
from __future__ import annotations

import ast
import re
from importlib.resources import as_file, files
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _skill_md() -> str:
    skill = files("puba").joinpath("skills/publication-analysis/SKILL.md")
    with as_file(skill) as p:
        return p.read_text(encoding="utf-8")


def _cli_src() -> str:
    return (Path(__file__).parent.parent / "puba" / "cli.py").read_text(encoding="utf-8")


def _emit_json_keys() -> set[str]:
    """Parse puba/cli.py and return every key used in a direct _emit_json({...}) call."""
    tree = ast.parse(_cli_src())
    keys: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else (
            func.attr if isinstance(func, ast.Attribute) else ""
        )
        if name == "_emit_json" and node.args:
            arg = node.args[0]
            if isinstance(arg, ast.Dict):
                for k in arg.keys:
                    if isinstance(k, ast.Constant):
                        keys.add(k.value)
    return keys


# ---------------------------------------------------------------------------
# Frontmatter
# ---------------------------------------------------------------------------

def test_frontmatter_present():
    md = _skill_md()
    assert md.startswith("---\n"), "SKILL.md must begin with YAML frontmatter"


def test_frontmatter_name():
    md = _skill_md()
    assert "name: publication-analysis" in md


def test_frontmatter_description():
    md = _skill_md()
    assert "description:" in md


# ---------------------------------------------------------------------------
# Key sections are present
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("heading", [
    "## Prerequisites",
    "## Typical workflow",
    "## Common invocations",
    "## Reviewing bib results",
    "## Defining distillation queries",
    "## JSON output for agents",
    "## Workflow guidance",
])
def test_section_present(heading):
    assert heading in _skill_md(), f"Section missing from SKILL.md: {heading!r}"


# ---------------------------------------------------------------------------
# Commands documented
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("cmd", [
    "puba bib",
    "puba bib edit",
    "puba md",
    "puba figures",
    "puba distill",
    "puba show bib",
    "puba show sections",
    "puba show section",
    "puba show distill",
    "puba show figures",
    "puba show info",
    "puba clean",
    "puba config",
])
def test_command_documented(cmd):
    assert cmd in _skill_md(), f"Command not documented in SKILL.md: {cmd!r}"


# ---------------------------------------------------------------------------
# Envelope keys: every key documented in SKILL.md exists in cli.py
# ---------------------------------------------------------------------------

def _md_quoted_keys() -> set[str]:
    """Extract every "key" token from SKILL.md JSON fenced blocks."""
    md = _skill_md()
    blocks = re.findall(r"```json\n(.*?)\n```", md, re.DOTALL)
    keys: set[str] = set()
    for block in blocks:
        keys.update(re.findall(r'"([a-z_]+)"(?:\s*:)', block))
    # also pick up table cells like `"ok"`, `"command"` outside code blocks
    keys.update(re.findall(r'`"([a-z_]+)"`', md))
    return keys


# Keys that appear in SKILL.md JSON examples but are not _emit_json keys in
# cli.py (e.g. values used as examples, or keys from nested objects the
# show info returns from state/sidecar rather than built inline).
_ALLOW_LIST = {
    "done",          # nested inside state.stages.*
    "status",        # distillation status string inside show info distillations list
    "stages",        # top-level key in state dict (from load_state, not _emit_json)
    "bib",           # show info passes through bib_data dict wholesale
    "md",            # bib_data field name
    "needs_review",  # field inside the bib dict (also a top-level _emit_json key)
    "review_reasons",# same — appears both ways
    "state",         # from load_state, passed through in show info envelope
    "authors",       # bib field shown in example
    "year",          # bib field shown in example
}


def test_md_keys_exist_in_code():
    """Every JSON key documented in SKILL.md must appear somewhere in cli.py
    (either as an _emit_json key or in the allow-list of nested/passthrough keys)."""
    code_keys = _emit_json_keys()
    md_keys = _md_quoted_keys()
    unknown = md_keys - code_keys - _ALLOW_LIST
    assert not unknown, (
        f"Keys in SKILL.md JSON examples not found in any _emit_json call:\n"
        f"  {sorted(unknown)}\n"
        f"Either add them to cli.py, fix the doc, or add to _ALLOW_LIST."
    )


# ---------------------------------------------------------------------------
# Envelope keys: core keys are documented in SKILL.md
# ---------------------------------------------------------------------------

_MUST_DOCUMENT = {
    "ok", "command", "pdf", "analysis_dir", "bib_yaml", "cached",
    "needs_review", "review_reasons", "error", "error_type",
    "fields_changed", "cleared_review",
    "output", "name", "scope", "model", "generated_at", "chars",
    "distillations", "count",
}


@pytest.mark.parametrize("key", sorted(_MUST_DOCUMENT))
def test_core_key_documented(key):
    md = _skill_md()
    # Accept the key appearing as "key", `"key"`, or `key` (backtick span / table cell)
    found = (
        f'"{key}"' in md
        or f'`"{key}"`' in md
        or f'`{key}`' in md
    )
    assert found, (
        f'Envelope key "{key}" (used in cli.py _emit_json calls) '
        f"is not documented in SKILL.md"
    )

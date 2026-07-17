"""puba skill subcommand — show and export the bundled Agent Skill."""
from __future__ import annotations

import shutil
import sys
from importlib.resources import as_file, files
from pathlib import Path

_SKILL_DIR_NAME = "publication-analysis"


def _skill_files():
    return files("puba").joinpath(f"skills/{_SKILL_DIR_NAME}")


def run_show() -> None:
    skill_md = _skill_files().joinpath("SKILL.md")
    with as_file(skill_md) as p:
        print(p.read_text(encoding="utf-8"), end="")


def run_export(path: Path, force: bool) -> None:
    dest = path.resolve()

    if dest.exists() and any(dest.iterdir()):
        if not force:
            print(
                f"Error: destination already exists and is non-empty: {dest}\n"
                "Use --force to overwrite.",
                file=sys.stderr,
            )
            sys.exit(1)
        shutil.rmtree(dest)

    skill_root = _skill_files()
    with as_file(skill_root) as src:
        shutil.copytree(src, dest, dirs_exist_ok=True)

    print(f"[puba] Skill exported to: {dest}", file=sys.stderr)

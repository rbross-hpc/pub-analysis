# BSD 3-Clause License
# Copyright (c) 2026, UChicago Argonne, LLC, Argonne National Laboratory.
"""puba CLI — single-paper bibliographic resolution and markdown rendering."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional

import typer
import yaml
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from . import __version__
from . import config as cfg

app = typer.Typer(
    name="puba",
    help="Single-paper bibliographic resolution and markdown rendering.",
    no_args_is_help=True,
)
config_app = typer.Typer(help="Configuration inspection and validation.")
app.add_typer(config_app, name="config")

show_app = typer.Typer(help="Read resolved outputs (bib, markdown, sections, info).")
app.add_typer(show_app, name="show")

_console = Console()
_err = Console(stderr=True)


def _quiet_option() -> bool:
    return False


def _ensure_bib(
    pdf: Path,
    force: bool,
    no_run: bool,
    as_json: bool,
    command: str,
) -> tuple[Path, bool]:
    """Return (bib_yaml_path, was_cached), running resolve() if needed.

    Raises typer.Exit on error (emitting JSON error when as_json=True).
    """
    from .state import analysis_dir, is_stage_current
    from . import config as _cfg

    ad = analysis_dir(pdf)
    prompt_version = _cfg.prompt_versions().get("bib_extract", "bib-1")
    already_current = ad.exists() and is_stage_current(ad, pdf, "bib", prompt_version)

    if no_run and not already_current:
        msg = "bib not resolved; run puba bib <pdf> first"
        if as_json:
            _emit_json({"ok": False, "command": command, "pdf": str(pdf),
                        "stage": "show.bib", "error": msg, "error_type": "CacheError"})
        else:
            _err.print(f"[red]Error:[/red] {msg}")
        raise typer.Exit(1)

    try:
        from .bib.stub import resolve
        return resolve(pdf, force=force)
    except RuntimeError as e:
        if as_json:
            _emit_json({"ok": False, "command": command, "pdf": str(pdf),
                        "stage": "bib", "error": str(e), "error_type": type(e).__name__})
        else:
            _err.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(2)
    except Exception as e:
        if as_json:
            _emit_json({"ok": False, "command": command, "pdf": str(pdf),
                        "stage": "bib", "error": str(e), "error_type": type(e).__name__})
        else:
            _err.print(f"[red]Failed:[/red] {e}")
        raise typer.Exit(1)


def _ensure_md(
    pdf: Path,
    force: bool,
    no_run: bool,
    as_json: bool,
    command: str,
) -> tuple[Path, bool]:
    """Return (paper_md_path, was_cached), running render() if needed.

    Raises typer.Exit on error (emitting JSON error when as_json=True).
    """
    from .state import analysis_dir, is_stage_current
    from . import config as _cfg

    ad = analysis_dir(pdf)
    prompt_version = _cfg.prompt_versions().get("md_cleanup", "md-cleanup-1")
    already_current = ad.exists() and is_stage_current(ad, pdf, "md", prompt_version)

    if no_run and not already_current:
        msg = "markdown not rendered; run puba md <pdf> first"
        if as_json:
            _emit_json({"ok": False, "command": command, "pdf": str(pdf),
                        "stage": "show.md", "error": msg, "error_type": "CacheError"})
        else:
            _err.print(f"[red]Error:[/red] {msg}")
        raise typer.Exit(1)

    try:
        from .md.render import render
        return render(pdf, force=force)
    except RuntimeError as e:
        if as_json:
            _emit_json({"ok": False, "command": command, "pdf": str(pdf),
                        "stage": "md", "error": str(e), "error_type": type(e).__name__})
        else:
            _err.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(2)
    except Exception as e:
        if as_json:
            _emit_json({"ok": False, "command": command, "pdf": str(pdf),
                        "stage": "md", "error": str(e), "error_type": type(e).__name__})
        else:
            _err.print(f"[red]Failed:[/red] {e}")
        raise typer.Exit(1)


def _emit_json(obj: dict) -> None:
    import sys
    print(json.dumps(obj, indent=2, default=str), file=sys.stdout, flush=True)


def _resolve_pdf(pdf: Path, as_json: bool = False, command: str = "") -> Path:
    pdf_abs = pdf.resolve()
    if not pdf_abs.exists():
        if as_json:
            _emit_json({"ok": False, "command": command, "pdf": str(pdf_abs),
                        "stage": "preflight", "error": "File not found",
                        "error_type": "FileNotFoundError"})
        else:
            _err.print(f"[red]File not found:[/red] {pdf_abs}")
        raise typer.Exit(1)
    if pdf_abs.suffix.lower() != ".pdf":
        if as_json:
            _emit_json({"ok": False, "command": command, "pdf": str(pdf_abs),
                        "stage": "preflight",
                        "error": f"Expected a PDF file, got: {pdf_abs.suffix}",
                        "error_type": "ValueError"})
        else:
            _err.print(f"[red]Expected a PDF file, got:[/red] {pdf_abs.suffix}")
        raise typer.Exit(1)
    return pdf_abs


@app.command()
def bib(
    pdf: Path = typer.Argument(..., help="Path to the publication PDF."),
    force: bool = typer.Option(False, "--force", help="Re-resolve even if cached."),
    no_llm: bool = typer.Option(False, "--no-llm", help="Skip LLM fallback extraction."),
    bibtex: Optional[Path] = typer.Option(None, "--bibtex", help="Path to a .bib file for fallback lookup."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would be queried without running."),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON result on stdout; implies --quiet."),
    quiet: bool = typer.Option(False, "-q", "--quiet", help="Suppress progress output."),
) -> None:
    """Resolve and write bibliographic information for a single PDF."""
    if as_json and dry_run:
        _emit_json({"ok": False, "command": "bib", "error": "--json and --dry-run are mutually exclusive",
                    "error_type": "UsageError"})
        raise typer.Exit(2)

    if as_json:
        quiet = True

    pdf = _resolve_pdf(pdf, as_json=as_json, command="bib")

    if dry_run:
        from .state import analysis_dir, is_stage_current
        from . import config as cfg
        ad = analysis_dir(pdf)
        prompt_version = cfg.prompt_versions().get("bib_extract", "bib-1")
        cached = ad.exists() and is_stage_current(ad, pdf, "bib", prompt_version)
        _console.print(f"[bold]Dry run:[/bold] {pdf.name}")
        _console.print(f"  Analysis dir : {ad}")
        _console.print(f"  Cached       : {'yes (would skip)' if cached and not force else 'no (would run)'}")
        _console.print(f"  Sources      : tier-1 parallel (openalex, crossref, osti) + fallback chain")
        _console.print(f"  LLM fallback : {'disabled (--no-llm)' if no_llm else 'enabled if needed'}")
        _console.print(f"  BibTeX       : {bibtex or 'none'}")
        return

    if not quiet:
        _err.print(f"[bold]puba bib[/bold] {pdf.name} ...")

    from .state import analysis_dir as _ad
    ad = _ad(pdf)

    try:
        from .bib.stub import resolve
        bib_path, was_cached = resolve(pdf, force=force, no_llm=no_llm, bibtex_file=bibtex)
    except RuntimeError as e:
        if as_json:
            _emit_json({"ok": False, "command": "bib", "pdf": str(pdf),
                        "analysis_dir": str(ad), "stage": "bib",
                        "error": str(e), "error_type": type(e).__name__})
        else:
            _err.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(2)
    except Exception as e:
        if as_json:
            _emit_json({"ok": False, "command": "bib", "pdf": str(pdf),
                        "analysis_dir": str(ad), "stage": "bib",
                        "error": str(e), "error_type": type(e).__name__})
        else:
            _err.print(f"[red]Failed:[/red] {e}")
        raise typer.Exit(1)

    bib_data = yaml.safe_load(bib_path.read_text(encoding="utf-8")) or {}
    needs_review = bool(bib_data.get("needs_review"))
    review_reasons = bib_data.get("_review_reasons") or []

    if as_json:
        out: dict = {"ok": True, "command": "bib", "pdf": str(pdf),
                     "analysis_dir": str(ad), "bib_yaml": str(bib_path),
                     "cached": was_cached, "needs_review": needs_review}
        if review_reasons:
            out["review_reasons"] = review_reasons
        _emit_json(out)
        if needs_review:
            raise typer.Exit(3)
        return

    if not quiet:
        cached_tag = " [dim](cached)[/dim]" if was_cached else ""
        _console.print(f"[green]bib written:[/green] {bib_path}{cached_tag}")
        if needs_review:
            _err.print("[yellow]Warning:[/yellow] needs_review=true — review bib.yaml:")
            for reason in review_reasons:
                _err.print(f"  [yellow]-[/yellow] {reason}")

    if needs_review:
        raise typer.Exit(3)


@app.command()
def md(
    pdf: Path = typer.Argument(..., help="Path to the publication PDF."),
    force: bool = typer.Option(False, "--force", help="Re-render even if cached."),
    backend: str = typer.Option("layered", "--backend", help="Extraction backend (currently only 'layered')."),
    no_llm_cleanup: bool = typer.Option(False, "--no-llm-cleanup", help="Skip LLM section cleanup."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would run without running."),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON result on stdout; implies --quiet."),
    quiet: bool = typer.Option(False, "-q", "--quiet", help="Suppress progress output."),
) -> None:
    """Render a clean markdown version of a PDF paper."""
    if as_json and dry_run:
        _emit_json({"ok": False, "command": "md", "error": "--json and --dry-run are mutually exclusive",
                    "error_type": "UsageError"})
        raise typer.Exit(2)

    if as_json:
        quiet = True

    pdf = _resolve_pdf(pdf, as_json=as_json, command="md")

    if backend != "layered":
        if as_json:
            _emit_json({"ok": False, "command": "md", "pdf": str(pdf),
                        "stage": "preflight",
                        "error": f"Unknown backend: {backend}. Only 'layered' is supported in v1.",
                        "error_type": "ValueError"})
        else:
            _err.print(f"[red]Unknown backend:[/red] {backend}. Only 'layered' is supported in v1.")
        raise typer.Exit(2)

    if dry_run:
        from .state import analysis_dir, is_stage_current
        ad = analysis_dir(pdf)
        prompt_version = cfg.prompt_versions().get("md_cleanup", "md-cleanup-1")
        cached = ad.exists() and is_stage_current(ad, pdf, "md", prompt_version)
        _console.print(f"[bold]Dry run:[/bold] {pdf.name}")
        _console.print(f"  Analysis dir : {ad}")
        _console.print(f"  Cached       : {'yes (would skip)' if cached and not force else 'no (would run)'}")
        _console.print(f"  Backend      : {backend}")
        _console.print(f"  LLM cleanup  : {'disabled (--no-llm-cleanup)' if no_llm_cleanup else 'enabled'}")
        return

    if not quiet:
        _err.print(f"[bold]puba md[/bold] {pdf.name} ...")

    from .state import analysis_dir as _ad
    ad = _ad(pdf)

    try:
        from .md.render import render
        md_path, was_cached = render(pdf, force=force, llm_cleanup=not no_llm_cleanup)
    except RuntimeError as e:
        if as_json:
            _emit_json({"ok": False, "command": "md", "pdf": str(pdf),
                        "analysis_dir": str(ad), "stage": "md",
                        "error": str(e), "error_type": type(e).__name__})
        else:
            _err.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(2)
    except Exception as e:
        if as_json:
            _emit_json({"ok": False, "command": "md", "pdf": str(pdf),
                        "analysis_dir": str(ad), "stage": "md",
                        "error": str(e), "error_type": type(e).__name__})
        else:
            _err.print(f"[red]Failed:[/red] {e}")
        raise typer.Exit(1)

    if as_json:
        _emit_json({"ok": True, "command": "md", "pdf": str(pdf),
                    "analysis_dir": str(ad),
                    "paper_md": str(md_path),
                    "paper_raw_txt": str(ad / "paper.raw.txt"),
                    "paper_sections_json": str(ad / "paper.sections.json"),
                    "cached": was_cached})
        return

    if not quiet:
        cached_tag = " [dim](cached)[/dim]" if was_cached else ""
        _console.print(f"[green]markdown written:[/green] {md_path}{cached_tag}")


@app.command()
def run(
    pdf: Path = typer.Argument(..., help="Path to the publication PDF."),
    force: bool = typer.Option(False, "--force", help="Re-run all stages even if cached."),
    no_llm_cleanup: bool = typer.Option(False, "--no-llm-cleanup", help="Skip LLM section cleanup in md stage."),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON result on stdout; implies --quiet."),
    quiet: bool = typer.Option(False, "-q", "--quiet", help="Suppress progress output."),
) -> None:
    """Run bib then md sequentially (full pipeline)."""
    if as_json:
        quiet = True

    pdf = _resolve_pdf(pdf, as_json=as_json, command="run")

    from .state import analysis_dir as _ad
    ad = _ad(pdf)

    if not quiet:
        _err.print(f"[bold]puba run[/bold] {pdf.name}")
        _err.print("  Stage 1/2: bib ...")

    try:
        from .bib.stub import resolve
        bib_path, bib_cached = resolve(pdf, force=force)
    except RuntimeError as e:
        if as_json:
            _emit_json({"ok": False, "command": "run", "pdf": str(pdf),
                        "analysis_dir": str(ad), "stage": "bib",
                        "stages": {"bib": {"ok": False, "error": str(e),
                                           "error_type": type(e).__name__}},
                        "error": str(e), "error_type": type(e).__name__})
        else:
            _err.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(2)
    except Exception as e:
        if as_json:
            _emit_json({"ok": False, "command": "run", "pdf": str(pdf),
                        "analysis_dir": str(ad), "stage": "bib",
                        "stages": {"bib": {"ok": False, "error": str(e),
                                           "error_type": type(e).__name__}},
                        "error": str(e), "error_type": type(e).__name__})
        else:
            _err.print(f"[red]bib failed:[/red] {e}")
        raise typer.Exit(1)

    bib_data = yaml.safe_load(bib_path.read_text(encoding="utf-8")) or {}
    bib_needs_review = bool(bib_data.get("needs_review"))
    bib_review_reasons = bib_data.get("_review_reasons") or []
    bib_stage: dict = {"ok": True, "bib_yaml": str(bib_path),
                       "cached": bib_cached, "needs_review": bib_needs_review}
    if bib_review_reasons:
        bib_stage["review_reasons"] = bib_review_reasons

    if bib_needs_review:
        if as_json:
            _emit_json({"ok": False, "command": "run", "pdf": str(pdf),
                        "analysis_dir": str(ad), "stage": "bib",
                        "stages": {"bib": bib_stage},
                        "error": "bib needs review before proceeding",
                        "error_type": "ReviewNeeded",
                        "review_reasons": bib_review_reasons})
        else:
            bib_tag = " [dim](cached)[/dim]" if bib_cached else ""
            _err.print(f"  [yellow]⚠[/yellow] bib → {bib_path}{bib_tag}")
            _err.print("[yellow]Warning:[/yellow] needs_review=true — fix bib.yaml before running md:")
            for reason in bib_review_reasons:
                _err.print(f"  [yellow]-[/yellow] {reason}")
        raise typer.Exit(3)

    if not quiet:
        bib_tag = " [dim](cached)[/dim]" if bib_cached else ""
        _err.print(f"  [green]✓[/green] bib → {bib_path}{bib_tag}")
        _err.print("  Stage 2/2: md ...")

    try:
        from .md.render import render
        md_path, md_cached = render(pdf, force=force, llm_cleanup=not no_llm_cleanup)
    except RuntimeError as e:
        if as_json:
            _emit_json({"ok": False, "command": "run", "pdf": str(pdf),
                        "analysis_dir": str(ad), "stage": "md",
                        "stages": {"bib": bib_stage,
                                   "md": {"ok": False, "error": str(e),
                                          "error_type": type(e).__name__}},
                        "error": str(e), "error_type": type(e).__name__})
        else:
            _err.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(2)
    except Exception as e:
        if as_json:
            _emit_json({"ok": False, "command": "run", "pdf": str(pdf),
                        "analysis_dir": str(ad), "stage": "md",
                        "stages": {"bib": bib_stage,
                                   "md": {"ok": False, "error": str(e),
                                          "error_type": type(e).__name__}},
                        "error": str(e), "error_type": type(e).__name__})
        else:
            _err.print(f"[red]md failed:[/red] {e}")
        raise typer.Exit(1)

    md_stage = {"ok": True, "paper_md": str(md_path),
                "paper_raw_txt": str(ad / "paper.raw.txt"),
                "paper_sections_json": str(ad / "paper.sections.json"),
                "cached": md_cached}

    if as_json:
        _emit_json({"ok": True, "command": "run", "pdf": str(pdf),
                    "analysis_dir": str(ad),
                    "stages": {"bib": bib_stage, "md": md_stage}})
        return

    if not quiet:
        md_tag = " [dim](cached)[/dim]" if md_cached else ""
        _err.print(f"  [green]✓[/green] md  → {md_path}{md_tag}")
        _console.print(f"\n[green]Done.[/green] Analysis directory: {bib_path.parent}")


@app.command()
def clean(
    pdf: Path = typer.Argument(..., help="Path to the publication PDF."),
    what: str = typer.Option("all", "--what", help="What to clean: bib | md | state | all"),
    quiet: bool = typer.Option(False, "-q", "--quiet"),
) -> None:
    """Remove cached outputs for a PDF."""
    pdf = _resolve_pdf(pdf)
    from .state import analysis_dir

    ad = analysis_dir(pdf)
    if not ad.exists():
        if not quiet:
            _console.print(f"[dim]Nothing to clean — {ad} does not exist.[/dim]")
        return

    targets = {
        "bib":     [ad / "bib.yaml"],
        "md":      [ad / "paper.md", ad / "paper.raw.txt", ad / "paper.sections.json"],
        "state":   [ad / ".state.json"],
        "distill": list((ad / "analyses").glob("*.yaml")) if (ad / "analyses").exists() else [],
        "all":     list(ad.glob("*")) + list((ad / "analyses").glob("*.yaml")) + [ad / ".state.json"],
    }

    files = targets.get(what)
    if files is None:
        _err.print(f"[red]Unknown --what value:[/red] {what}. Use: bib, md, state, distill, all")
        raise typer.Exit(2)

    for f in files:
        if f.exists() and f.is_file():
            f.unlink()
            if not quiet:
                _console.print(f"  removed {f.relative_to(ad)}")


@app.command()
def distill(
    pdf: Path = typer.Argument(..., help="Path to the publication PDF."),
    only: list[str] = typer.Option([], "--only", help="Run only this query (repeatable)."),
    force: bool = typer.Option(False, "--force", help="Re-run even if cached."),
    list_queries: bool = typer.Option(False, "--list", help="List defined queries and their status."),
    as_json: bool = typer.Option(False, "--json", help="Output --list as JSON."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would run without running."),
    quiet: bool = typer.Option(False, "-q", "--quiet"),
) -> None:
    """Distill a paper using configured queries (analyses/<name>.yaml)."""
    from .distill.queries import load_queries
    from .distill.run import list_distillations, run_query
    from .state import analysis_dir

    pdf = _resolve_pdf(pdf)
    ad = analysis_dir(pdf)

    try:
        all_queries = load_queries()
    except Exception as e:
        _err.print(f"[red]Failed to load queries:[/red] {e}")
        raise typer.Exit(2)

    if not all_queries:
        _err.print("[yellow]No distillation queries defined.[/yellow] "
                   "Add queries in config.yaml or prompts/*.yaml.")
        raise typer.Exit(0)

    if list_queries:
        from .pdf.sections import load_sections_json
        existing = {d["name"]: d for d in list_distillations(pdf)}
        available_sections = {s["short_name"] for s in load_sections_json(ad) if s.get("short_name")}
        if as_json:
            rows = []
            for name, q in all_queries.items():
                cached = name in existing
                missing_sec = (
                    q.scope == "section"
                    and q.section
                    and available_sections
                    and q.section not in available_sections
                )
                rows.append({
                    "name": name, "scope": q.scope,
                    "section": q.section,
                    "model": q.model or cfg.distill().get("default_model"),
                    "cached": cached,
                    "missing_section": missing_sec,
                    "generated_at": existing[name]["generated_at"] if cached else None,
                })
            _console.print(json.dumps(rows, indent=2))
        else:
            table = Table(show_header=True, header_style="bold", box=None, padding=(0, 1))
            table.add_column("Name", style="cyan")
            table.add_column("Scope")
            table.add_column("Target", style="dim")
            table.add_column("Model", style="dim")
            table.add_column("Status")
            table.add_column("Generated at", style="dim")
            for name, q in all_queries.items():
                target = q.section or ""
                model = q.model or cfg.distill().get("default_model", "?")
                if name in existing:
                    status = "[green]cached[/green]"
                    gen_at = existing[name]["generated_at"]
                elif (
                    q.scope == "section"
                    and q.section
                    and available_sections
                    and q.section not in available_sections
                ):
                    status = "[red]missing-section[/red]"
                    gen_at = "—"
                else:
                    status = "[dim]never-run[/dim]"
                    gen_at = "—"
                table.add_row(name, q.scope, target, model, status, gen_at)
            _console.print(table)
        return

    selected = {k: v for k, v in all_queries.items() if not only or k in only}
    if only:
        missing = set(only) - set(all_queries)
        if missing:
            _err.print(f"[red]Unknown query names:[/red] {', '.join(sorted(missing))}")
            raise typer.Exit(2)

    if dry_run:
        _console.print(f"[bold]Dry run:[/bold] {pdf.name} — {len(selected)} query(ies)")
        for name, q in selected.items():
            model = q.model or cfg.distill().get("default_model", "?")
            target = f" section={q.section}" if q.section else ""
            _console.print(f"  {name:<20} scope={q.scope:<10}{target} model={model}")
        return

    if not quiet:
        _err.print(f"[bold]puba distill[/bold] {pdf.name} — {len(selected)} query(ies)")

    failures = []
    for name, query in selected.items():
        if not quiet:
            _err.print(f"  {name} ...", end="")
        result = run_query(pdf, query, force=force)
        status = result["status"]
        if status == "distilled":
            if not quiet:
                truncated = " [yellow](truncated)[/yellow]" if result.get("truncated") else ""
                _err.print(f" [green]✓[/green] {result['chars']} chars{truncated}")
        elif status == "cached":
            if not quiet:
                _err.print(" [dim]cached[/dim]")
        elif status == "missing-section":
            if not quiet:
                _err.print(f" [red]✗ missing-section[/red]")
            _err.print(f"  [red]Error ({name}):[/red] {result['error']}")
            failures.append(name)
        elif status == "error":
            if not quiet:
                _err.print(f" [red]✗[/red]")
            _err.print(f"  [red]Error ({name}):[/red] {result['error']}")
            failures.append(name)

    if failures:
        _err.print(f"\n[red]{len(failures)} query(ies) failed:[/red] {', '.join(failures)}")
        raise typer.Exit(1)

    if not quiet and not failures:
        _console.print(f"[green]Done.[/green] Results in {ad / 'analyses'}")


@config_app.command("init")
def config_init(
    path: Optional[Path] = typer.Option(None, "--path", help="Destination directory or file. Default: ./puba.config.yaml"),
    force: bool = typer.Option(False, "--force", help="Overwrite if file already exists."),
    quiet: bool = typer.Option(False, "-q", "--quiet"),
) -> None:
    """Copy the packaged config.yaml into the current directory as puba.config.yaml."""
    import shutil
    from . import config as _cfg

    packaged = _cfg.packaged_config_path()

    if path is None:
        dest = _cfg.local_config_path()
    elif path.is_dir():
        dest = path / "puba.config.yaml"
    else:
        dest = path
        if dest.name != "puba.config.yaml":
            _err.print(
                f"[yellow]Warning:[/yellow] destination filename is {dest.name!r}. "
                f"Only 'puba.config.yaml' in the current working directory is loaded automatically."
            )

    if dest.exists() and not force:
        _err.print(
            f"[red]File already exists:[/red] {dest}\n"
            "Use [bold]--force[/bold] to overwrite."
        )
        raise typer.Exit(1)

    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(packaged, dest)

    if not quiet:
        _console.print(f"[green]wrote[/green] {dest}")
        _console.print(
            "Edit this file to override packaged defaults. "
            "Run [bold]puba config show[/bold] to see which keys resolve from project-local."
        )


@config_app.command("show")
def config_show(
    quiet: bool = typer.Option(False, "-q"),
) -> None:
    """Print the resolved configuration and the source of each key."""
    _console.print(cfg.show())


@config_app.command("validate")
def config_validate(
    quiet: bool = typer.Option(False, "-q"),
) -> None:
    """Validate configuration: compile regexes, check enums, verify env vars."""
    errors = cfg.validate()
    if errors:
        for e in errors:
            _err.print(f"[red]✗[/red] {e}")
        raise typer.Exit(1)
    if not quiet:
        _console.print("[green]✓[/green] Configuration is valid.")


@show_app.command("bib")
def show_bib(
    pdf: Path = typer.Argument(..., help="Path to the publication PDF."),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON result on stdout; implies --quiet."),
    verbose: bool = typer.Option(False, "--verbose", help="Include conflicts, lookup_log, and meta in JSON output."),
    force: bool = typer.Option(False, "--force", help="Re-resolve even if cached."),
    no_run: bool = typer.Option(False, "--no-run", help="Error instead of auto-running resolution."),
    quiet: bool = typer.Option(False, "-q", "--quiet"),
) -> None:
    """Show resolved bibliographic information for a PDF (auto-resolves if needed)."""
    if as_json:
        quiet = True

    pdf = _resolve_pdf(pdf, as_json=as_json, command="show.bib")

    from .state import analysis_dir as _ad
    from .sidecar import load_clean

    bib_path, was_cached = _ensure_bib(pdf, force=force, no_run=no_run,
                                        as_json=as_json, command="show.bib")
    ad = _ad(pdf)
    clean = load_clean(pdf, include_verbose=verbose)

    if as_json:
        out: dict = {
            "ok": True, "command": "show.bib",
            "pdf": str(pdf), "analysis_dir": str(ad),
            "cached": was_cached,
            "needs_review": clean.get("needs_review", False),
            "review_reasons": clean.get("review_reasons", []),
            "bib": clean.get("fields", {}),
            "provenance": clean.get("provenance", {}),
        }
        if verbose:
            out["conflicts"] = clean.get("conflicts", {})
            out["lookup_log"] = clean.get("lookup_log", {})
            out["meta"] = clean.get("meta", {})
        _emit_json(out)
        return

    bib_data = clean.get("fields", {})
    prov = clean.get("provenance", {})
    needs_review = clean.get("needs_review", False)
    review_reasons = clean.get("review_reasons", [])

    if not quiet:
        cached_tag = " [dim](cached)[/dim]" if was_cached else ""
        _console.print(f"\n[bold]puba show bib[/bold]: {pdf.name}{cached_tag}")

    if needs_review:
        _console.print("\n[yellow]  ⚠ needs_review=true — review bib.yaml:[/yellow]")
        for reason in review_reasons:
            _console.print(f"  [yellow]  - {reason}[/yellow]")

    table = Table(show_header=True, header_style="bold", box=None, padding=(0, 1))
    table.add_column("Field", style="cyan", min_width=18)
    table.add_column("Value")
    table.add_column("Source", style="dim")

    for field in ("title", "authors", "year", "venue", "category", "doi",
                  "arxiv_id", "osti_id", "url", "abstract", "keywords",
                  "language", "oa_status", "references_count", "pages"):
        val = bib_data.get(field)
        if val is None:
            continue
        if isinstance(val, list):
            display = "; ".join(str(v) for v in val[:3])
            if len(val) > 3:
                display += f" (+{len(val)-3})"
        else:
            display = str(val)
        if len(display) > 80:
            display = display[:77] + "..."
        source = prov.get(field, {}).get("source", "")
        table.add_row(field, display, source)

    _console.print(table)


@show_app.command("md")
def show_md(
    pdf: Path = typer.Argument(..., help="Path to the publication PDF."),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON result on stdout; implies --quiet."),
    include_content: bool = typer.Option(False, "--include-content",
                                         help="Inline markdown text and sections list into JSON (requires --json)."),
    force: bool = typer.Option(False, "--force", help="Re-render even if cached."),
    no_run: bool = typer.Option(False, "--no-run", help="Error instead of auto-running render."),
    quiet: bool = typer.Option(False, "-q", "--quiet"),
) -> None:
    """Show rendered markdown for a PDF (auto-renders if needed)."""
    if include_content and not as_json:
        _emit_json({"ok": False, "command": "show.md",
                    "error": "--include-content requires --json",
                    "error_type": "UsageError"})
        raise typer.Exit(2)

    if as_json:
        quiet = True

    pdf = _resolve_pdf(pdf, as_json=as_json, command="show.md")

    from .state import analysis_dir as _ad
    from .pdf.sections import load_sections_json

    md_path, was_cached = _ensure_md(pdf, force=force, no_run=no_run,
                                      as_json=as_json, command="show.md")
    ad = _ad(pdf)

    if as_json:
        out: dict = {
            "ok": True, "command": "show.md",
            "pdf": str(pdf), "analysis_dir": str(ad),
            "paper_md": str(md_path),
            "paper_raw_txt": str(ad / "paper.raw.txt"),
            "paper_sections_json": str(ad / "paper.sections.json"),
            "cached": was_cached,
        }
        if include_content:
            out["content"] = md_path.read_text(encoding="utf-8") if md_path.exists() else ""
            out["sections"] = load_sections_json(ad)
        _emit_json(out)
        return

    if md_path.exists():
        print(md_path.read_text(encoding="utf-8"), end="")
    else:
        _err.print(f"[red]paper.md not found:[/red] {md_path}")
        raise typer.Exit(1)


@show_app.command("sections")
def show_sections(
    pdf: Path = typer.Argument(..., help="Path to the publication PDF."),
    as_json: bool = typer.Option(False, "--json", help="Output raw sections list as JSON."),
    force: bool = typer.Option(False, "--force", help="Re-render markdown to re-detect sections."),
    no_run: bool = typer.Option(False, "--no-run", help="Error instead of auto-running render."),
    quiet: bool = typer.Option(False, "-q", "--quiet"),
) -> None:
    """List detected sections for a PDF (auto-renders if needed)."""
    if as_json:
        quiet = True

    pdf = _resolve_pdf(pdf, as_json=as_json, command="show.sections")

    from .state import analysis_dir as _ad
    from .pdf.sections import load_sections_json

    _ensure_md(pdf, force=force, no_run=no_run, as_json=as_json, command="show.sections")
    ad = _ad(pdf)
    secs = load_sections_json(ad)

    if as_json:
        _emit_json(secs)
        return

    if not secs:
        _console.print(
            "[dim]No sections detected. Heading detection patterns may need tuning "
            "(see docs/configuration.md).[/dim]"
        )
        return

    table = Table(show_header=True, header_style="bold", box=None, padding=(0, 1))
    table.add_column("short_name", style="cyan", min_width=20)
    table.add_column("level", justify="right")
    table.add_column("title")
    for s in secs:
        indent = "  " * max(0, s.get("level", 1) - 1)
        table.add_row(s["short_name"], str(s.get("level", "")), indent + s.get("title", ""))
    _console.print(table)


@show_app.command("distill")
def show_distill(
    pdf: Path = typer.Argument(..., help="Path to the publication PDF."),
    name: Optional[str] = typer.Argument(None, help="Distillation name. Required for plain output."),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON envelope including provenance."),
    all_: bool = typer.Option(False, "--all", help="Emit all distillations (requires --json)."),
    quiet: bool = typer.Option(False, "-q", "--quiet"),
) -> None:
    """Show the output of a named distillation (plain text or JSON)."""
    if as_json:
        quiet = True

    if all_ and not as_json:
        msg = "--all requires --json"
        _err.print(f"[red]Error:[/red] {msg}")
        raise typer.Exit(2)

    if all_ and name:
        msg = "--all and NAME are mutually exclusive"
        if as_json:
            _emit_json({"ok": False, "command": "show.distill", "pdf": str(pdf),
                        "stage": "preflight", "error": msg, "error_type": "UsageError"})
        else:
            _err.print(f"[red]Error:[/red] {msg}")
        raise typer.Exit(2)

    pdf = _resolve_pdf(pdf, as_json=as_json, command="show.distill")

    from .state import analysis_dir as _ad

    ad = _ad(pdf)
    analyses_dir = ad / "analyses"

    if not analyses_dir.exists() or not any(analyses_dir.glob("*.yaml")):
        msg = f"No distillations found for {pdf.name}. Run 'puba distill {pdf.name}' first."
        if as_json:
            _emit_json({"ok": False, "command": "show.distill", "pdf": str(pdf),
                        "analysis_dir": str(ad), "stage": "show.distill",
                        "error": msg, "error_type": "FileNotFoundError"})
        else:
            _err.print(f"[red]Error:[/red] {msg}")
        raise typer.Exit(1)

    available = sorted(f.stem for f in analyses_dir.glob("*.yaml"))

    if not name and not all_:
        msg = f"No distillation name given. Available: {', '.join(available)}"
        if as_json:
            _emit_json({"ok": False, "command": "show.distill", "pdf": str(pdf),
                        "analysis_dir": str(ad), "stage": "preflight",
                        "error": msg, "error_type": "UsageError", "available": available})
        else:
            _err.print(f"[red]Error:[/red] {msg}")
        raise typer.Exit(2)

    if all_:
        records = []
        for f in sorted(analyses_dir.glob("*.yaml")):
            try:
                data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
            except Exception as e:
                _emit_json({"ok": False, "command": "show.distill", "pdf": str(pdf),
                            "analysis_dir": str(ad), "stage": "show.distill",
                            "error": f"Corrupt YAML in {f}: {e}",
                            "error_type": type(e).__name__, "bad_file": str(f)})
                raise typer.Exit(1)
            output = data.get("output", "")
            records.append({
                "name": data.get("name", f.stem),
                "scope": data.get("scope"),
                "section": data.get("section"),
                "model": data.get("model"),
                "generated_at": data.get("generated_at"),
                "chars": len(output),
                "output": output,
                "_provenance": data.get("_provenance"),
            })
        _emit_json({"ok": True, "command": "show.distill", "pdf": str(pdf),
                    "analysis_dir": str(ad), "count": len(records),
                    "distillations": records})
        return

    target = analyses_dir / f"{name}.yaml"
    if not target.exists():
        msg = f"No such distillation '{name}'. Available: {', '.join(available)}"
        if as_json:
            _emit_json({"ok": False, "command": "show.distill", "pdf": str(pdf),
                        "analysis_dir": str(ad), "stage": "show.distill",
                        "error": msg, "error_type": "FileNotFoundError",
                        "available": available})
        else:
            _err.print(f"[red]Error:[/red] {msg}")
        raise typer.Exit(2)

    try:
        data = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    except Exception as e:
        msg = f"Corrupt YAML in {target}: {e}"
        if as_json:
            _emit_json({"ok": False, "command": "show.distill", "pdf": str(pdf),
                        "analysis_dir": str(ad), "stage": "show.distill",
                        "error": msg, "error_type": type(e).__name__, "bad_file": str(target)})
        else:
            _err.print(f"[red]Error:[/red] {msg}")
        raise typer.Exit(1)

    output = data.get("output", "")

    if as_json:
        _emit_json({"ok": True, "command": "show.distill", "pdf": str(pdf),
                    "analysis_dir": str(ad),
                    "name": data.get("name", name),
                    "scope": data.get("scope"),
                    "section": data.get("section"),
                    "model": data.get("model"),
                    "generated_at": data.get("generated_at"),
                    "chars": len(output),
                    "output": output,
                    "_provenance": data.get("_provenance")})
        return

    print(output)


@show_app.command("info")
def show_info(
    pdf: Path = typer.Argument(..., help="Path to the publication PDF."),
    as_json: bool = typer.Option(False, "--json", help="Output as JSON instead of Rich table."),
    quiet: bool = typer.Option(False, "-q", "--quiet"),
) -> None:
    """Show combined status: bib summary, stage cache, and distillations."""
    if as_json:
        quiet = True

    pdf = _resolve_pdf(pdf, as_json=as_json, command="show.info")

    from .state import analysis_dir, load_state
    from .sidecar import bib_path
    from .distill.run import list_distillations

    ad = analysis_dir(pdf)
    state = load_state(ad)
    bp = bib_path(ad)
    bib_data = {}
    if bp.exists():
        bib_data = yaml.safe_load(bp.read_text(encoding="utf-8")) or {}

    distillations = list_distillations(pdf)

    if as_json:
        out = {
            "pdf": str(pdf),
            "analysis_dir": str(ad),
            "state": state,
            "bib": {k: v for k, v in bib_data.items() if not k.startswith("_")},
            "review_reasons": bib_data.get("_review_reasons") or [],
            "distillations": [
                {k: v for k, v in d.items() if k != "path"} for d in distillations
            ],
        }
        _emit_json(out)
        return

    _console.print(f"\n[bold]puba show info[/bold]: {pdf.name}")
    _console.print(f"  Analysis dir: {ad}")

    if bib_data.get("needs_review"):
        review_reasons = bib_data.get("_review_reasons") or []
        _console.print("\n[yellow]  ⚠ needs_review=true — review bib.yaml:[/yellow]")
        for reason in review_reasons:
            _console.print(f"  [yellow]  - {reason}[/yellow]")

    prov = bib_data.get("_provenance") or {}
    table = Table(show_header=True, header_style="bold", box=None, padding=(0, 1))
    table.add_column("Field", style="cyan", min_width=18)
    table.add_column("Value")
    table.add_column("Source", style="dim")
    for field in ("title", "authors", "year", "venue", "category", "doi", "arxiv_id", "osti_id", "url"):
        val = bib_data.get(field)
        if val is None:
            continue
        if isinstance(val, list):
            display = "; ".join(str(v) for v in val[:3])
            if len(val) > 3:
                display += f" (+{len(val)-3})"
        else:
            display = str(val)
        if len(display) > 80:
            display = display[:77] + "..."
        source = prov.get(field, {}).get("source", "")
        table.add_row(field, display, source)
    _console.print(table)

    stages = state.get("stages", {})
    if stages:
        _console.print("\n  [bold]Stage cache:[/bold]")
        for stage, info_s in stages.items():
            if stage == "distill":
                continue
            completed = info_s.get("completed_at", "—")
            _console.print(f"    {stage:<8} completed {completed}")
    else:
        _console.print("\n  [dim]No stages run yet.[/dim]")

    if distillations:
        _console.print("\n  [bold]Distillations:[/bold]")
        dtable = Table(show_header=True, header_style="bold", box=None, padding=(0, 1))
        dtable.add_column("Name", style="cyan", min_width=16)
        dtable.add_column("Scope")
        dtable.add_column("Target", style="dim")
        dtable.add_column("Model", style="dim")
        dtable.add_column("Chars")
        dtable.add_column("Generated at", style="dim")
        for d in distillations:
            dtable.add_row(
                d["name"], d["scope"],
                d.get("section") or "",
                d["model"],
                str(d["chars"]), d["generated_at"],
            )
        _console.print(dtable)


if __name__ == "__main__":
    app()

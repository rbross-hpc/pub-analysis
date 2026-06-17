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

_console = Console()
_err = Console(stderr=True)


def _quiet_option() -> bool:
    return False


def _resolve_pdf(pdf: Path) -> Path:
    pdf = pdf.resolve()
    if not pdf.exists():
        _err.print(f"[red]File not found:[/red] {pdf}")
        raise typer.Exit(1)
    if pdf.suffix.lower() != ".pdf":
        _err.print(f"[red]Expected a PDF file, got:[/red] {pdf.suffix}")
        raise typer.Exit(1)
    return pdf


@app.command()
def bib(
    pdf: Path = typer.Argument(..., help="Path to the publication PDF."),
    force: bool = typer.Option(False, "--force", help="Re-resolve even if cached."),
    no_llm: bool = typer.Option(False, "--no-llm", help="Skip LLM fallback extraction."),
    bibtex: Optional[Path] = typer.Option(None, "--bibtex", help="Path to a .bib file for fallback lookup."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would be queried without running."),
    quiet: bool = typer.Option(False, "-q", "--quiet", help="Suppress progress output."),
) -> None:
    """Resolve and write bibliographic information for a single PDF."""
    pdf = _resolve_pdf(pdf)

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

    try:
        from .bib.stub import resolve
        bib_path = resolve(pdf, force=force, no_llm=no_llm, bibtex_file=bibtex)
    except RuntimeError as e:
        _err.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(2)
    except Exception as e:
        _err.print(f"[red]Failed:[/red] {e}")
        raise typer.Exit(1)

    if not quiet:
        _console.print(f"[green]bib written:[/green] {bib_path}")
        bib_data = yaml.safe_load(bib_path.read_text(encoding="utf-8")) or {}
        if bib_data.get("needs_review"):
            _err.print("[yellow]Warning:[/yellow] needs_review=true — sources disagreed on one or more fields. Review bib.yaml.")


@app.command()
def md(
    pdf: Path = typer.Argument(..., help="Path to the publication PDF."),
    force: bool = typer.Option(False, "--force", help="Re-render even if cached."),
    backend: str = typer.Option("layered", "--backend", help="Extraction backend (currently only 'layered')."),
    no_llm_cleanup: bool = typer.Option(False, "--no-llm-cleanup", help="Skip LLM section cleanup."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would run without running."),
    quiet: bool = typer.Option(False, "-q", "--quiet", help="Suppress progress output."),
) -> None:
    """Render a clean markdown version of a PDF paper."""
    pdf = _resolve_pdf(pdf)

    if backend != "layered":
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

    try:
        from .md.render import render
        md_path = render(pdf, force=force, llm_cleanup=not no_llm_cleanup)
    except RuntimeError as e:
        _err.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(2)
    except Exception as e:
        _err.print(f"[red]Failed:[/red] {e}")
        raise typer.Exit(1)

    if not quiet:
        _console.print(f"[green]markdown written:[/green] {md_path}")


@app.command()
def run(
    pdf: Path = typer.Argument(..., help="Path to the publication PDF."),
    force: bool = typer.Option(False, "--force", help="Re-run all stages even if cached."),
    no_llm_cleanup: bool = typer.Option(False, "--no-llm-cleanup", help="Skip LLM section cleanup in md stage."),
    quiet: bool = typer.Option(False, "-q", "--quiet", help="Suppress progress output."),
) -> None:
    """Run bib then md sequentially (full pipeline)."""
    pdf = _resolve_pdf(pdf)

    if not quiet:
        _err.print(f"[bold]puba run[/bold] {pdf.name}")
        _err.print("  Stage 1/2: bib ...")

    try:
        from .bib.stub import resolve
        bib_path = resolve(pdf, force=force)
    except RuntimeError as e:
        _err.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(2)
    except Exception as e:
        _err.print(f"[red]bib failed:[/red] {e}")
        raise typer.Exit(1)

    if not quiet:
        _err.print(f"  [green]✓[/green] bib → {bib_path}")
        _err.print("  Stage 2/2: md ...")

    try:
        from .md.render import render
        md_path = render(pdf, force=force, llm_cleanup=not no_llm_cleanup)
    except RuntimeError as e:
        _err.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(2)
    except Exception as e:
        _err.print(f"[red]md failed:[/red] {e}")
        raise typer.Exit(1)

    if not quiet:
        _err.print(f"  [green]✓[/green] md  → {md_path}")
        _console.print(f"\n[green]Done.[/green] Analysis directory: {bib_path.parent}")


@app.command()
def info(
    pdf: Path = typer.Argument(..., help="Path to the publication PDF."),
    as_json: bool = typer.Option(False, "--json", help="Output as JSON instead of Rich table."),
    quiet: bool = typer.Option(False, "-q", "--quiet"),
) -> None:
    """Show bibliographic summary and stage status for a PDF."""
    pdf = _resolve_pdf(pdf)
    from .state import analysis_dir, load_state
    from .sidecar import bib_path

    ad = analysis_dir(pdf)
    state = load_state(ad)
    bib_yaml = bib_path(ad)
    bib_data = {}
    if bib_yaml.exists():
        bib_data = yaml.safe_load(bib_yaml.read_text(encoding="utf-8")) or {}

    if as_json:
        out = {
            "pdf": str(pdf),
            "analysis_dir": str(ad),
            "state": state,
            "bib": {k: v for k, v in bib_data.items() if not k.startswith("_")},
        }
        _console.print(json.dumps(out, indent=2, default=str))
        return

    _console.print(f"\n[bold]puba info[/bold]: {pdf.name}")
    _console.print(f"  Analysis dir: {ad}")
    _console.print(f"  PDF exists  : {pdf.exists()}")

    if bib_data.get("needs_review"):
        _console.print("\n[yellow]  ⚠ needs_review=true — sources disagreed. Review bib.yaml.[/yellow]")

    table = Table(show_header=True, header_style="bold", box=None, padding=(0, 1))
    table.add_column("Field", style="cyan", min_width=18)
    table.add_column("Value")
    table.add_column("Source", style="dim")

    prov = bib_data.get("_provenance") or {}
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
            completed = info_s.get("completed_at", "—")
            _console.print(f"    {stage:<8} completed {completed}")
    else:
        _console.print("\n  [dim]No stages run yet.[/dim]")


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
        "bib":   [ad / "bib.yaml"],
        "md":    [ad / "paper.md", ad / "paper.raw.txt", ad / "paper.sections.json"],
        "state": [ad / ".state.json"],
        "all":   list(ad.glob("*")) + [ad / ".state.json"],
    }

    files = targets.get(what)
    if files is None:
        _err.print(f"[red]Unknown --what value:[/red] {what}. Use: bib, md, state, all")
        raise typer.Exit(2)

    for f in files:
        if f.exists() and f.is_file():
            f.unlink()
            if not quiet:
                _console.print(f"  removed {f.name}")


@app.command()
def distill(
    pdf: Path = typer.Argument(..., help="Path to the publication PDF."),
) -> None:
    """[Phase 2 — not yet implemented] Distill paper into structured summaries."""
    _console.print("[yellow]puba distill is planned for phase 2 and is not yet implemented.[/yellow]")
    _console.print("Outputs will be written to the analyses/ subdirectory of the .puba analysis dir.")
    raise typer.Exit(0)


@app.command()
def ask(
    pdf: Path = typer.Argument(..., help="Path to the publication PDF."),
    question: str = typer.Argument(..., help="Question to ask about the paper."),
) -> None:
    """[Phase 2 — not yet implemented] Ask a question about a paper."""
    _console.print("[yellow]puba ask is planned for phase 2 and is not yet implemented.[/yellow]")
    raise typer.Exit(0)


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


if __name__ == "__main__":
    app()

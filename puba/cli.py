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

    from .distill.run import list_distillations
    distillations = list_distillations(pdf)

    if as_json:
        out = {
            "pdf": str(pdf),
            "analysis_dir": str(ad),
            "state": state,
            "bib": {k: v for k, v in bib_data.items() if not k.startswith("_")},
            "distillations": [
                {k: v for k, v in d.items() if k != "path"} for d in distillations
            ],
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
def sections(
    pdf: Path = typer.Argument(..., help="Path to the publication PDF."),
    as_json: bool = typer.Option(False, "--json", help="Output raw paper.sections.json."),
    quiet: bool = typer.Option(False, "-q", "--quiet"),
) -> None:
    """List the detected sections of a paper (requires puba md to have been run)."""
    pdf = _resolve_pdf(pdf)
    from .state import analysis_dir
    from .pdf.sections import load_sections_json

    ad = analysis_dir(pdf)
    sections_file = ad / "paper.sections.json"

    if not sections_file.exists():
        _err.print(
            f"[red]paper.sections.json not found.[/red] "
            f"Run [bold]puba md {pdf.name}[/bold] first to detect sections."
        )
        raise typer.Exit(1)

    secs = load_sections_json(ad)

    if not secs:
        _console.print(
            "[dim]No sections detected in this PDF. "
            "Heading detection patterns may need tuning "
            "(see docs/configuration.md).[/dim]"
        )
        return

    if as_json:
        _console.print(sections_file.read_text(encoding="utf-8"))
        return

    table = Table(show_header=True, header_style="bold", box=None, padding=(0, 1))
    table.add_column("short_name", style="cyan", min_width=20)
    table.add_column("level", justify="right")
    table.add_column("title")
    for s in secs:
        indent = "  " * max(0, s.get("level", 1) - 1)
        table.add_row(s["short_name"], str(s.get("level", "")), indent + s.get("title", ""))
    _console.print(table)


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


@app.command()
def ask(
    pdf: Path = typer.Argument(..., help="Path to the publication PDF."),
    question: str = typer.Argument(..., help="Question to ask about the paper."),
) -> None:
    """Ask a question about a paper (not yet implemented)."""
    _console.print("[yellow]puba ask is not yet implemented.[/yellow]")
    raise typer.Exit(0)


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


if __name__ == "__main__":
    app()

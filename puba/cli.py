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

class _BibFallbackGroup(typer.core.TyperGroup):
    """Custom group that routes bare 'puba bib <pdf>' to the default command."""
    def parse_args(self, ctx, args):
        if args and not args[0].startswith("-") and args[0] not in self.commands:
            args = ["__default__"] + args
        return super().parse_args(ctx, args)


bib_app = typer.Typer(
    cls=_BibFallbackGroup,
    help="Bibliographic resolution and editing.",
)
app.add_typer(bib_app, name="bib")

_console = Console()
_err = Console(stderr=True)


def _quiet_option() -> bool:
    return False


def _require_cached_bib(pdf: Path, as_json: bool, command: str) -> Path:
    """Return bib_yaml_path if the bib stage is cached. Error and exit 1 otherwise.

    Never triggers resolution. Callers that need to run bib should invoke
    `puba bib <pdf>` directly.
    """
    from .state import analysis_dir, is_stage_current
    from . import config as _cfg

    ad = analysis_dir(pdf)
    prompt_version = _cfg.prompt_versions().get("bib_extract", "bib-1")
    if not (ad.exists() and is_stage_current(ad, pdf, "bib", prompt_version)):
        msg = "bib not resolved; run puba bib <pdf> first"
        if as_json:
            _emit_json({"ok": False, "command": command, "pdf": str(pdf),
                        "stage": "show.bib", "error": msg, "error_type": "CacheError"})
        else:
            _err.print(f"[red]Error:[/red] {msg}")
        raise typer.Exit(1)
    return ad / "bib.yaml"


def _require_cached_md(pdf: Path, as_json: bool, command: str) -> Path:
    """Return paper_md_path if the md stage is cached. Error and exit 1 otherwise.

    Never triggers rendering. Callers that need to run md should invoke
    `puba md <pdf>` directly.
    """
    from .state import analysis_dir, is_stage_current
    from . import config as _cfg

    ad = analysis_dir(pdf)
    mineru_version = _cfg.md().get("mineru_version", "mineru-1")
    if not (ad.exists() and is_stage_current(ad, pdf, "md", mineru_version)):
        msg = "markdown not rendered; run puba md <pdf> first"
        if as_json:
            _emit_json({"ok": False, "command": command, "pdf": str(pdf),
                        "stage": "show.md", "error": msg, "error_type": "CacheError"})
        else:
            _err.print(f"[red]Error:[/red] {msg}")
        raise typer.Exit(1)
    return ad / "paper.md"


def _require_resolved_bib(pdf: Path, as_json: bool, command: str) -> dict:
    """Enforce: bib.yaml exists and needs_review is false. Exit 3 on either failure.

    Returns the parsed bib dict on success.
    """
    from .state import analysis_dir as _ad
    ad = _ad(pdf)
    bib_path = ad / "bib.yaml"

    if not bib_path.exists():
        msg = "bib.yaml not found; run `puba bib <pdf>` first"
        if as_json:
            _emit_json({"ok": False, "command": command, "pdf": str(pdf),
                        "analysis_dir": str(ad), "stage": "preflight",
                        "error": msg, "error_type": "BibMissing"})
        else:
            _err.print(f"[red]Error:[/red] {msg}")
        raise typer.Exit(3)

    bib_data = yaml.safe_load(bib_path.read_text(encoding="utf-8")) or {}
    needs_review = bool(bib_data.get("needs_review"))
    review_reasons = bib_data.get("_review_reasons") or []

    if needs_review:
        if as_json:
            out: dict = {"ok": True, "command": command, "pdf": str(pdf),
                         "analysis_dir": str(ad),
                         "needs_review": True,
                         "error_type": "ReviewNeeded"}
            if review_reasons:
                out["review_reasons"] = review_reasons
            _emit_json(out)
        else:
            _err.print("[yellow]Warning:[/yellow] needs_review=true — fix bib.yaml before rendering:")
            for reason in review_reasons:
                _err.print(f"  [yellow]-[/yellow] {reason}")
        raise typer.Exit(3)

    return bib_data


def _emit_json(obj: dict) -> None:
    import sys
    print(json.dumps(obj, indent=2, default=str), file=sys.stdout, flush=True)


_EMBED_MAX_LONG_SIDE = 2048


def _embed_jpeg(jpg_path: Path) -> str:
    """Return a base64 data URL for the given JPG, downsampled to fit within
    _EMBED_MAX_LONG_SIDE pixels on the longest side.

    Images already within the limit are returned unchanged.
    Uses fitz (pymupdf) for arbitrary-scale resize.
    """
    import base64
    import fitz

    pm = fitz.Pixmap(str(jpg_path))
    w, h = pm.width, pm.height
    pm = None

    if max(w, h) <= _EMBED_MAX_LONG_SIDE:
        image_bytes = jpg_path.read_bytes()
    else:
        factor = _EMBED_MAX_LONG_SIDE / max(w, h)
        doc = fitz.open(str(jpg_path))
        page = doc[0]
        pt_w = page.rect.width
        px_factor = (w * factor) / pt_w
        mat = fitz.Matrix(px_factor, px_factor)
        pm_scaled = page.get_pixmap(matrix=mat)
        image_bytes = pm_scaled.tobytes("jpeg")
        doc.close()

    b64 = base64.b64encode(image_bytes).decode("utf-8")
    return f"data:image/jpeg;base64,{b64}"


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


@bib_app.callback()
def bib_group() -> None:
    """Bibliographic resolution and editing."""


@bib_app.command("__default__", hidden=True)
def bib(
    pdf: Path = typer.Argument(..., help="Path to the publication PDF."),
    force: bool = typer.Option(False, "--force", help="Re-resolve even if cached."),
    no_llm: bool = typer.Option(False, "--no-llm", help="Skip LLM fallback extraction."),
    bibtex: Optional[Path] = typer.Option(
        None, "--bibtex",
        help="Path to a .bib file for fallback lookup.",
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
        resolve_path=True,
    ),
    model: Optional[str] = typer.Option(None, "--model", help="Override LLM model for this invocation (e.g. 'Claude Sonnet 4.6')."),
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
        resolved_model = model or cfg.models().get("bib_extract", "GPT-5.4")
        cached = ad.exists() and is_stage_current(ad, pdf, "bib", prompt_version, model=resolved_model)
        _console.print(f"[bold]Dry run:[/bold] {pdf.name}")
        _console.print(f"  Analysis dir : {ad}")
        _console.print(f"  Cached       : {'yes (would skip)' if cached and not force else 'no (would run)'}")
        _console.print(f"  Sources      : tier-1 parallel (openalex, crossref, osti) + fallback chain")
        _console.print(f"  LLM fallback : {'disabled (--no-llm)' if no_llm else 'enabled if needed'}")
        _console.print(f"  LLM model    : {resolved_model}")
        _console.print(f"  BibTeX       : {bibtex or 'none'}")
        return

    if not quiet:
        _err.print(f"[bold]puba bib[/bold] {pdf.name} ...")

    from .state import analysis_dir as _ad
    ad = _ad(pdf)

    try:
        from .bib.stub import resolve
        bib_path, was_cached = resolve(pdf, force=force, no_llm=no_llm, bibtex_file=bibtex, model=model)
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


def _parse_set_value(raw: str) -> Any:
    """Parse a --set field=value string value. Tries JSON decode first, falls back to str."""
    if raw == "null":
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return raw


@bib_app.command("edit")
def bib_edit(
    pdf: Path = typer.Argument(..., help="Path to the publication PDF."),
    json_file: Optional[str] = typer.Option(
        None, "--json-file",
        help="Path to a JSON patch file, or '-' to read from stdin.",
    ),
    set_: Optional[list[str]] = typer.Option(
        None, "--set",
        help="Set a single field: field=value. Repeatable. null removes the field.",
    ),
    source: str = typer.Option(
        "human", "--source",
        help="Provenance source: 'human' (default) or 'tool:<name>'.",
    ),
    note: Optional[str] = typer.Option(None, "--note", help="Optional note recorded in provenance."),
    clear_review: bool = typer.Option(False, "--clear-review",
                                      help="Set needs_review=false and remove _review_reasons."),
    dry_run: bool = typer.Option(False, "--dry-run",
                                 help="Print the proposed changes without writing anything."),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON result on stdout."),
    quiet: bool = typer.Option(False, "-q", "--quiet"),
) -> None:
    """Apply a JSON field patch to bib.yaml with sticky provenance."""
    from .sidecar import EDIT_SOURCE_RE, _load_raw, apply_patch, _validate_patch_field, _ALL_FIELDS
    from .state import analysis_dir as _ad

    if as_json:
        quiet = True

    if json_file and set_:
        msg = "--json-file and --set are mutually exclusive"
        if as_json:
            _emit_json({"ok": False, "command": "bib.edit", "error": msg, "error_type": "UsageError"})
        else:
            _err.print(f"[red]Error:[/red] {msg}")
        raise typer.Exit(2)

    if not EDIT_SOURCE_RE.match(source):
        msg = f"Invalid --source {source!r}. Must be 'human' or 'tool:<name>'."
        if as_json:
            _emit_json({"ok": False, "command": "bib.edit", "error": msg, "error_type": "ValueError"})
        else:
            _err.print(f"[red]Error:[/red] {msg}")
        raise typer.Exit(2)

    pdf = _resolve_pdf(pdf, as_json=as_json, command="bib.edit")
    _require_cached_bib(pdf, as_json=as_json, command="bib.edit")
    ad = _ad(pdf)

    patch_fields: dict[str, Any] = {}

    if json_file:
        try:
            if json_file == "-":
                import sys as _sys
                raw_text = _sys.stdin.read()
            else:
                raw_text = Path(json_file).read_text(encoding="utf-8")
            patch_fields = json.loads(raw_text)
        except (OSError, json.JSONDecodeError) as e:
            msg = f"Failed to read patch: {e}"
            if as_json:
                _emit_json({"ok": False, "command": "bib.edit", "pdf": str(pdf),
                            "error": msg, "error_type": type(e).__name__})
            else:
                _err.print(f"[red]Error:[/red] {msg}")
            raise typer.Exit(1)
        if not isinstance(patch_fields, dict):
            msg = "Patch must be a JSON object (dict), not a list or scalar."
            if as_json:
                _emit_json({"ok": False, "command": "bib.edit", "pdf": str(pdf),
                            "error": msg, "error_type": "ValueError"})
            else:
                _err.print(f"[red]Error:[/red] {msg}")
            raise typer.Exit(2)

    elif set_:
        for item in set_:
            if "=" not in item:
                msg = f"--set requires 'field=value' format, got: {item!r}"
                if as_json:
                    _emit_json({"ok": False, "command": "bib.edit", "pdf": str(pdf),
                                "error": msg, "error_type": "ValueError"})
                else:
                    _err.print(f"[red]Error:[/red] {msg}")
                raise typer.Exit(2)
            field, _, raw_val = item.partition("=")
            patch_fields[field.strip()] = _parse_set_value(raw_val)

    if not patch_fields and not clear_review:
        msg = "Nothing to do: no fields specified and --clear-review not set."
        if as_json:
            _emit_json({"ok": False, "command": "bib.edit", "pdf": str(pdf),
                        "error": msg, "error_type": "UsageError"})
        else:
            _err.print(f"[yellow]Warning:[/yellow] {msg}")
        raise typer.Exit(0)

    for field in patch_fields:
        if field.startswith("_"):
            msg = f"Cannot patch underscore-prefixed key {field!r}."
            if as_json:
                _emit_json({"ok": False, "command": "bib.edit", "pdf": str(pdf),
                            "error": msg, "error_type": "ValueError"})
            else:
                _err.print(f"[red]Error:[/red] {msg}")
            raise typer.Exit(2)
        if field not in {*_ALL_FIELDS, "notes", "needs_review"}:
            msg = f"Unknown field {field!r}."
            if as_json:
                _emit_json({"ok": False, "command": "bib.edit", "pdf": str(pdf),
                            "error": msg, "error_type": "ValueError"})
            else:
                _err.print(f"[red]Error:[/red] {msg}")
            raise typer.Exit(2)
        try:
            _validate_patch_field(field, patch_fields[field])
        except ValueError as e:
            if as_json:
                _emit_json({"ok": False, "command": "bib.edit", "pdf": str(pdf),
                            "error": str(e), "error_type": "ValueError"})
            else:
                _err.print(f"[red]Error:[/red] {e}")
            raise typer.Exit(2)

    if dry_run:
        raw = _load_raw(ad)
        if as_json:
            diff = {}
            for field, new_val in patch_fields.items():
                old_val = raw.get(field)
                diff[field] = {"before": old_val, "after": new_val}
            out: dict = {
                "ok": True, "command": "bib.edit", "pdf": str(pdf),
                "analysis_dir": str(ad), "dry_run": True,
                "source": source, "clear_review": clear_review,
                "diff": diff,
            }
            _emit_json(out)
        else:
            _console.print(f"[bold]Dry run:[/bold] bib edit {pdf.name}")
            table = Table(show_header=True, header_style="bold", box=None, padding=(0, 1))
            table.add_column("Field", style="cyan", min_width=18)
            table.add_column("Before", style="dim")
            table.add_column("After")
            for field, new_val in patch_fields.items():
                old_val = raw.get(field)
                table.add_row(field, str(old_val), str(new_val))
            if clear_review:
                table.add_row("needs_review", str(raw.get("needs_review", False)), "False")
                table.add_row("_review_reasons", str(raw.get("_review_reasons")), "(removed)")
            _console.print(table)
            _console.print(f"  Source: {source}" + (f"  Note: {note}" if note else ""))
        return

    try:
        result = apply_patch(ad, pdf, patch_fields, source=source, note=note, clear_review=clear_review)
    except ValueError as e:
        if as_json:
            _emit_json({"ok": False, "command": "bib.edit", "pdf": str(pdf),
                        "analysis_dir": str(ad), "error": str(e), "error_type": "ValueError"})
        else:
            _err.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(2)
    except Exception as e:
        if as_json:
            _emit_json({"ok": False, "command": "bib.edit", "pdf": str(pdf),
                        "analysis_dir": str(ad), "error": str(e), "error_type": type(e).__name__})
        else:
            _err.print(f"[red]Failed:[/red] {e}")
        raise typer.Exit(1)

    if as_json:
        _emit_json({
            "ok": True, "command": "bib.edit",
            "pdf": str(pdf), "analysis_dir": str(ad),
            "bib_yaml": result["bib_yaml"],
            "fields_changed": result["fields_changed"],
            "cleared_review": result["cleared_review"],
            "source": source,
            "dry_run": False,
        })
        return

    if not quiet:
        n = len(result["fields_changed"])
        _console.print(f"[green]bib edited:[/green] {n} field(s) updated ({source})")
        for field in result["fields_changed"]:
            _console.print(f"  [cyan]{field}[/cyan]")
        if result["cleared_review"]:
            _console.print("  [green]needs_review → false[/green]")


@app.command()
def md(
    pdf: Path = typer.Argument(..., help="Path to the publication PDF."),
    force: bool = typer.Option(False, "--force", help="Re-render even if cached."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would run without running."),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON result on stdout; implies --quiet."),
    quiet: bool = typer.Option(False, "-q", "--quiet", help="Suppress progress output."),
) -> None:
    """Render a clean markdown version of a PDF paper via MinerU."""
    if as_json and dry_run:
        _emit_json({"ok": False, "command": "md", "error": "--json and --dry-run are mutually exclusive",
                    "error_type": "UsageError"})
        raise typer.Exit(2)

    if as_json:
        quiet = True

    pdf = _resolve_pdf(pdf, as_json=as_json, command="md")

    if dry_run:
        from .state import analysis_dir, is_stage_current
        ad = analysis_dir(pdf)
        mineru_version = cfg.md().get("mineru_version", "mineru-1")
        cached = ad.exists() and is_stage_current(ad, pdf, "md", mineru_version)
        _console.print(f"[bold]Dry run:[/bold] {pdf.name}")
        _console.print(f"  Analysis dir   : {ad}")
        _console.print(f"  Cached         : {'yes (would skip)' if cached and not force else 'no (would run)'}")
        _console.print(f"  Backend        : mineru pipeline")
        _console.print(f"  MinerU version : {mineru_version}")
        return

    _require_resolved_bib(pdf, as_json=as_json, command="md")

    if not quiet:
        _err.print(f"[bold]puba md[/bold] {pdf.name} ...")

    from .state import analysis_dir as _ad
    ad = _ad(pdf)

    try:
        from .md.render import render
        md_path, was_cached = render(pdf, force=force)
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
                    "paper_sections_json": str(ad / "paper.sections.json"),
                    "cached": was_cached})
        return

    if not quiet:
        cached_tag = " [dim](cached)[/dim]" if was_cached else ""
        _console.print(f"[green]markdown written:[/green] {md_path}{cached_tag}")


@app.command()
def figures(
    pdf: Path = typer.Argument(..., help="Path to the publication PDF."),
    force: bool = typer.Option(False, "--force", help="Re-extract even if cached."),
    types: Optional[str] = typer.Option(
        None, "--types",
        help="Comma-separated figure types to include: image,chart,table (default: all three).",
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON result on stdout; implies --quiet."),
    quiet: bool = typer.Option(False, "-q", "--quiet", help="Suppress progress output."),
) -> None:
    """Extract figure artifacts (JPG crops + manifest) from MinerU layout output."""
    if as_json:
        quiet = True

    pdf = _resolve_pdf(pdf, as_json=as_json, command="figures")

    _require_resolved_bib(pdf, as_json=as_json, command="figures")
    _require_cached_md(pdf, as_json=as_json, command="figures")

    active_types: set[str] | None = None
    if types is not None:
        active_types = {t.strip() for t in types.split(",") if t.strip()}
        valid = {"image", "chart", "table"}
        bad = active_types - valid
        if bad:
            msg = f"Unknown figure types: {sorted(bad)}. Valid: image, chart, table"
            if as_json:
                _emit_json({"ok": False, "command": "figures", "pdf": str(pdf),
                            "stage": "preflight", "error": msg, "error_type": "ValueError"})
            else:
                _err.print(f"[red]Error:[/red] {msg}")
            raise typer.Exit(2)

    from .state import analysis_dir as _ad
    ad = _ad(pdf)

    if not quiet:
        _err.print(f"[bold]puba figures[/bold] {pdf.name} ...")

    try:
        from .figures.extract import extract
        manifest = extract(pdf, types=active_types, force=force)
    except RuntimeError as e:
        if as_json:
            _emit_json({"ok": False, "command": "figures", "pdf": str(pdf),
                        "analysis_dir": str(ad), "stage": "figures",
                        "error": str(e), "error_type": type(e).__name__})
        else:
            _err.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(2)
    except Exception as e:
        if as_json:
            _emit_json({"ok": False, "command": "figures", "pdf": str(pdf),
                        "analysis_dir": str(ad), "stage": "figures",
                        "error": str(e), "error_type": type(e).__name__})
        else:
            _err.print(f"[red]Failed:[/red] {e}")
        raise typer.Exit(1)

    n = len(manifest.get("figures", []))
    manifest_path = ad / "paper.figures.json"

    if as_json:
        _emit_json({"ok": True, "command": "figures", "pdf": str(pdf),
                    "analysis_dir": str(ad), "manifest": str(manifest_path),
                    "figures_count": n})
        return

    if not quiet:
        _console.print(f"[green]figures extracted:[/green] {n} figure(s) → {manifest_path}")


@app.command()
def clean(
    pdf: Path = typer.Argument(..., help="Path to the publication PDF."),
    what: str = typer.Option("all", "--what", help="What to clean: bib | md | figures | state | all"),
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

    targets: dict[str, list[Path]] = {
        "bib":     [ad / "bib.yaml"],
        "md":      [ad / "paper.md", ad / "paper.sections.json", ad / "mineru"],
        "figures": [ad / "paper.figures.json", ad / "figures"],
        "state":   [ad / ".state.json"],
        "distill": list((ad / "analyses").glob("*.yaml")) if (ad / "analyses").exists() else [],
        "all":     list(ad.glob("*")) + list((ad / "analyses").glob("*.yaml")) + [ad / ".state.json"],
    }

    files = targets.get(what)
    if files is None:
        _err.print(f"[red]Unknown --what value:[/red] {what}. Use: bib, md, figures, state, distill, all")
        raise typer.Exit(2)

    import shutil as _shutil
    for f in files:
        if f.exists():
            if f.is_dir():
                _shutil.rmtree(f)
                if not quiet:
                    _console.print(f"  removed {f.relative_to(ad)}/")
            else:
                f.unlink()
                if not quiet:
                    _console.print(f"  removed {f.relative_to(ad)}")

    if what in {"bib", "md", "figures", "distill"}:
        from .state import invalidate_stage
        invalidate_stage(ad, what)
        if not quiet:
            _console.print(f"  invalidated state: {what}")


@app.command()
def distill(
    pdf: Path = typer.Argument(..., help="Path to the publication PDF."),
    only: list[str] = typer.Option([], "--only", help="Run only this query (repeatable)."),
    force: bool = typer.Option(False, "--force", help="Re-run even if cached."),
    model: Optional[str] = typer.Option(None, "--model", help="Override LLM model for all queries in this invocation (e.g. 'Claude Sonnet 4.6')."),
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
                    "model": model or q.model or cfg.models().get("distill", "GPT-5.4"),
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
                model_display = model or q.model or cfg.models().get("distill", "GPT-5.4")
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
                table.add_row(name, q.scope, target, model_display, status, gen_at)
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
            model_display = model or q.model or cfg.models().get("distill", "GPT-5.4")
            target = f" section={q.section}" if q.section else ""
            _console.print(f"  {name:<20} scope={q.scope:<10}{target} model={model_display}")
        return

    if not quiet:
        _err.print(f"[bold]puba distill[/bold] {pdf.name} — {len(selected)} query(ies)")

    failures = []
    for name, query in selected.items():
        if not quiet:
            _err.print(f"  {name} ...", end="")
        result = run_query(pdf, query, force=force, model_override=model)
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
    writable: bool = typer.Option(False, "--writable",
                                   help="Emit just the fields dict as JSON for piping into `puba bib edit --json-file -`. Implies --json."),
    quiet: bool = typer.Option(False, "-q", "--quiet"),
) -> None:
    """Show resolved bibliographic information for a PDF."""
    if writable and verbose:
        _emit_json({"ok": False, "command": "show.bib",
                    "error": "--writable and --verbose are mutually exclusive",
                    "error_type": "UsageError"})
        raise typer.Exit(2)

    if writable:
        as_json = True

    if as_json:
        quiet = True

    pdf = _resolve_pdf(pdf, as_json=as_json, command="show.bib")

    from .state import analysis_dir as _ad
    from .sidecar import load_clean

    _require_cached_bib(pdf, as_json=as_json, command="show.bib")
    ad = _ad(pdf)
    clean = load_clean(pdf, include_verbose=verbose)

    if writable:
        _emit_json(clean.get("fields", {}))
        return

    if as_json:
        out: dict = {
            "ok": True, "command": "show.bib",
            "pdf": str(pdf), "analysis_dir": str(ad),
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
        _console.print(f"\n[bold]puba show bib[/bold]: {pdf.name}")

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
    quiet: bool = typer.Option(False, "-q", "--quiet"),
) -> None:
    """Show rendered markdown for a PDF; errors if not yet rendered."""
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

    md_path = _require_cached_md(pdf, as_json=as_json, command="show.md")
    ad = _ad(pdf)

    if as_json:
        out: dict = {
            "ok": True, "command": "show.md",
            "pdf": str(pdf), "analysis_dir": str(ad),
            "paper_md": str(md_path),
            "paper_sections_json": str(ad / "paper.sections.json"),
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
    quiet: bool = typer.Option(False, "-q", "--quiet"),
) -> None:
    """List detected sections for a PDF; errors if markdown not yet rendered."""
    if as_json:
        quiet = True

    pdf = _resolve_pdf(pdf, as_json=as_json, command="show.sections")

    from .state import analysis_dir as _ad
    from .pdf.sections import load_sections_json

    _require_cached_md(pdf, as_json=as_json, command="show.sections")
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


@show_app.command("section")
def show_section(
    pdf: Path = typer.Argument(..., help="Path to the publication PDF."),
    name: str = typer.Argument(..., help="Section short_name (from `puba show sections`)."),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON envelope with content on stdout."),
    quiet: bool = typer.Option(False, "-q", "--quiet"),
) -> None:
    """Show the markdown content of a named section; errors if markdown not yet rendered."""
    if as_json:
        quiet = True

    pdf = _resolve_pdf(pdf, as_json=as_json, command="show.section")
    _require_cached_md(pdf, as_json=as_json, command="show.section")

    from .state import analysis_dir as _ad
    from .pdf.sections import load_sections_json

    ad = _ad(pdf)
    secs = load_sections_json(ad)
    match = next((s for s in secs if s.get("short_name") == name), None)

    if match is None:
        available = sorted(s["short_name"] for s in secs if s.get("short_name"))
        msg = (
            f"Section '{name}' not found. "
            f"Available: {', '.join(available) if available else '(none — run puba md first)'}"
        )
        if as_json:
            _emit_json({"ok": False, "command": "show.section", "pdf": str(pdf),
                        "analysis_dir": str(ad), "stage": "show.section",
                        "error": msg, "error_type": "KeyError"})
        else:
            _err.print(f"[red]Error:[/red] {msg}")
        raise typer.Exit(1)

    md_path = ad / "paper.md"
    md_text = md_path.read_text(encoding="utf-8") if md_path.exists() else ""
    start = match.get("start_offset", 0)
    end = match.get("end_offset", len(md_text))
    content = md_text[start:end]

    if as_json:
        _emit_json({
            "ok": True,
            "command": "show.section",
            "pdf": str(pdf),
            "analysis_dir": str(ad),
            "name": match["short_name"],
            "title": match.get("title", ""),
            "level": match.get("level", 1),
            "start_offset": start,
            "end_offset": end,
            "content": content,
        })
        return

    print(content, end="")


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


@show_app.command("figures")
def show_figures(
    pdf: Path = typer.Argument(..., help="Path to the publication PDF."),
    as_json: bool = typer.Option(False, "--json", help="Emit full manifest as JSON on stdout."),
    quiet: bool = typer.Option(False, "-q", "--quiet"),
) -> None:
    """List extracted figures for a PDF."""
    if as_json:
        quiet = True

    pdf = _resolve_pdf(pdf, as_json=as_json, command="show.figures")
    _require_cached_md(pdf, as_json=as_json, command="show.figures")

    from .state import analysis_dir as _ad
    ad = _ad(pdf)
    manifest_path = ad / "paper.figures.json"

    if not manifest_path.exists():
        msg = f"No figures manifest found for {pdf.name}. Run 'puba figures {pdf.name}' first."
        if as_json:
            _emit_json({"ok": False, "command": "show.figures", "pdf": str(pdf),
                        "analysis_dir": str(ad), "stage": "show.figures",
                        "error": msg, "error_type": "FileNotFoundError"})
        else:
            _err.print(f"[red]Error:[/red] {msg}")
        raise typer.Exit(1)

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    if as_json:
        _emit_json(manifest)
        return

    figs = manifest.get("figures", [])
    if not figs:
        _console.print("[dim]No figures found in manifest.[/dim]")
        return

    max_w = max(f["width_px"] for f in figs)
    w_col_w = len(str(max_w))
    max_h = max(f["height_px"] for f in figs)
    h_col_w = len(str(max_h))
    size_col_w = w_col_w + 1 + h_col_w

    table = Table(show_header=True, header_style="bold", box=None, padding=(0, 1))
    table.add_column("ID", style="cyan", min_width=16)
    table.add_column("PAGE", justify="right", min_width=4)
    table.add_column("TYPE", min_width=6)
    table.add_column("SIZE (px)", justify="right", min_width=size_col_w)
    table.add_column("CAPTION")

    for f in figs:
        size_str = f"{f['width_px']:>{w_col_w}}×{f['height_px']:<{h_col_w}}"
        caption = f.get("caption") or ""
        if len(caption) > 60:
            caption = caption[:57] + "..."
        table.add_row(
            f["id"],
            str(f["page"]),
            f["type"],
            size_str,
            caption,
        )
    _console.print(table)


@show_app.command("figure")
def show_figure(
    pdf: Path = typer.Argument(..., help="Path to the publication PDF."),
    figure_id: str = typer.Argument(..., help="Figure ID (e.g. page006_img1)."),
    as_json: bool = typer.Option(False, "--json", help="Emit single figure entry as JSON on stdout."),
    embed: bool = typer.Option(False, "--embed", help="Add base64 data_url field (requires --json)."),
    path: bool = typer.Option(False, "--path", help="Print only the absolute JPG path."),
    quiet: bool = typer.Option(False, "-q", "--quiet"),
) -> None:
    """Show detail for a single extracted figure."""
    if embed and not as_json:
        _err.print("[red]Error:[/red] --embed requires --json")
        raise typer.Exit(2)
    if path and as_json:
        _err.print("[red]Error:[/red] --path and --json are mutually exclusive")
        raise typer.Exit(2)

    if as_json:
        quiet = True

    pdf = _resolve_pdf(pdf, as_json=as_json, command="show.figure")
    _require_cached_md(pdf, as_json=as_json, command="show.figure")

    from .state import analysis_dir as _ad
    ad = _ad(pdf)
    manifest_path = ad / "paper.figures.json"

    if not manifest_path.exists():
        msg = f"No figures manifest found for {pdf.name}. Run 'puba figures {pdf.name}' first."
        if as_json:
            _emit_json({"ok": False, "command": "show.figure", "pdf": str(pdf),
                        "analysis_dir": str(ad), "stage": "show.figure",
                        "error": msg, "error_type": "FileNotFoundError"})
        else:
            _err.print(f"[red]Error:[/red] {msg}")
        raise typer.Exit(1)

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    figs = manifest.get("figures", [])
    entry = next((f for f in figs if f["id"] == figure_id), None)

    if entry is None:
        available = sorted(f["id"] for f in figs)
        msg = f"Figure '{figure_id}' not found. Available: {', '.join(available)}"
        if as_json:
            _emit_json({"ok": False, "command": "show.figure", "pdf": str(pdf),
                        "stage": "show.figure", "error": msg, "error_type": "KeyError"})
        else:
            _err.print(f"[red]Error:[/red] {msg}")
        raise typer.Exit(1)

    if path:
        print(entry["jpg"])
        return

    if as_json:
        if embed:
            jpg = Path(entry.get("jpg", ""))
            if jpg.exists():
                entry["data_url"] = _embed_jpeg(jpg)
        _emit_json(entry)
        return

    _console.print(f"\n[bold cyan]{entry['id']}[/bold cyan]")
    _console.print(f"  Page      : {entry['page']} (page_idx {entry['page_idx']})")
    _console.print(f"  Type      : {entry['type']}")
    _console.print(f"  Size      : {entry['width_px']} × {entry['height_px']} px  "
                   f"({entry['width_pt']} × {entry['height_pt']} pt)")
    _console.print(f"  Bbox      : {entry['bbox']}")
    _console.print(f"  JPG       : {entry['jpg']}")
    caption = entry.get("caption")
    _console.print(f"  Caption   : {caption if caption is not None else '(none)'}")
    footnote = entry.get("footnote")
    _console.print(f"  Footnote  : {footnote if footnote is not None else '(none)'}")
    _console.print(f"  Source SHA: {entry.get('source_sha', '')}")


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

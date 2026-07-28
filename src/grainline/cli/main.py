"""The ``grainline`` command-line interface.

Designed for two audiences at once. A fabricator runs ``grainline nest job.toml``
and reads a table. A production system runs ``grainline nest job.toml --json``
and parses the result. Both get the same numbers, and exit codes are meaningful
so a shop can wire nesting into a build script:

* ``0`` — every part was placed
* ``1`` — the command failed (bad file, missing geometry, licence problem)
* ``2`` — the nest ran but some parts could not be placed

Exit code 2 is the important one. A nest that silently drops three parts and
returns success is how a job ships short.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .. import __version__
from ..core.licensing import (
    Tier,
    active_license,
    active_license_problem,
    active_tier,
    clear_cache,
    grainline_home,
    license_search_paths,
)

__all__ = ["app", "main"]

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_INCOMPLETE = 2

app = typer.Typer(
    name="grainline",
    help="Irregular-shape nesting and material-yield engine for sheet-goods shops.",
    no_args_is_help=True,
    add_completion=False,
)
license_app = typer.Typer(help="Inspect and install your licence.", no_args_is_help=True)
app.add_typer(license_app, name="license")

remnants_app = typer.Typer(
    help="Manage the tracked offcut inventory (Pro).", no_args_is_help=True
)
app.add_typer(remnants_app, name="remnants")

console = Console()
err_console = Console(stderr=True)


def _banner() -> None:
    """Print the active edition, and any reason a licence was rejected."""
    licence = active_license()
    problem = active_license_problem()

    tier = licence.effective_tier
    colour = {Tier.FREE: "white", Tier.PREMIUM: "cyan", Tier.PRO: "magenta"}[tier]
    console.print(f"[bold {colour}]GRAINLINE[/] {__version__} - {licence.describe()}")

    if problem:
        err_console.print(f"[yellow]licence warning:[/] {problem}")


def _fail(message: str) -> None:
    """Report a fatal error and exit."""
    err_console.print(f"[bold red]error:[/] {message}")
    raise typer.Exit(EXIT_ERROR)


# ---------------------------------------------------------------------------
# nest
# ---------------------------------------------------------------------------


@app.command()
def nest(
    job_file: Path = typer.Argument(..., help="Job file (.toml or .json)."),
    out: Path = typer.Option(
        Path("nest-output"), "--out", "-o", help="Directory for exported sheets."
    ),
    formats: list[str] = typer.Option(
        ["svg", "json"],
        "--format",
        "-f",
        help="Output formats: svg, dxf, json, html. Repeatable.",
    ),
    strategy: Optional[str] = typer.Option(
        None, "--strategy", "-s", help="Override the strategy named in the job file."
    ),
    spacing: Optional[float] = typer.Option(
        None, "--spacing", help="Override part spacing in mm."
    ),
    kerf: Optional[float] = typer.Option(None, "--kerf", help="Override kerf in mm."),
    as_json: bool = typer.Option(
        False, "--json", help="Print the machine-readable report to stdout instead."
    ),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Suppress the banner."),
) -> None:
    """Nest a job and export the sheets."""
    from ..core.config import JobFileError, load_job
    from ..core.model.job import NestConfig
    from ..core.nesting import nest as run_nest
    from ..core.nesting.base import NestingError
    from ..core.registry import CapabilityError, TierRequiredError
    from ..core.report import build_report

    if not quiet and not as_json:
        _banner()

    try:
        job = load_job(job_file)
    except JobFileError as exc:
        _fail(str(exc))

    # Command-line overrides win over the file, which is what an operator
    # trying "one more millimetre of spacing" expects.
    current = job.config
    job.config = NestConfig(
        kerf=current.kerf if kerf is None else kerf,
        part_spacing=current.part_spacing if spacing is None else spacing,
        sheet_margin=current.sheet_margin,
        allow_rotation=current.allow_rotation,
        rotations=current.rotations,
        strategy=strategy or current.strategy,
        seed=current.seed,
        time_limit_s=current.time_limit_s,
        sort_key=current.sort_key,
    )

    try:
        result = run_nest(job, tier=active_tier())
    except TierRequiredError as exc:
        err_console.print(f"[bold red]error:[/] {exc}")
        err_console.print(
            f"[dim]Run 'grainline strategies' to see what your licence covers.[/]"
        )
        raise typer.Exit(EXIT_ERROR)
    except (CapabilityError, NestingError) as exc:
        _fail(str(exc))

    report = build_report(result, job)

    if as_json:
        console.print_json(json.dumps(report))
    else:
        _print_result_table(result, job)

    _export(result, job, out, formats, report, quiet=quiet or as_json)

    raise typer.Exit(EXIT_OK if result.is_complete else EXIT_INCOMPLETE)


def _print_result_table(result, job) -> None:
    """Render the nest result as a rich table."""
    from ..core.report import estimate_irregular_upside

    table = Table(title=f"Nest: {job.name}", title_justify="left", show_edge=False)
    table.add_column("Sheet", justify="right")
    table.add_column("Stock")
    table.add_column("Size (mm)", justify="right")
    table.add_column("Parts", justify="right")
    table.add_column("Yield", justify="right")

    for layout in result.sheets():
        yield_pct = layout.utilisation * 100.0
        colour = "green" if yield_pct >= 75 else "yellow" if yield_pct >= 55 else "red"
        table.add_row(
            str(layout.index + 1),
            layout.stock.id,
            f"{layout.stock.width:.0f} x {layout.stock.height:.0f}",
            str(layout.part_count),
            f"[{colour}]{yield_pct:.1f}%[/]",
        )

    console.print()
    console.print(table)
    console.print()

    overall = result.yield_percent
    colour = "green" if overall >= 75 else "yellow" if overall >= 55 else "red"
    console.print(
        f"  Overall yield  [bold {colour}]{overall:.1f}%[/]   "
        f"sheets [bold]{result.sheets_used}[/]   "
        f"placed [bold]{result.placed_count}[/]"
    )
    if result.material_cost:
        console.print(
            f"  Material cost  [bold]{result.material_cost:.2f}[/]   "
            f"wasted value [bold]{result.wasted_cost:.2f}[/]"
        )

    if result.unplaced:
        console.print()
        console.print("[bold red]  UNPLACED PARTS[/]")
        for part_id, count in sorted(result.unplaced.items()):
            console.print(f"    {part_id}: {count}")

    for note in result.notes:
        console.print(f"  [yellow]note:[/] {note}")

    signal = estimate_irregular_upside(result, job)
    if signal.worthwhile and active_tier() < Tier.PREMIUM:
        console.print()
        console.print(
            Panel(
                signal.message()
                + "\n\n[dim]The Premium tier's irregular (no-fit-polygon) nesting "
                "recovers material that rectangular nesting cannot reach.[/]",
                title="Material analysis",
                border_style="yellow",
            )
        )


def _export(result, job, out: Path, formats, report, *, quiet: bool) -> None:
    """Write the requested output formats."""
    from ..core.io.dxf_writer import write_nest_dxf
    from ..core.io.svg_writer import write_nest_svg
    from ..core.report import render_html

    requested = {f.strip().lower() for f in formats}
    unknown = requested - {"svg", "dxf", "json", "html"}
    if unknown:
        _fail(f"unknown output format(s): {', '.join(sorted(unknown))}")

    if not result.sheets():
        return

    out.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    watermark = "" if active_tier() >= Tier.PREMIUM else "GRAINLINE Free edition"

    if "svg" in requested:
        written += write_nest_svg(result, out, show_watermark=watermark)
    if "dxf" in requested:
        written += write_nest_dxf(result, out)
    if "json" in requested:
        path = out / "report.json"
        path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        written.append(path)
    if "html" in requested:
        path = out / "report.html"
        path.write_text(render_html(result, job), encoding="utf-8")
        written.append(path)

    if not quiet and written:
        console.print()
        console.print(f"  Wrote {len(written)} file(s) to [bold]{out}[/]")


# ---------------------------------------------------------------------------
# inspect
# ---------------------------------------------------------------------------


@app.command()
def inspect(
    path: Path = typer.Argument(..., help="A .dxf or .svg file to examine."),
    chord_tolerance: float = typer.Option(0.05, help="Arc flattening tolerance in mm."),
    weld_tolerance: float = typer.Option(1e-3, help="Endpoint welding distance in mm."),
    min_area: float = typer.Option(1.0, help="Discard shapes below this area in mm2."),
    unit: Optional[str] = typer.Option(None, help="Override the file's units."),
    exclude_layer: list[str] = typer.Option(
        [], "--exclude-layer", help="Layer to ignore. Repeatable."
    ),
) -> None:
    """Import a CAD file and report what GRAINLINE found in it.

    Run this first whenever a file nests badly. Almost every import problem is
    visible here: wrong units, geometry on a construction layer, or an outline
    that never closed.
    """
    from ..core.io.base import ImportError_, ImportOptions
    from ..core.io.reader import read_geometry

    options = ImportOptions(
        chord_tolerance=chord_tolerance,
        weld_tolerance=weld_tolerance,
        min_area=min_area,
        unit_override=unit,
        exclude_layers=frozenset(exclude_layer),
    )

    try:
        imported = read_geometry(path, options)
    except ImportError_ as exc:
        _fail(str(exc))

    report = imported.report
    console.print(f"[bold]{path}[/]")
    console.print(
        f"  unit {report.unit} (x{report.scale_to_mm:g} to mm)   "
        f"{report.entities_read} entities read"
    )
    console.print(
        f"  {report.rings_closed} closed ring(s)   "
        f"{report.runs_unclosed} unclosed run(s)   "
        f"{report.shapes_built} shape(s) built"
    )

    if report.layers_seen:
        console.print(f"  layers: {', '.join(sorted(report.layers_seen))}")
    if report.entities_skipped:
        skipped = ", ".join(f"{k} x{v}" for k, v in sorted(report.entities_skipped.items()))
        console.print(f"  [dim]skipped: {skipped}[/]")

    if imported.shapes:
        table = Table(show_edge=False, box=None, padding=(0, 2))
        table.add_column("#", justify="right")
        table.add_column("Size (mm)", justify="right")
        table.add_column("Area (mm2)", justify="right")
        table.add_column("Holes", justify="right")
        table.add_column("Box fill", justify="right")
        table.add_column("Points", justify="right")
        for i, shape in enumerate(imported.shapes[:40], start=1):
            table.add_row(
                str(i),
                f"{shape.width:.2f} x {shape.height:.2f}",
                f"{shape.area:.1f}",
                str(len(shape.holes)),
                f"{shape.utilisation * 100:.0f}%",
                str(len(shape.outer)),
            )
        console.print()
        console.print(table)
        if len(imported.shapes) > 40:
            console.print(f"  [dim]... and {len(imported.shapes) - 40} more[/]")

    for warning in report.warnings:
        err_console.print(f"[yellow]warning:[/] {warning}")

    raise typer.Exit(EXIT_OK if imported.shapes else EXIT_ERROR)


# ---------------------------------------------------------------------------
# strategies
# ---------------------------------------------------------------------------


@app.command()
def strategies() -> None:
    """List every nesting strategy and whether your licence covers it."""
    from ..core.registry import available_strategies

    _banner()
    tier = active_tier()

    table = Table(show_edge=False)
    table.add_column("Strategy")
    table.add_column("Tier")
    table.add_column("Available")
    table.add_column("Description")

    for name, capability in sorted(
        available_strategies().items(), key=lambda kv: (kv[1].tier, kv[0])
    ):
        usable = capability.tier <= tier
        table.add_row(
            name,
            capability.tier.label,
            "[green]yes[/]" if usable else "[dim]upgrade[/]",
            capability.summary or capability.upgrade_hint or "",
        )

    console.print()
    console.print(table)

    if tier < Tier.PRO:
        console.print()
        console.print(
            "[dim]Premium adds irregular (no-fit-polygon) nesting. "
            "Pro adds the remnant ledger, cut-path sequencing and the REST API.[/]"
        )


# ---------------------------------------------------------------------------
# licence
# ---------------------------------------------------------------------------


@license_app.command("show")
def license_show() -> None:
    """Show the active licence and where GRAINLINE looked for it."""
    licence = active_license()
    problem = active_license_problem()

    console.print(f"[bold]{licence.describe()}[/]")
    if licence.tier is not Tier.FREE:
        console.print(f"  seats      {licence.seats}")
        console.print(f"  licence id {licence.license_id or '(none)'}")
        if licence.features:
            console.print(f"  features   {', '.join(sorted(licence.features))}")

    if problem:
        err_console.print(f"[yellow]licence warning:[/] {problem}")

    console.print()
    console.print("[dim]Searched, in order:[/]")
    for path in license_search_paths():
        marker = "[green]found[/]" if path.is_file() else "[dim]-[/]"
        console.print(f"  {marker} {path}")


@license_app.command("set")
def license_set(
    token: str = typer.Argument(..., help="The GL1-... licence key, or a path to it."),
) -> None:
    """Install a licence key on this machine."""
    from ..core.licensing.verify import LicenseVerifier
    from ..core.licensing.token import LicenseError

    text = token.strip()
    candidate = Path(text).expanduser()
    if not text.startswith("GL1-") and candidate.is_file():
        text = candidate.read_text(encoding="utf-8").strip()

    try:
        licence = LicenseVerifier.release().verify(text)
    except LicenseError as exc:
        _fail(f"licence rejected: {exc}")

    home = grainline_home()
    home.mkdir(parents=True, exist_ok=True)
    target = home / "license.key"
    target.write_text(text + "\n", encoding="utf-8")

    clear_cache()
    console.print(f"[green]Installed:[/] {licence.describe()}")
    console.print(f"[dim]{target}[/]")


@license_app.command("path")
def license_path() -> None:
    """Print the directory GRAINLINE keeps its configuration in."""
    console.print(str(grainline_home()))


# ---------------------------------------------------------------------------
# remnants (Pro)
# ---------------------------------------------------------------------------


def _require_pro_module():
    """Load the Pro module, or explain precisely why it is unavailable."""
    if active_tier() < Tier.PRO:
        _fail(
            f"the remnant ledger requires the Pro tier; this licence is "
            f"{active_tier().label}. Run 'grainline license show' for details."
        )
    try:
        from .. import pro
    except ImportError:
        _fail(
            "the Pro module is not installed in this build. The public "
            "distribution ships the Free tier only."
        )
    return pro


@remnants_app.command("list")
def remnants_list(
    material: str = typer.Option("", help="Filter by material."),
    limit: int = typer.Option(50, help="Maximum rows to show."),
) -> None:
    """Show the offcuts currently available in the rack."""
    pro = _require_pro_module()

    with pro.RemnantLedger(pro.default_ledger_path()) as ledger:
        stats = ledger.stats()
        available = ledger.available(material=material, limit=limit)

    console.print(
        f"[bold]{stats['available']}[/] offcut(s) available, "
        f"{stats['available_area_mm2'] / 1e6:.3f} m2, "
        f"carrying value [bold]{stats['available_value']:.2f}[/]"
    )
    console.print(
        f"[dim]{stats['consumed']} reclaimed to date, "
        f"{stats['reclaimed_area_mm2'] / 1e6:.3f} m2 "
        f"worth {stats['reclaimed_value']:.2f}[/]"
    )

    if not available:
        console.print("\n[dim]Nothing in the rack yet. Nest a job with "
                      "--strategy pro to start banking offcuts.[/]")
        return

    table = Table(show_edge=False)
    table.add_column("ID")
    table.add_column("Size (mm)", justify="right")
    table.add_column("Area (m2)", justify="right")
    table.add_column("Material")
    table.add_column("Location")
    table.add_column("From job")

    for remnant in available:
        table.add_row(
            remnant.id,
            f"{remnant.width:.0f} x {remnant.height:.0f}",
            f"{remnant.area / 1e6:.3f}",
            remnant.material or "[dim]any[/]",
            remnant.location or "[dim]-[/]",
            remnant.source_job or "[dim]-[/]",
        )

    console.print()
    console.print(table)


@remnants_app.command("add")
def remnants_add(
    width: float = typer.Argument(..., help="Width in mm."),
    height: float = typer.Argument(..., help="Height in mm."),
    material: str = typer.Option("", help="Material key."),
    cost: float = typer.Option(0.0, help="Carrying value."),
    location: str = typer.Option("", help="Where it is racked, e.g. 'Rack B3'."),
) -> None:
    """Record an offcut by hand, e.g. one left over from a manual cut."""
    pro = _require_pro_module()

    with pro.RemnantLedger(pro.default_ledger_path()) as ledger:
        try:
            remnant = ledger.add(
                width=width, height=height, material=material,
                cost_basis=cost, location=location, source_job="manual",
            )
        except pro.LedgerError as exc:
            _fail(str(exc))

    console.print(f"[green]Banked[/] {remnant.id} - {width:.0f} x {height:.0f} mm")


@remnants_app.command("consume")
def remnants_consume(
    remnant_id: str = typer.Argument(..., help="The offcut's id."),
    reason: str = typer.Option("manual", help="Why it left the rack."),
) -> None:
    """Mark an offcut as used or scrapped."""
    pro = _require_pro_module()

    with pro.RemnantLedger(pro.default_ledger_path()) as ledger:
        if ledger.get(remnant_id) is None:
            _fail(f"no remnant with id {remnant_id!r}")
        if not ledger.consume(remnant_id, job=reason):
            _fail(f"remnant {remnant_id} was already consumed")

    console.print(f"[green]Consumed[/] {remnant_id}")


@remnants_app.command("path")
def remnants_path() -> None:
    """Print the location of the remnant ledger database."""
    pro = _require_pro_module()
    console.print(str(pro.default_ledger_path()))


# ---------------------------------------------------------------------------
# serve-api (Pro)
# ---------------------------------------------------------------------------


@app.command("serve-api")
def serve_api(
    host: str = typer.Option("127.0.0.1", help="Interface to bind."),
    port: int = typer.Option(8712, help="Port to listen on."),
) -> None:
    """Start the headless REST API for ERP and MRP integration (Pro)."""
    pro = _require_pro_module()

    try:
        import uvicorn
    except ImportError:
        _fail(
            "the REST API needs extra packages. Install them with:\n"
            "    pip install 'grainline[web]'"
        )

    import os

    _banner()
    if not os.environ.get(pro.ENV_API_KEYS):
        err_console.print(
            f"[yellow]warning:[/] {pro.ENV_API_KEYS} is not set, so every "
            f"caller is trusted. Set it to a comma-separated list of keys "
            f"before exposing this port."
        )
    if host not in {"127.0.0.1", "localhost", "::1"}:
        err_console.print(
            f"[yellow]warning:[/] binding to {host} exposes the nesting API "
            f"to the network."
        )

    console.print(f"  REST API on [bold]http://{host}:{port}[/]  (Ctrl+C to stop)")
    console.print(f"  OpenAPI schema at http://{host}:{port}/docs")
    uvicorn.run(pro.create_api(), host=host, port=port, log_level="warning")


# ---------------------------------------------------------------------------
# analytics, model, post (Pro)
# ---------------------------------------------------------------------------


@app.command()
def analytics(
    json_out: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
    months: int = typer.Option(12, help="Months of trend to show."),
) -> None:
    """Show the shop's material history: yield trend, waste and reclaim (Pro)."""
    pro = _require_pro_module()

    with pro.RemnantLedger(pro.default_ledger_path()) as ledger:
        report = pro.Analytics(ledger)
        if json_out:
            payload = {
                "summary": report.summary(),
                "trend": [
                    {
                        "month": row.month, "nests": row.nests, "sheets": row.sheets,
                        "parts": row.parts,
                        "yield_percent": round(row.yield_percent, 2),
                        "material_cost": round(row.material_cost, 2),
                        "wasted_cost": round(row.wasted_cost, 2),
                        "reclaimed_value": round(row.reclaimed_value, 2),
                    }
                    for row in report.trend(months)
                ],
                "by_material": report.by_material(),
                "by_job": report.by_job(),
                "improvement": report.improvement(),
            }
            console.print_json(json.dumps(payload))
            return
        console.print(report.render_console())


@app.command()
def model() -> None:
    """Explain what the placement model has learned from your jobs (Pro)."""
    pro = _require_pro_module()

    path = pro.default_model_path()
    learned = pro.PlacementModel.load(path)
    console.print(learned.explain())
    console.print(f"\n[dim]{path}[/]")
    if learned.is_trained:
        console.print(
            "[dim]Delete that file to reset the model and return to the "
            "standard size-sorted opening order.[/]"
        )


@app.command()
def post(
    job_file: Path = typer.Argument(..., help="Job file (.toml or .json)."),
    dialect: str = typer.Option(
        "linuxcnc", "--dialect", "-d", help="Controller dialect."
    ),
    out: Path = typer.Option(Path("gcode"), "--out", "-o", help="Output directory."),
    feed: float = typer.Option(2400.0, help="Cutting feed in mm/min."),
    lead_in: float = typer.Option(4.0, help="Lead-in length in mm."),
    cut_z: float = typer.Option(-1.0, help="Cutting depth in mm."),
    acknowledge: bool = typer.Option(
        False,
        "--acknowledge-unverified",
        help="Confirm you will dry-run this code before cutting. Required.",
    ),
    list_dialects: bool = typer.Option(
        False, "--list", help="List available dialects and exit."
    ),
) -> None:
    """Nest a job and emit machine G-code (Pro).

    The programs produced have NOT been validated on physical hardware.
    Dry-run above the material, at reduced feed, with the cutter disabled,
    before committing a sheet.
    """
    pro = _require_pro_module()

    if list_dialects:
        table = Table(show_edge=False)
        table.add_column("Dialect")
        table.add_column("Extension")
        table.add_column("Description")
        for name, cls in sorted(pro.POST_PROCESSORS.items()):
            table.add_row(name, f".{cls.extension}", cls.summary)
        console.print(table)
        return

    if not acknowledge:
        err_console.print(
            "[bold red]error:[/] refusing to write an unvalidated machine "
            "program.\n\n"
            "  These programs have not been tested on physical hardware. "
            "Controller\n"
            "  dialects differ in ways documentation does not capture, and a "
            "wrong\n"
            "  plunge rate or a missed torch-off is a crash or a fire.\n\n"
            "  Dry-run above the material, at reduced feed, with the cutter "
            "disabled.\n"
            "  Then re-run with [bold]--acknowledge-unverified[/]."
        )
        raise typer.Exit(EXIT_ERROR)

    from ..core.config import JobFileError, load_job
    from ..core.nesting import nest as run_nest
    from ..core.nesting.base import NestingError
    from ..core.registry import CapabilityError, TierRequiredError

    _banner()

    try:
        job = load_job(job_file)
    except JobFileError as exc:
        _fail(str(exc))

    try:
        result = run_nest(job, tier=active_tier())
    except (CapabilityError, NestingError, TierRequiredError) as exc:
        _fail(str(exc))

    try:
        processor = pro.get_post_processor(
            dialect,
            pro.PostOptions(feed_rate=feed, lead_in=lead_in, cut_z=cut_z),
        )
    except ValueError as exc:
        _fail(str(exc))

    machine = pro.MachineProfile(id=dialect)
    written: list[Path] = []
    for layout in result.sheets():
        written.append(
            pro.write_program(
                result, layout, machine, processor, out, acknowledged=True
            )
        )

    console.print()
    console.print(f"  Wrote {len(written)} program(s) to [bold]{out}[/]")
    err_console.print(
        "[yellow]  DRY-RUN THIS CODE ABOVE THE MATERIAL BEFORE CUTTING.[/]"
    )
    raise typer.Exit(EXIT_OK if result.is_complete else EXIT_INCOMPLETE)


@app.command("api-key")
def api_key() -> None:
    """Generate an API key suitable for handing to an integrator (Pro)."""
    pro = _require_pro_module()
    key = pro.ApiKeyStore.generate()
    console.print(key)
    console.print(
        f"\n[dim]Add it to the {pro.ENV_API_KEYS} environment variable "
        f"(comma-separated for multiple keys).[/]"
    )


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------


_EXAMPLE_JOB = '''\
# GRAINLINE job file
# Run with:  grainline nest job.toml --out nest-output

name = "example-job"

[config]
kerf = 0.2          # material the tool destroys, in mm
part_spacing = 2.0  # desired clear gap between parts, in mm
sheet_margin = 5.0  # clear border inside the usable sheet, in mm
strategy = "guillotine"

[import]
chord_tolerance = 0.05   # arc flattening accuracy, in mm
min_area = 1.0           # ignore marks smaller than this, in mm2
exclude_layers = ["DIMENSIONS", "NOTES"]

# Stock is consumed in the order listed - put remnants first to burn them off.
[[stock]]
id = "alu-3mm-2440x1220"
width = 2440
height = 1220
cost = 180.0
material = "aluminium"
# quantity = 5        # omit for unlimited

[[parts]]
id = "bracket"
file = "parts/bracket.dxf"
quantity = 12
material = "aluminium"
grain = "free"          # free | fixed | bidirectional
# select = "largest"    # or "all" if the file holds a whole kit
'''


@app.command()
def init(
    directory: Path = typer.Argument(Path("."), help="Where to write the example job."),
    force: bool = typer.Option(False, "--force", help="Overwrite an existing job.toml."),
) -> None:
    """Write a commented example job file to get started."""
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / "job.toml"

    if target.exists() and not force:
        _fail(f"{target} already exists; pass --force to overwrite")

    target.write_text(_EXAMPLE_JOB, encoding="utf-8")
    (directory / "parts").mkdir(exist_ok=True)

    console.print(f"[green]Created[/] {target}")
    console.print(f"[dim]Put your DXF or SVG files in {directory / 'parts'}[/]")
    console.print()
    console.print("Then run:  [bold]grainline nest job.toml[/]")


# ---------------------------------------------------------------------------
# serve
# ---------------------------------------------------------------------------


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", help="Interface to bind."),
    port: int = typer.Option(8711, help="Port to listen on."),
) -> None:
    """Start the local web interface.

    Binds to loopback by default. These files are a shop's proprietary
    geometry; exposing them on a shared network must be a deliberate act, not
    something that happens because a default was convenient.
    """
    try:
        import uvicorn
    except ImportError:
        _fail(
            "the web interface needs extra packages. Install them with:\n"
            "    pip install 'grainline[web]'"
        )

    from ..web.app import create_app

    if host not in {"127.0.0.1", "localhost", "::1"}:
        err_console.print(
            f"[yellow]warning:[/] binding to {host} exposes your part geometry "
            f"to the network. Use 127.0.0.1 unless you intend this."
        )

    _banner()
    console.print(f"  Web interface on [bold]http://{host}:{port}[/]  (Ctrl+C to stop)")
    uvicorn.run(create_app(), host=host, port=port, log_level="warning")


# ---------------------------------------------------------------------------
# version
# ---------------------------------------------------------------------------


@app.command()
def version() -> None:
    """Print the version and active edition."""
    _banner()


def main() -> None:
    """Console-script entry point."""
    app()


if __name__ == "__main__":
    main()

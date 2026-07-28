"""Yield reporting: the numbers a shop owner actually makes decisions on.

Three renderings of one dataset — a JSON-serialisable dict, a console table and
a standalone HTML page — all built from :func:`build_report` so they can never
disagree with one another.

The report also computes an **irregularity index**: the area-weighted mean of
each part's shape area divided by its bounding-box area. A job of rectangles
scores near 1.00 and rectangular nesting is already near-optimal for it. A job
of brackets and gussets scores well below, and the material trapped inside those
bounding boxes is precisely what the irregular kernel recovers. Reporting it
honestly in the free tier is both good engineering and the most effective
upgrade prompt available, because it is derived from the customer's own parts.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..model.job import Job
from ..model.result import NestResult

__all__ = [
    "build_report",
    "render_console",
    "render_html",
    "UpgradeSignal",
    "estimate_irregular_upside",
]

#: Below this irregularity index, bounding-box nesting is leaving enough
#: material on the table to be worth telling the operator about. 0.80 means a
#: fifth of every bounding box is air.
_IRREGULARITY_HINT_THRESHOLD: float = 0.80

#: Fraction of the material trapped inside bounding boxes that a good irregular
#: nest typically recovers. Deliberately conservative: published benchmarks and
#: vendor claims run higher, and a savings estimate a shop cannot reproduce
#: destroys trust far faster than a modest one builds it.
_IRREGULAR_RECOVERY_FACTOR: float = 0.55


@dataclass(frozen=True, slots=True)
class UpgradeSignal:
    """Evidence, drawn from the customer's own parts, that irregular nesting pays."""

    irregularity_index: float
    trapped_area: float
    estimated_recoverable_area: float
    estimated_sheets_saved: float
    estimated_cost_saved: float
    worthwhile: bool

    def message(self) -> str:
        """One-line summary suitable for a CLI footer or a web banner."""
        if not self.worthwhile:
            return (
                f"Parts are close to rectangular (index {self.irregularity_index:.2f}); "
                f"guillotine nesting is already near-optimal for this job."
            )
        bits = [
            f"Parts fill only {self.irregularity_index * 100:.0f}% of their bounding boxes."
        ]
        if self.estimated_sheets_saved >= 0.1:
            bits.append(
                f"Irregular nesting could recover roughly "
                f"{self.estimated_sheets_saved:.1f} sheet(s) on this job."
            )
        if self.estimated_cost_saved > 0:
            bits.append(f"Estimated value: {self.estimated_cost_saved:.2f}.")
        return " ".join(bits)


def estimate_irregular_upside(result: NestResult, job: Job | None = None) -> UpgradeSignal:
    """Quantify the material trapped inside part bounding boxes.

    The estimate is intentionally derived only from geometry the customer
    supplied, so they can verify it by hand on a single part if they want to.
    """
    parts = list(result.parts_by_id.values())
    if not parts:
        return UpgradeSignal(1.0, 0.0, 0.0, 0.0, 0.0, False)

    placed_per_part: dict[str, int] = {}
    for layout in result.sheets():
        for placement in layout.placements:
            placed_per_part[placement.part_id] = (
                placed_per_part.get(placement.part_id, 0) + 1
            )

    total_area = 0.0
    total_bbox = 0.0
    for part in parts:
        count = placed_per_part.get(part.id, 0)
        if count == 0:
            continue
        total_area += part.shape.area * count
        total_bbox += part.shape.bbox_area * count

    if total_bbox <= 0.0:
        return UpgradeSignal(1.0, 0.0, 0.0, 0.0, 0.0, False)

    index = total_area / total_bbox
    trapped = total_bbox - total_area
    recoverable = trapped * _IRREGULAR_RECOVERY_FACTOR

    sheets = result.sheets()
    mean_sheet_area = (
        sum(layout.stock.area for layout in sheets) / len(sheets) if sheets else 0.0
    )
    sheets_saved = recoverable / mean_sheet_area if mean_sheet_area > 0 else 0.0

    mean_sheet_cost = (
        sum(layout.stock.cost for layout in sheets) / len(sheets) if sheets else 0.0
    )
    cost_saved = sheets_saved * mean_sheet_cost

    return UpgradeSignal(
        irregularity_index=index,
        trapped_area=trapped,
        estimated_recoverable_area=recoverable,
        estimated_sheets_saved=sheets_saved,
        estimated_cost_saved=cost_saved,
        worthwhile=index < _IRREGULARITY_HINT_THRESHOLD,
    )


def build_report(result: NestResult, job: Job | None = None) -> dict[str, Any]:
    """Assemble the full report as a JSON-serialisable dictionary."""
    signal = estimate_irregular_upside(result, job)

    sheets: list[dict[str, Any]] = []
    for layout in result.sheets():
        sheets.append(
            {
                "index": layout.index,
                "number": layout.index + 1,
                "stock_id": layout.stock.id,
                "material": layout.stock.material,
                "width_mm": layout.stock.width,
                "height_mm": layout.stock.height,
                "is_remnant": layout.stock.is_remnant,
                "part_count": layout.part_count,
                "placed_area_mm2": round(layout.placed_area, 3),
                "waste_area_mm2": round(layout.waste_area, 3),
                "utilisation": round(layout.utilisation, 6),
                "yield_percent": round(layout.utilisation * 100.0, 2),
                "cost": round(layout.cost, 4),
                "parts": [
                    {
                        "key": p.key,
                        "part_id": p.part_id,
                        "instance": p.instance,
                        "rotation": p.transform.rotation,
                        "mirrored": p.transform.mirror_x,
                        "x_mm": round(p.bounds[0], 3),
                        "y_mm": round(p.bounds[1], 3),
                    }
                    for p in layout.placements
                ],
            }
        )

    part_rows: list[dict[str, Any]] = []
    for part_id in sorted(result.parts_by_id):
        part = result.parts_by_id[part_id]
        placed = sum(
            1
            for layout in result.sheets()
            for p in layout.placements
            if p.part_id == part_id
        )
        part_rows.append(
            {
                "part_id": part_id,
                "required": part.quantity,
                "placed": placed,
                "unplaced": result.unplaced.get(part_id, 0),
                "area_mm2": round(part.shape.area, 3),
                "bbox_mm": [round(part.shape.width, 3), round(part.shape.height, 3)],
                "bbox_fill": round(part.shape.utilisation, 4),
                "grain": part.grain.value,
                "material": part.material,
            }
        )

    return {
        "job": {
            "name": job.name if job else "",
            "strategy": result.strategy,
            "total_instances": job.total_instances if job else result.placed_count,
        },
        "summary": {
            "sheets_used": result.sheets_used,
            "parts_placed": result.placed_count,
            "parts_unplaced": result.unplaced_count,
            "placed_area_mm2": round(result.placed_area, 3),
            "consumed_area_mm2": round(result.consumed_area, 3),
            "waste_area_mm2": round(result.waste_area, 3),
            "utilisation": round(result.utilisation, 6),
            "yield_percent": round(result.yield_percent, 2),
            "material_cost": round(result.material_cost, 4),
            "wasted_cost": round(result.wasted_cost, 4),
            "complete": result.is_complete,
            "elapsed_s": round(result.elapsed_s, 4),
        },
        "sheets": sheets,
        "parts": part_rows,
        "unplaced": dict(result.unplaced),
        "notes": list(result.notes),
        "upgrade_signal": {
            "irregularity_index": round(signal.irregularity_index, 4),
            "trapped_area_mm2": round(signal.trapped_area, 3),
            "estimated_recoverable_area_mm2": round(
                signal.estimated_recoverable_area, 3
            ),
            "estimated_sheets_saved": round(signal.estimated_sheets_saved, 3),
            "estimated_cost_saved": round(signal.estimated_cost_saved, 2),
            "worthwhile": signal.worthwhile,
            "message": signal.message(),
        },
    }


def render_console(result: NestResult, job: Job | None = None) -> str:
    """Render the report as a plain-text table.

    Returns a string rather than printing so the same output can be captured by
    tests, piped to a file, or embedded in a REST response.
    """
    data = build_report(result, job)
    summary = data["summary"]
    lines: list[str] = []

    name = data["job"]["name"] or "nest"
    lines.append(f"GRAINLINE nest report - {name}")
    lines.append(f"strategy: {data['job']['strategy']}   ({summary['elapsed_s']:.3f}s)")
    lines.append("")
    lines.append(f"  Yield          {summary['yield_percent']:.1f}%")
    lines.append(f"  Sheets used    {summary['sheets_used']}")
    lines.append(
        f"  Parts placed   {summary['parts_placed']}"
        + (
            f"   ({summary['parts_unplaced']} UNPLACED)"
            if summary["parts_unplaced"]
            else ""
        )
    )
    lines.append(f"  Material used  {summary['placed_area_mm2'] / 1e6:.4f} m2")
    lines.append(f"  Material waste {summary['waste_area_mm2'] / 1e6:.4f} m2")
    if summary["material_cost"]:
        lines.append(f"  Material cost  {summary['material_cost']:.2f}")
        lines.append(f"  Wasted value   {summary['wasted_cost']:.2f}")

    lines.append("")
    lines.append("  Sheet  Stock                 Parts   Yield")
    lines.append("  -----  --------------------  -----  ------")
    for sheet in data["sheets"]:
        lines.append(
            f"  {sheet['number']:>5}  {sheet['stock_id'][:20]:<20}  "
            f"{sheet['part_count']:>5}  {sheet['yield_percent']:>5.1f}%"
        )

    if data["unplaced"]:
        lines.append("")
        lines.append("  UNPLACED PARTS")
        for part_id, count in sorted(data["unplaced"].items()):
            lines.append(f"    {part_id}: {count}")

    if data["notes"]:
        lines.append("")
        lines.append("  NOTES")
        for note in data["notes"]:
            lines.append(f"    - {note}")

    lines.append("")
    lines.append(f"  {data['upgrade_signal']['message']}")

    return "\n".join(lines)


def render_html(result: NestResult, job: Job | None = None) -> str:
    """Render the report as a standalone, dependency-free HTML page."""
    from html import escape

    data = build_report(result, job)
    summary = data["summary"]
    signal = data["upgrade_signal"]
    name = escape(data["job"]["name"] or "nest")

    sheet_rows = "\n".join(
        f"<tr><td>{s['number']}</td><td>{escape(s['stock_id'])}</td>"
        f"<td>{s['width_mm']:.0f} x {s['height_mm']:.0f}</td>"
        f"<td class='num'>{s['part_count']}</td>"
        f"<td class='num'>{s['yield_percent']:.1f}%</td></tr>"
        for s in data["sheets"]
    )

    part_rows = "\n".join(
        f"<tr><td>{escape(p['part_id'])}</td>"
        f"<td class='num'>{p['required']}</td>"
        f"<td class='num'>{p['placed']}</td>"
        f"<td class='num'>{p['unplaced'] or ''}</td>"
        f"<td class='num'>{p['bbox_mm'][0]:.1f} x {p['bbox_mm'][1]:.1f}</td>"
        f"<td class='num'>{p['bbox_fill'] * 100:.0f}%</td></tr>"
        for p in data["parts"]
    )

    notes_html = ""
    if data["notes"]:
        items = "".join(f"<li>{escape(n)}</li>" for n in data["notes"])
        notes_html = f"<h2>Notes</h2><ul class='notes'>{items}</ul>"

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>GRAINLINE nest report - {name}</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ font-family: ui-sans-serif, system-ui, sans-serif; margin: 0; padding: 2rem;
         background: #fbfaf8; color: #1c1a17; }}
  h1 {{ font-size: 1.4rem; margin: 0 0 .25rem; }}
  .sub {{ color: #6b635a; margin-bottom: 1.5rem; font-size: .9rem; }}
  .cards {{ display: flex; flex-wrap: wrap; gap: 1rem; margin-bottom: 2rem; }}
  .card {{ background: #fff; border: 1px solid #e3ddd4; border-radius: 8px;
           padding: 1rem 1.25rem; min-width: 9rem; }}
  .card .v {{ font-size: 1.6rem; font-weight: 600; }}
  .card .k {{ font-size: .75rem; text-transform: uppercase; letter-spacing: .04em;
              color: #6b635a; }}
  table {{ border-collapse: collapse; width: 100%; max-width: 60rem;
           margin-bottom: 2rem; background: #fff; }}
  th, td {{ text-align: left; padding: .45rem .7rem; border-bottom: 1px solid #eee7dd;
            font-size: .9rem; }}
  th {{ background: #f2ece3; font-weight: 600; }}
  td.num, th.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
  .signal {{ background: #fff6e5; border: 1px solid #f0d9a8; border-radius: 8px;
             padding: 1rem 1.25rem; max-width: 60rem; }}
  .notes li {{ font-size: .9rem; color: #6b635a; }}
  @media (prefers-color-scheme: dark) {{
    body {{ background: #161513; color: #ece7df; }}
    .card, table {{ background: #201e1b; border-color: #37332d; }}
    th {{ background: #2a2723; }}
    td, th {{ border-bottom-color: #2f2b26; }}
    .signal {{ background: #2a2318; border-color: #4d3f26; }}
  }}
</style></head><body>
<h1>GRAINLINE nest report &mdash; {name}</h1>
<div class="sub">strategy: {escape(data['job']['strategy'])} &middot;
  {summary['elapsed_s']:.3f}s</div>

<div class="cards">
  <div class="card"><div class="v">{summary['yield_percent']:.1f}%</div>
    <div class="k">Yield</div></div>
  <div class="card"><div class="v">{summary['sheets_used']}</div>
    <div class="k">Sheets used</div></div>
  <div class="card"><div class="v">{summary['parts_placed']}</div>
    <div class="k">Parts placed</div></div>
  <div class="card"><div class="v">{summary['waste_area_mm2'] / 1e6:.3f}</div>
    <div class="k">Waste (m&sup2;)</div></div>
</div>

<h2>Sheets</h2>
<table><thead><tr><th>#</th><th>Stock</th><th>Size (mm)</th>
<th class="num">Parts</th><th class="num">Yield</th></tr></thead>
<tbody>{sheet_rows}</tbody></table>

<h2>Parts</h2>
<table><thead><tr><th>Part</th><th class="num">Required</th><th class="num">Placed</th>
<th class="num">Unplaced</th><th class="num">Bounding box</th>
<th class="num">Box fill</th></tr></thead>
<tbody>{part_rows}</tbody></table>

{notes_html}

<div class="signal"><strong>Material analysis.</strong>
  {escape(signal['message'])}</div>
</body></html>
"""

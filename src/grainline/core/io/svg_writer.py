"""Render finished nests to SVG for on-screen review and printing.

One SVG per sheet. The sheet outline, the usable area after trim and margin,
and every placed part are drawn as real geometry at 1:1 scale in millimetres, so
an operator can print the page and physically check it against the board.

The only subtlety is the Y axis. SVG's grows downward; CAD's grows upward. A
single wrapping transform flips it once at the top level, which means every
coordinate written inside is the same number the DXF export writes. Flipping
per-shape instead would eventually produce a nest whose SVG and DXF disagree.
"""

from __future__ import annotations

from pathlib import Path
from xml.sax.saxutils import escape

from ..geometry.primitives import Shape
from ..model.result import NestResult, SheetLayout

__all__ = ["sheet_to_svg", "write_nest_svg", "PART_PALETTE"]

#: Fill colours cycled across parts so adjacent pieces stay distinguishable.
#: Chosen for adequate contrast against both the sheet fill and black stroke,
#: and to remain distinguishable in greyscale when the page is printed on the
#: shop's monochrome laser printer.
PART_PALETTE: tuple[str, ...] = (
    "#4C78A8", "#F58518", "#54A24B", "#E45756", "#72B7B2",
    "#EECA3B", "#B279A2", "#FF9DA6", "#9D755D", "#BAB0AC",
)

_SHEET_FILL = "#f5f3ef"
_SHEET_STROKE = "#333333"
_USABLE_STROKE = "#b0a99f"


def _contour_path(shape: Shape) -> str:
    """SVG path data for a shape, holes included via the even-odd fill rule."""
    parts: list[str] = []
    for contour in shape.contours:
        pts = contour.points
        head = f"M {pts[0][0]:.4f} {pts[0][1]:.4f}"
        body = " ".join(f"L {x:.4f} {y:.4f}" for x, y in pts[1:])
        parts.append(f"{head} {body} Z")
    return " ".join(parts)


def sheet_to_svg(
    result: NestResult,
    layout: SheetLayout,
    *,
    show_labels: bool = True,
    show_watermark: str = "",
) -> str:
    """Render one sheet layout as a standalone SVG document.

    Args:
        result: The nest the layout belongs to, used to resolve part geometry.
        layout: The sheet to draw.
        show_labels: Draw the part key at each placement's centre.
        show_watermark: Optional footer text, used by the free build to mark
            output and by the Pro build to stamp a job number.
    """
    stock = layout.stock
    w, h = stock.width, stock.height

    lines: list[str] = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        (
            f'<svg xmlns="http://www.w3.org/2000/svg" version="1.1" '
            f'width="{w:.3f}mm" height="{h:.3f}mm" '
            f'viewBox="0 0 {w:.4f} {h:.4f}">'
        ),
        f"<title>{escape(stock.id)} - sheet {layout.index + 1}</title>",
        # Flip Y once for the whole document so inner coordinates match CAD.
        f'<g transform="translate(0 {h:.4f}) scale(1 -1)">',
        (
            f'<rect x="0" y="0" width="{w:.4f}" height="{h:.4f}" '
            f'fill="{_SHEET_FILL}" stroke="{_SHEET_STROKE}" stroke-width="0.6"/>'
        ),
    ]

    if stock.trim_margin > 0:
        lines.append(
            f'<rect x="{stock.trim_margin:.4f}" y="{stock.trim_margin:.4f}" '
            f'width="{stock.usable_width:.4f}" height="{stock.usable_height:.4f}" '
            f'fill="none" stroke="{_USABLE_STROKE}" stroke-width="0.4" '
            f'stroke-dasharray="4 3"/>'
        )

    parts_by_id = result.parts_by_id
    colour_index: dict[str, str] = {}
    for i, part_id in enumerate(sorted(parts_by_id)):
        colour_index[part_id] = PART_PALETTE[i % len(PART_PALETTE)]

    for placement in layout.placements:
        part = parts_by_id.get(placement.part_id)
        if part is None:
            continue
        placed = placement.transform.apply(part.shape)
        colour = colour_index.get(placement.part_id, PART_PALETTE[0])
        lines.append(
            f'<path d="{_contour_path(placed)}" fill="{colour}" '
            f'fill-rule="evenodd" fill-opacity="0.72" stroke="#111111" '
            f'stroke-width="0.35" stroke-linejoin="round">'
            f"<title>{escape(placement.key)}</title></path>"
        )

    lines.append("</g>")

    if show_labels:
        # Labels sit outside the flipped group so text reads the right way up.
        for placement in layout.placements:
            part = parts_by_id.get(placement.part_id)
            if part is None:
                continue
            x0, y0, x1, y1 = placement.bounds
            cx = (x0 + x1) / 2.0
            cy = h - (y0 + y1) / 2.0  # convert CAD Y to SVG Y
            size = max(2.0, min(6.0, min(x1 - x0, y1 - y0) * 0.22))
            lines.append(
                f'<text x="{cx:.3f}" y="{cy:.3f}" font-size="{size:.2f}" '
                f'font-family="sans-serif" fill="#111111" text-anchor="middle" '
                f'dominant-baseline="middle" pointer-events="none">'
                f"{escape(placement.key)}</text>"
            )

    footer = (
        f"Sheet {layout.index + 1} - {escape(stock.id)} - "
        f"{layout.part_count} parts - {layout.utilisation * 100:.1f}% yield"
    )
    lines.append(
        f'<text x="{w / 2:.3f}" y="{h - 2:.3f}" font-size="4" '
        f'font-family="sans-serif" fill="#555555" text-anchor="middle">'
        f"{footer}</text>"
    )
    if show_watermark:
        lines.append(
            f'<text x="{w / 2:.3f}" y="7" font-size="4" font-family="sans-serif" '
            f'fill="#999999" text-anchor="middle">{escape(show_watermark)}</text>'
        )

    lines.append("</svg>")
    return "\n".join(lines)


def write_nest_svg(
    result: NestResult,
    directory: str | Path,
    *,
    prefix: str = "sheet",
    show_labels: bool = True,
    show_watermark: str = "",
) -> list[Path]:
    """Write one SVG per sheet into ``directory``. Returns the paths written."""
    out_dir = Path(directory)
    out_dir.mkdir(parents=True, exist_ok=True)

    written: list[Path] = []
    for layout in result.sheets():
        svg = sheet_to_svg(
            result, layout, show_labels=show_labels, show_watermark=show_watermark
        )
        path = out_dir / f"{prefix}-{layout.index + 1:03d}.svg"
        path.write_text(svg, encoding="utf-8")
        written.append(path)
    return written

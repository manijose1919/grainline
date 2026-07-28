"""Write finished nests back out as DXF, ready to load on the machine.

Geometry is written as closed ``LWPOLYLINE`` entities because every controller
and CAM post-processor in this market reads them without complaint — including
the R12-era software still running on machines a small shop can actually afford.

Layers carry meaning and must not be collapsed:

``SHEET``
    The board outline. Reference only, never cut.
``USABLE``
    The trim and margin boundary, when one is configured.
``PARTS``
    The cut geometry. This is what the operator selects and posts.

Keeping the sheet outline on its own layer is not cosmetic. If it lands on the
cut layer the machine will happily cut the board in half.
"""

from __future__ import annotations

from pathlib import Path

from ..geometry.primitives import Shape
from ..model.result import NestResult, SheetLayout

__all__ = ["sheet_to_dxf_document", "write_nest_dxf", "LAYER_SHEET", "LAYER_PARTS"]

LAYER_SHEET = "SHEET"
LAYER_USABLE = "USABLE"
LAYER_PARTS = "PARTS"
LAYER_LABELS = "LABELS"

#: AutoCAD Color Index values. 8 is grey, 5 is blue, 3 is green, 2 is yellow.
_ACI_SHEET = 8
_ACI_USABLE = 5
_ACI_PARTS = 3
_ACI_LABELS = 2


def _add_shape(msp, shape: Shape, layer: str) -> None:
    """Write a shape's outer contour and every hole as closed polylines."""
    for contour in shape.contours:
        msp.add_lwpolyline(
            contour.points,
            format="xy",
            close=True,
            dxfattribs={"layer": layer},
        )


def sheet_to_dxf_document(
    result: NestResult,
    layout: SheetLayout,
    *,
    include_labels: bool = True,
    dxfversion: str = "R2010",
):
    """Build an in-memory ezdxf document for one sheet.

    Returned rather than written so the web UI and REST API can stream it
    without touching the filesystem.
    """
    import ezdxf

    doc = ezdxf.new(dxfversion)
    # 4 == millimetres. Setting it means a machine that honours the header does
    # not silently scale a 2440 mm sheet as 2440 inches.
    doc.header["$INSUNITS"] = 4

    for name, colour in (
        (LAYER_SHEET, _ACI_SHEET),
        (LAYER_USABLE, _ACI_USABLE),
        (LAYER_PARTS, _ACI_PARTS),
        (LAYER_LABELS, _ACI_LABELS),
    ):
        if name not in doc.layers:
            doc.layers.add(name, color=colour)

    msp = doc.modelspace()
    stock = layout.stock

    msp.add_lwpolyline(
        [(0.0, 0.0), (stock.width, 0.0), (stock.width, stock.height), (0.0, stock.height)],
        format="xy",
        close=True,
        dxfattribs={"layer": LAYER_SHEET},
    )

    if stock.trim_margin > 0:
        t = stock.trim_margin
        msp.add_lwpolyline(
            [
                (t, t),
                (stock.width - t, t),
                (stock.width - t, stock.height - t),
                (t, stock.height - t),
            ],
            format="xy",
            close=True,
            dxfattribs={"layer": LAYER_USABLE},
        )

    parts_by_id = result.parts_by_id
    for placement in layout.placements:
        part = parts_by_id.get(placement.part_id)
        if part is None:
            continue
        _add_shape(msp, placement.transform.apply(part.shape), LAYER_PARTS)

        if include_labels:
            x0, y0, x1, y1 = placement.bounds
            height = max(2.0, min(8.0, min(x1 - x0, y1 - y0) * 0.2))
            text = msp.add_text(
                placement.key,
                height=height,
                dxfattribs={"layer": LAYER_LABELS},
            )
            text.set_placement(((x0 + x1) / 2.0, (y0 + y1) / 2.0))

    return doc


def write_nest_dxf(
    result: NestResult,
    directory: str | Path,
    *,
    prefix: str = "sheet",
    include_labels: bool = True,
    dxfversion: str = "R2010",
) -> list[Path]:
    """Write one DXF per sheet into ``directory``. Returns the paths written."""
    out_dir = Path(directory)
    out_dir.mkdir(parents=True, exist_ok=True)

    written: list[Path] = []
    for layout in result.sheets():
        doc = sheet_to_dxf_document(
            result, layout, include_labels=include_labels, dxfversion=dxfversion
        )
        path = out_dir / f"{prefix}-{layout.index + 1:03d}.dxf"
        doc.saveas(str(path))
        written.append(path)
    return written

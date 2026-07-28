"""Generate the example DXF parts referenced by examples/job.toml.

Kept as a generator rather than committed binaries so the example geometry is
readable and reviewable in a diff, and so anyone can adjust a dimension and
regenerate without a CAD seat.

    python examples/make_parts.py
"""

from __future__ import annotations

import math
from pathlib import Path

import ezdxf

OUT = Path(__file__).parent / "parts"


def _document():
    doc = ezdxf.new("R2010")
    doc.header["$INSUNITS"] = 4  # millimetres
    doc.layers.add("CUT", color=3)
    doc.layers.add("DIMENSIONS", color=8)
    return doc


def _circle_points(cx: float, cy: float, radius: float, segments: int = 48):
    return [
        (
            cx + radius * math.cos(2 * math.pi * i / segments),
            cy + radius * math.sin(2 * math.pi * i / segments),
        )
        for i in range(segments)
    ]


def bracket() -> None:
    """An L-shaped mounting bracket with two fixing holes.

    The canonical case rectangular nesting wastes: it fills only 43% of its
    bounding box, and two of them rotated 180 degrees form a near-perfect
    rectangle that guillotine nesting cannot exploit.
    """
    doc = _document()
    msp = doc.modelspace()

    width, height, thickness = 160.0, 100.0, 30.0
    msp.add_lwpolyline(
        [
            (0, 0), (width, 0), (width, thickness),
            (thickness, thickness), (thickness, height), (0, height),
        ],
        close=True,
        dxfattribs={"layer": "CUT"},
    )

    for cx, cy in ((width - 20.0, thickness / 2), (thickness / 2, height - 20.0)):
        msp.add_lwpolyline(
            _circle_points(cx, cy, 5.5), close=True, dxfattribs={"layer": "CUT"}
        )

    # Annotation on its own layer, excluded by the example job file.
    msp.add_text("BRACKET 160x100x30", height=8,
                 dxfattribs={"layer": "DIMENSIONS"}).set_placement((60, 60))

    doc.saveas(OUT / "bracket.dxf")


def gusset() -> None:
    """A right-triangle corner gusset. Fills exactly half its bounding box."""
    doc = _document()
    msp = doc.modelspace()
    msp.add_lwpolyline(
        [(0, 0), (180, 0), (0, 140)], close=True, dxfattribs={"layer": "CUT"}
    )
    msp.add_lwpolyline(
        _circle_points(30.0, 25.0, 6.0), close=True, dxfattribs={"layer": "CUT"}
    )
    doc.saveas(OUT / "gusset.dxf")


def flange() -> None:
    """A round flange with a bolt circle.

    Drawn as exploded ARC and LINE entities rather than closed polylines, so the
    example exercises the importer's loop reconstruction - which is the state
    most real production DXF files arrive in.
    """
    doc = _document()
    msp = doc.modelspace()

    outer = _circle_points(0.0, 0.0, 70.0, 64)
    for i in range(len(outer)):
        msp.add_line(outer[i], outer[(i + 1) % len(outer)],
                     dxfattribs={"layer": "CUT"})

    msp.add_lwpolyline(
        _circle_points(0.0, 0.0, 28.0), close=True, dxfattribs={"layer": "CUT"}
    )
    for i in range(6):
        angle = 2 * math.pi * i / 6
        msp.add_lwpolyline(
            _circle_points(48.0 * math.cos(angle), 48.0 * math.sin(angle), 5.0, 24),
            close=True,
            dxfattribs={"layer": "CUT"},
        )

    doc.saveas(OUT / "flange.dxf")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    bracket()
    gusset()
    flange()
    for path in sorted(OUT.glob("*.dxf")):
        print(f"wrote {path.relative_to(Path(__file__).parent.parent)} "
              f"({path.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()

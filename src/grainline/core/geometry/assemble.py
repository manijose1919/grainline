"""Turn a flat bag of closed rings into shapes with correctly assigned holes.

A CAD file gives no indication of which rings are holes. A washer arrives as two
unrelated circles; a stencilled letter "B" as three. The relationship has to be
recovered geometrically.

The rule is containment parity. A ring nested inside an odd number of other
rings is a hole; inside an even number (including zero) it is solid material.
A part sitting inside a window cut-out — depth two — is therefore a separate
shape in its own right, not a hole, which is exactly the behaviour a shop
expects when they nest a frame and its infill together.
"""

from __future__ import annotations

from typing import Iterable, Sequence

from shapely.geometry import Polygon as ShapelyPolygon
from shapely.strtree import STRtree

from .primitives import AREA_TOL, Contour, DegenerateGeometryError, Point, Shape
from .validate import repair_polygon, simplify_points

__all__ = ["rings_to_shapes", "assign_hierarchy"]


def assign_hierarchy(polygons: Sequence[ShapelyPolygon]) -> list[int]:
    """Return the index of each polygon's immediate container, or ``-1``.

    Polygons are compared largest-first so a container is always resolved before
    the things it contains. Containment is tested with a representative interior
    point rather than full ``contains``: it is dramatically cheaper and cannot
    be fooled by shared boundaries, which are common where a hole was drawn
    tangent to an outline.

    An R-tree narrows the candidate set first; without it this is quadratic and
    a 2,000-ring architectural panel takes minutes.
    """
    n = len(polygons)
    if n <= 1:
        return [-1] * n

    order = sorted(range(n), key=lambda i: polygons[i].area, reverse=True)
    rank = {idx: pos for pos, idx in enumerate(order)}

    tree = STRtree(list(polygons))
    parent = [-1] * n

    for i in range(n):
        probe = polygons[i].representative_point()
        best, best_area = -1, float("inf")
        for j in tree.query(probe):
            j = int(j)
            if j == i:
                continue
            # Only a strictly larger polygon (earlier in the size order) can
            # contain this one. Guards against ties between identical rings.
            if rank[j] >= rank[i]:
                continue
            if polygons[j].contains(probe) and polygons[j].area < best_area:
                best, best_area = j, polygons[j].area
        parent[i] = best

    return parent


def rings_to_shapes(
    rings: Iterable[Sequence[Point]],
    *,
    min_area: float = 1e-6,
    min_hole_area: float = 0.0,
    simplify_tolerance: float = 0.0,
) -> list[Shape]:
    """Build validated :class:`Shape` objects from closed rings.

    Args:
        rings: Closed rings, without a repeated closing vertex.
        min_area: Rings enclosing less than this (mm^2) are discarded as noise.
        min_hole_area: Holes smaller than this (mm^2) are ignored.
        simplify_tolerance: Douglas-Peucker tolerance in mm applied to each ring
            before assembly. Zero disables simplification.

    Returns:
        One shape per solid region, holes attached. Ordered largest first, which
        is also the order most nesting heuristics want to place them in.
    """
    prepared: list[ShapelyPolygon] = []

    for ring in rings:
        pts = list(ring)
        if simplify_tolerance > 0.0:
            pts = simplify_points(pts, simplify_tolerance, closed=True)
        if len(pts) < 3:
            continue
        raw = ShapelyPolygon([*pts, pts[0]])
        # Repair may split a self-intersecting ring into several pieces; each
        # piece participates in the hierarchy independently.
        prepared.extend(repair_polygon(raw, min_area=max(min_area, AREA_TOL)))

    if not prepared:
        return []

    parent = assign_hierarchy(prepared)

    depth = [-1] * len(prepared)

    def _depth(i: int) -> int:
        if depth[i] >= 0:
            return depth[i]
        p = parent[i]
        depth[i] = 0 if p < 0 else _depth(p) + 1
        return depth[i]

    for i in range(len(prepared)):
        _depth(i)

    holes_of: dict[int, list[int]] = {}
    solids: list[int] = []
    for i in range(len(prepared)):
        if depth[i] % 2 == 0:
            solids.append(i)
        else:
            holes_of.setdefault(parent[i], []).append(i)

    shapes: list[Shape] = []
    for i in solids:
        outer_ring = list(prepared[i].exterior.coords)
        hole_rings = []
        for h in holes_of.get(i, ()):
            hp = prepared[h]
            if hp.area < min_hole_area:
                continue
            hole_rings.append(list(hp.exterior.coords))
        try:
            shapes.append(
                Shape.of(
                    Contour.of(outer_ring),
                    [Contour.of(r) for r in hole_rings],
                    validate=False,
                )
            )
        except DegenerateGeometryError:
            continue

    shapes.sort(key=lambda s: s.area, reverse=True)
    return shapes

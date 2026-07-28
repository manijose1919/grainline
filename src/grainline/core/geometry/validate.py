"""Repair and simplification for geometry arriving from the outside world.

CAD exports are routinely unsound: bow-tie self-intersections from a mirrored
polyline, zero-width slivers where two segments double back, rings that touch
themselves at a single vertex. Shapely refuses to compute reliable areas or
boolean operations on such polygons, and the nesting kernels depend on both.

This module is the quarantine boundary. Everything entering the system passes
through :func:`repair_polygon`, and everything leaving it is a valid
:class:`~grainline.core.geometry.primitives.Shape`.
"""

from __future__ import annotations

import math
from typing import Iterable

from shapely.geometry import (
    GeometryCollection,
    MultiPolygon,
    Polygon as ShapelyPolygon,
)
from shapely.geometry.base import BaseGeometry
from shapely.validation import make_valid

from .primitives import (
    AREA_TOL,
    Contour,
    DegenerateGeometryError,
    GEOM_TOL,
    Point,
    Shape,
)

__all__ = [
    "repair_polygon",
    "explode_to_polygons",
    "to_shapes",
    "simplify_points",
    "drop_tiny_holes",
]


def explode_to_polygons(geom: BaseGeometry) -> list[ShapelyPolygon]:
    """Flatten any Shapely geometry down to its constituent polygons.

    ``make_valid`` on a self-intersecting ring can return a
    ``GeometryCollection`` mixing polygons with the lines and points where the
    ring pinched. Only the polygons carry material; the rest is discarded.
    """
    if geom.is_empty:
        return []
    if isinstance(geom, ShapelyPolygon):
        return [geom]
    if isinstance(geom, (MultiPolygon, GeometryCollection)):
        out: list[ShapelyPolygon] = []
        for part in geom.geoms:
            out.extend(explode_to_polygons(part))
        return out
    return []  # LineString / Point residue from a pinched ring


def repair_polygon(
    polygon: ShapelyPolygon, *, min_area: float = AREA_TOL
) -> list[ShapelyPolygon]:
    """Return valid, non-degenerate polygons equivalent to ``polygon``.

    A bow-tie splits into two triangles, so this returns a *list*: callers must
    decide whether to take the largest piece (usual for a part outline) or keep
    all of them (usual for a sheet of scattered parts).

    Args:
        polygon: Possibly invalid input.
        min_area: Pieces smaller than this in mm^2 are dropped as artefacts.
    """
    if polygon.is_valid:
        candidates = [polygon]
    else:
        candidates = explode_to_polygons(make_valid(polygon))

    kept = [p for p in candidates if not p.is_empty and p.area >= min_area]
    kept.sort(key=lambda p: p.area, reverse=True)
    return kept


def drop_tiny_holes(
    polygon: ShapelyPolygon, *, min_hole_area: float
) -> ShapelyPolygon:
    """Remove interior rings below ``min_hole_area`` mm^2.

    Sub-millimetre holes are almost always flattening noise rather than real
    features, and each one costs the irregular nester an inner-fit computation.
    Removing them is a meaningful speed-up on ornate parts and never changes the
    cut path a machine can actually execute.
    """
    if not polygon.interiors:
        return polygon
    keep = [r for r in polygon.interiors if abs(ShapelyPolygon(r).area) >= min_hole_area]
    if len(keep) == len(polygon.interiors):
        return polygon
    return ShapelyPolygon(polygon.exterior, keep)


def to_shapes(
    geom: BaseGeometry,
    *,
    min_area: float = AREA_TOL,
    min_hole_area: float = 0.0,
) -> list[Shape]:
    """Convert arbitrary Shapely geometry into validated :class:`Shape` objects.

    Invalid input is repaired first. Anything that cannot be rescued into a
    positive-area polygon is dropped rather than raised, because a single bad
    entity in a 900-entity DXF should not fail the whole import.
    """
    shapes: list[Shape] = []
    for poly in explode_to_polygons(geom):
        for fixed in repair_polygon(poly, min_area=min_area):
            if min_hole_area > 0.0:
                fixed = drop_tiny_holes(fixed, min_hole_area=min_hole_area)
            try:
                shapes.append(Shape.from_shapely(fixed, validate=False))
            except DegenerateGeometryError:
                continue
    return shapes


def simplify_points(
    points: Iterable[Point], tolerance: float, *, closed: bool = True
) -> list[Point]:
    """Douglas-Peucker simplification of a point run.

    Arc and spline flattening emits far more vertices than the nesting kernels
    need. Because no-fit-polygon cost scales with the *product* of two parts'
    vertex counts, halving vertices quarters the work — this is the single
    highest-leverage optimisation in the whole import path.

    Args:
        points: The run to simplify.
        tolerance: Maximum permitted deviation in mm. Zero returns the input.
        closed: Treat the run as a closed ring, keeping at least three vertices.
    """
    pts = list(points)
    if tolerance <= 0.0 or len(pts) < 3:
        return pts

    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack: list[tuple[int, int]] = [(0, len(pts) - 1)]

    while stack:
        start, end = stack.pop()
        if end <= start + 1:
            continue
        ax, ay = pts[start]
        bx, by = pts[end]
        dx, dy = bx - ax, by - ay
        seg_len = math.hypot(dx, dy)

        worst_idx, worst_dist = -1, 0.0
        for i in range(start + 1, end):
            px, py = pts[i]
            if seg_len <= GEOM_TOL:
                dist = math.hypot(px - ax, py - ay)
            else:
                # Perpendicular distance from the point to segment AB.
                dist = abs(dy * px - dx * py + bx * ay - by * ax) / seg_len
            if dist > worst_dist:
                worst_idx, worst_dist = i, dist

        if worst_dist > tolerance:
            keep[worst_idx] = True
            stack.append((start, worst_idx))
            stack.append((worst_idx, end))

    result = [p for p, k in zip(pts, keep) if k]

    # A closed ring simplified to a line has been destroyed; back off rather
    # than return something that will fail Contour validation.
    if closed and len(result) < 3:
        return pts
    return result

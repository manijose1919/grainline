"""Polygon primitives, affine transforms, repair and CAD-loop reconstruction."""

from __future__ import annotations

from .assemble import assign_hierarchy, rings_to_shapes
from .primitives import (
    AREA_TOL,
    GEOM_TOL,
    Bounds,
    Contour,
    DegenerateGeometryError,
    Point,
    Shape,
    signed_area,
)
from .stitch import DEFAULT_WELD_TOL, Polyline, StitchResult, stitch_loops
from .transform import IDENTITY, Transform, normalise_angle
from .validate import (
    drop_tiny_holes,
    explode_to_polygons,
    repair_polygon,
    simplify_points,
    to_shapes,
)

__all__ = [
    "AREA_TOL",
    "GEOM_TOL",
    "DEFAULT_WELD_TOL",
    "IDENTITY",
    "Bounds",
    "Contour",
    "DegenerateGeometryError",
    "Point",
    "Polyline",
    "Shape",
    "StitchResult",
    "Transform",
    "assign_hierarchy",
    "drop_tiny_holes",
    "explode_to_polygons",
    "normalise_angle",
    "repair_polygon",
    "rings_to_shapes",
    "signed_area",
    "simplify_points",
    "stitch_loops",
    "to_shapes",
]

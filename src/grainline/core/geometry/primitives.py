"""Immutable geometry primitives for GRAINLINE.

Everything in the nesting pipeline is expressed with two types:

``Contour``
    A single closed ring of 2-D points. The closing point is *not* stored; a
    triangle is three points, not four. Contours know their signed area and
    therefore their winding direction.

``Shape``
    An outer :class:`Contour` plus zero or more hole contours. This is the unit
    a nester places on a sheet.

Both types are frozen dataclasses and validate at construction, so any
``Shape`` that exists in memory is guaranteed to be geometrically sound. That
guarantee is what lets the nesting kernels skip defensive checks in their inner
loops.

All coordinates are millimetres. Unit conversion happens once, at import.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Iterable, Sequence

from shapely.geometry import Polygon as ShapelyPolygon

if TYPE_CHECKING:  # pragma: no cover
    from shapely.geometry.base import BaseGeometry

__all__ = [
    "Point",
    "Bounds",
    "Contour",
    "Shape",
    "GEOM_TOL",
    "AREA_TOL",
    "DegenerateGeometryError",
]

#: Coordinate comparison tolerance in millimetres. Two points closer than this
#: are considered identical. Chosen an order of magnitude below the precision of
#: any real cutting machine (a good fibre laser holds ~0.05 mm).
GEOM_TOL: float = 1e-6

#: Minimum area in mm^2 for a contour to be considered non-degenerate. A ring
#: smaller than this is a numerical artefact, not a part.
AREA_TOL: float = 1e-9

Point = tuple[float, float]
Bounds = tuple[float, float, float, float]  # (min_x, min_y, max_x, max_y)


class DegenerateGeometryError(ValueError):
    """Raised when geometry is too small, too few points, or self-collapsed."""


def _dedupe_consecutive(points: Sequence[Point], tol: float = GEOM_TOL) -> list[Point]:
    """Drop consecutive duplicate points, including a wrap-around duplicate.

    Input rings frequently repeat the first point at the end (the DXF/SVG
    convention). ``Contour`` stores rings *without* that closing point, so it is
    stripped here along with any interior stutter introduced by flattening.
    """
    out: list[Point] = []
    for p in points:
        if not out or math.dist(out[-1], p) > tol:
            out.append((float(p[0]), float(p[1])))
    # Remove the wrap-around closing point if present.
    while len(out) > 1 and math.dist(out[0], out[-1]) <= tol:
        out.pop()
    return out


def signed_area(points: Sequence[Point]) -> float:
    """Return the shoelace signed area of a ring. Positive means counter-clockwise.

    The ring is treated as implicitly closed; do not pass a repeated closing
    point (it contributes zero anyway, but costs a multiply).
    """
    n = len(points)
    if n < 3:
        return 0.0
    total = 0.0
    x0, y0 = points[-1]
    for x1, y1 in points:
        total += x0 * y1 - x1 * y0
        x0, y0 = x1, y1
    return total * 0.5


@dataclass(frozen=True, slots=True)
class Contour:
    """A single closed ring of points, stored without a repeated closing vertex.

    Construct via :meth:`of` rather than the raw initialiser so that duplicate
    points are stripped and degeneracy is caught.
    """

    points: tuple[Point, ...]

    # -- construction -----------------------------------------------------

    @classmethod
    def of(cls, points: Iterable[Point], *, tol: float = GEOM_TOL) -> Contour:
        """Build a contour, cleaning duplicates and validating non-degeneracy.

        Raises:
            DegenerateGeometryError: fewer than three distinct points remain, or
                the enclosed area is below :data:`AREA_TOL`.
        """
        cleaned = _dedupe_consecutive(list(points), tol)
        if len(cleaned) < 3:
            raise DegenerateGeometryError(
                f"contour needs at least 3 distinct points, got {len(cleaned)}"
            )
        if abs(signed_area(cleaned)) < AREA_TOL:
            # Zero *signed* area has two distinct causes and the message must
            # name both: genuinely collinear points, or a self-intersecting ring
            # whose lobes cancel (a symmetric bow-tie sums to exactly zero).
            raise DegenerateGeometryError(
                "contour encloses no net area - points are collinear, the ring "
                "is a zero-width sliver, or it self-intersects with cancelling lobes"
            )
        return cls(tuple(cleaned))

    # -- measurement ------------------------------------------------------

    @property
    def signed_area(self) -> float:
        """Shoelace signed area; positive when wound counter-clockwise."""
        return signed_area(self.points)

    @property
    def area(self) -> float:
        """Unsigned enclosed area in mm^2."""
        return abs(self.signed_area)

    @property
    def is_ccw(self) -> bool:
        """True when the ring is wound counter-clockwise."""
        return self.signed_area > 0.0

    @property
    def bounds(self) -> Bounds:
        """Axis-aligned bounding box as ``(min_x, min_y, max_x, max_y)``."""
        xs = [p[0] for p in self.points]
        ys = [p[1] for p in self.points]
        return (min(xs), min(ys), max(xs), max(ys))

    @property
    def perimeter(self) -> float:
        """Total closed path length in mm."""
        pts = self.points
        return sum(
            math.dist(pts[i - 1], pts[i]) for i in range(len(pts))
        )

    # -- transformation ---------------------------------------------------

    def reversed(self) -> Contour:
        """Return the same ring wound in the opposite direction."""
        return Contour(tuple(reversed(self.points)))

    def oriented(self, *, ccw: bool) -> Contour:
        """Return this ring wound in the requested direction.

        Returns ``self`` unchanged when the winding already matches, so callers
        can use it freely without worrying about allocation churn.
        """
        return self if self.is_ccw == ccw else self.reversed()

    def closed_points(self) -> tuple[Point, ...]:
        """Points with the first vertex repeated at the end, for export/plotting."""
        return (*self.points, self.points[0])

    def __len__(self) -> int:
        return len(self.points)


@dataclass(frozen=True, slots=True)
class Shape:
    """An outer boundary with optional holes: the unit a nester places.

    Invariants enforced at construction by :meth:`of`:

    * the outer contour is wound counter-clockwise;
    * every hole is wound clockwise;
    * the resulting Shapely polygon is valid (no self-intersection);
    * every hole lies inside the outer boundary.

    The Shapely polygon is built once and cached, because the nesting kernels
    hit it constantly and rebuilding it per query dominates runtime.
    """

    outer: Contour
    holes: tuple[Contour, ...] = ()
    #: Cached Shapely representation. Populated by ``of``; never set by hand.
    _polygon: ShapelyPolygon | None = field(
        default=None, compare=False, repr=False, hash=False
    )

    # -- construction -----------------------------------------------------

    @classmethod
    def of(
        cls,
        outer: Contour | Iterable[Point],
        holes: Iterable[Contour | Iterable[Point]] = (),
        *,
        validate: bool = True,
    ) -> Shape:
        """Build a shape, normalising winding order and validating topology.

        Args:
            outer: The outer boundary, as a :class:`Contour` or a point iterable.
            holes: Interior boundaries. Winding is corrected automatically.
            validate: When ``False``, skip the Shapely validity and containment
                checks. Only pass ``False`` for geometry a kernel has just
                produced and already knows to be sound.

        Raises:
            DegenerateGeometryError: the polygon is invalid or a hole escapes the
                outer boundary.
        """
        outer_c = outer if isinstance(outer, Contour) else Contour.of(outer)
        hole_cs = tuple(
            h if isinstance(h, Contour) else Contour.of(h) for h in holes
        )

        outer_c = outer_c.oriented(ccw=True)
        hole_cs = tuple(h.oriented(ccw=False) for h in hole_cs)

        poly = ShapelyPolygon(
            outer_c.closed_points(),
            [h.closed_points() for h in hole_cs],
        )

        if validate:
            if not poly.is_valid:
                from shapely.validation import explain_validity

                raise DegenerateGeometryError(
                    f"invalid polygon: {explain_validity(poly)}"
                )
            if poly.area < AREA_TOL:
                raise DegenerateGeometryError("shape encloses no area after holes")

        return cls(outer_c, hole_cs, poly)

    @classmethod
    def from_shapely(cls, poly: ShapelyPolygon, *, validate: bool = True) -> Shape:
        """Adapt a Shapely polygon into a :class:`Shape`."""
        if poly.is_empty:
            raise DegenerateGeometryError("cannot build a Shape from an empty polygon")
        return cls.of(
            Contour.of(list(poly.exterior.coords)),
            [Contour.of(list(r.coords)) for r in poly.interiors],
            validate=validate,
        )

    @classmethod
    def rectangle(cls, width: float, height: float, *, origin: Point = (0.0, 0.0)) -> Shape:
        """Convenience constructor for an axis-aligned rectangle."""
        if width <= 0 or height <= 0:
            raise DegenerateGeometryError(
                f"rectangle needs positive dimensions, got {width}x{height}"
            )
        ox, oy = origin
        return cls.of(
            [(ox, oy), (ox + width, oy), (ox + width, oy + height), (ox, oy + height)]
        )

    # -- access -----------------------------------------------------------

    @property
    def polygon(self) -> ShapelyPolygon:
        """The cached Shapely polygon for this shape."""
        if self._polygon is None:  # pragma: no cover - only via raw __init__
            object.__setattr__(
                self,
                "_polygon",
                ShapelyPolygon(
                    self.outer.closed_points(),
                    [h.closed_points() for h in self.holes],
                ),
            )
        assert self._polygon is not None
        return self._polygon

    @property
    def contours(self) -> tuple[Contour, ...]:
        """Outer contour followed by every hole."""
        return (self.outer, *self.holes)

    # -- measurement ------------------------------------------------------

    @property
    def area(self) -> float:
        """Net material area in mm^2 (outer area minus holes)."""
        return self.outer.area - sum(h.area for h in self.holes)

    @property
    def gross_area(self) -> float:
        """Outer area in mm^2, ignoring holes."""
        return self.outer.area

    @property
    def bounds(self) -> Bounds:
        """Axis-aligned bounding box of the outer contour."""
        return self.outer.bounds

    @property
    def width(self) -> float:
        """Bounding-box width in mm."""
        min_x, _, max_x, _ = self.bounds
        return max_x - min_x

    @property
    def height(self) -> float:
        """Bounding-box height in mm."""
        _, min_y, _, max_y = self.bounds
        return max_y - min_y

    @property
    def bbox_area(self) -> float:
        """Area of the bounding box in mm^2."""
        return self.width * self.height

    @property
    def utilisation(self) -> float:
        """Material area divided by bounding-box area, in ``(0, 1]``.

        A low value flags a part that wastes a lot of space under rectangular
        nesting and therefore benefits most from the irregular kernel. The Free
        tier surfaces this number as the upgrade signal.
        """
        bbox = self.bbox_area
        return self.area / bbox if bbox > AREA_TOL else 0.0

    @property
    def centroid(self) -> Point:
        """Area centroid of the shape, holes accounted for."""
        c = self.polygon.centroid
        return (c.x, c.y)

    def __repr__(self) -> str:  # pragma: no cover - diagnostic only
        return (
            f"Shape(pts={len(self.outer)}, holes={len(self.holes)}, "
            f"{self.width:.1f}x{self.height:.1f}mm, area={self.area:.1f}mm2)"
        )

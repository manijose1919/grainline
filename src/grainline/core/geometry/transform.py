"""Rigid transforms for placing parts on sheets.

A :class:`Transform` is the complete description of *where a part goes*: an
optional mirror, a rotation, then a translation, applied in that order. Nesting
kernels never mutate source geometry — they emit transforms, and rendering or
export applies them. Keeping placement separate from geometry means one imported
part can appear fifty times on a sheet while its point data exists exactly once.

Order matters and is fixed as **mirror -> rotate -> translate**. Fixing it here
means a transform is fully described by four scalars and can be compared,
hashed, serialised and reasoned about without matrix bookkeeping.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable

from .primitives import Contour, Point, Shape

__all__ = ["Transform", "IDENTITY", "rotate_point", "normalise_angle"]


def normalise_angle(degrees: float) -> float:
    """Fold an angle into ``[0, 360)``.

    Rotation search generates angles by repeated addition; without folding, a
    part rotated 720 degrees would not compare equal to an unrotated one and the
    de-duplication in the rotation search would silently stop working.
    """
    return degrees % 360.0


def rotate_point(p: Point, cos_t: float, sin_t: float) -> Point:
    """Rotate a point about the origin given precomputed cosine and sine.

    The trig terms are passed in rather than computed per point: rotating a
    5,000-point contour would otherwise call ``cos``/``sin`` 10,000 times for a
    single answer.
    """
    x, y = p
    return (x * cos_t - y * sin_t, x * sin_t + y * cos_t)


@dataclass(frozen=True, slots=True)
class Transform:
    """A mirror, rotation and translation applied in that fixed order.

    Attributes:
        rotation: Counter-clockwise rotation in degrees about the origin.
        tx: Translation along X in mm, applied after rotation.
        ty: Translation along Y in mm, applied after rotation.
        mirror_x: When true, negate X before rotating. Used for parts that may
            be flipped over (valid for symmetric stock, invalid for material
            with a face or a printed side).
    """

    rotation: float = 0.0
    tx: float = 0.0
    ty: float = 0.0
    mirror_x: bool = False

    # -- construction -----------------------------------------------------

    @classmethod
    def translation(cls, tx: float, ty: float) -> Transform:
        """A pure translation."""
        return cls(0.0, tx, ty, False)

    @classmethod
    def rotation_only(cls, degrees: float) -> Transform:
        """A pure rotation about the origin."""
        return cls(normalise_angle(degrees), 0.0, 0.0, False)

    def with_translation(self, tx: float, ty: float) -> Transform:
        """Return a copy with the translation replaced (rotation preserved)."""
        return Transform(self.rotation, tx, ty, self.mirror_x)

    def offset_by(self, dx: float, dy: float) -> Transform:
        """Return a copy shifted by an additional ``(dx, dy)``.

        Because translation is applied last, composing translations is simple
        addition — no re-rotation of the existing offset is required.
        """
        return Transform(self.rotation, self.tx + dx, self.ty + dy, self.mirror_x)

    # -- application ------------------------------------------------------

    def apply_point(self, p: Point) -> Point:
        """Transform a single point."""
        x, y = p
        if self.mirror_x:
            x = -x
        if self.rotation:
            theta = math.radians(self.rotation)
            c, s = math.cos(theta), math.sin(theta)
            x, y = x * c - y * s, x * s + y * c
        return (x + self.tx, y + self.ty)

    def apply_points(self, points: Iterable[Point]) -> list[Point]:
        """Transform many points, hoisting the trig out of the loop."""
        pts = list(points)
        mx = -1.0 if self.mirror_x else 1.0
        if not self.rotation:
            return [(p[0] * mx + self.tx, p[1] + self.ty) for p in pts]
        theta = math.radians(self.rotation)
        c, s = math.cos(theta), math.sin(theta)
        out: list[Point] = []
        for px, py in pts:
            x = px * mx
            out.append((x * c - py * s + self.tx, x * s + py * c + self.ty))
        return out

    def apply_contour(self, contour: Contour) -> Contour:
        """Transform a contour.

        A mirror reverses winding direction, so the ring is re-reversed to keep
        the original orientation. Without this, mirrored parts would come out
        with inverted winding and be rejected downstream as holes.
        """
        pts = self.apply_points(contour.points)
        if self.mirror_x:
            pts.reverse()
        return Contour(tuple(pts))

    def apply(self, shape: Shape) -> Shape:
        """Transform a whole shape.

        Validation is skipped: a rigid transform of a valid polygon is always
        valid, and re-running Shapely's validity check per placement would
        dominate nesting runtime.
        """
        return Shape.of(
            self.apply_contour(shape.outer),
            [self.apply_contour(h) for h in shape.holes],
            validate=False,
        )

    # -- composition ------------------------------------------------------

    def then(self, other: Transform) -> Transform:
        """Return the transform equivalent to applying ``self`` then ``other``.

        Two mirrors cancel. The trailing translation of ``self`` must itself be
        rotated by ``other`` before the two offsets can be summed, which is the
        only genuinely subtle line here.
        """
        combined_mirror = self.mirror_x != other.mirror_x
        combined_rotation = normalise_angle(
            (-self.rotation if other.mirror_x else self.rotation) + other.rotation
        )
        moved = other.apply_point((self.tx, self.ty))
        return Transform(combined_rotation, moved[0], moved[1], combined_mirror)

    @property
    def is_identity(self) -> bool:
        """True when this transform changes nothing."""
        return (
            not self.mirror_x
            and self.rotation == 0.0
            and self.tx == 0.0
            and self.ty == 0.0
        )


#: The do-nothing transform.
IDENTITY = Transform()

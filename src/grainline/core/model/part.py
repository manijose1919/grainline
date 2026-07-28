"""The part: a shape a shop needs some number of copies of."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from ..geometry.primitives import Shape

__all__ = ["GrainConstraint", "Part", "PartInstance"]


class GrainConstraint(str, Enum):
    """How a part may be oriented relative to the material's grain or pattern.

    Brushed aluminium, wood veneer, printed vinyl and directional fabric all
    have a visible direction. Nesting a part 90 degrees off ruins it even though
    the geometry fits perfectly, so this constraint outranks any yield gain.

    Members:
        FREE: Any rotation is acceptable. Plain acrylic, mild steel, MDF.
        FIXED: No rotation at all. The part must sit exactly as drawn.
        BIDIRECTIONAL: 0 or 180 degrees only - the grain runs the right way
            either upside down or not, which is correct for most veneer.
    """

    FREE = "free"
    FIXED = "fixed"
    BIDIRECTIONAL = "bidirectional"

    def allowed_angles(self, candidates: tuple[float, ...]) -> tuple[float, ...]:
        """Filter a set of candidate rotations down to those this grain permits."""
        if self is GrainConstraint.FIXED:
            return (0.0,)
        if self is GrainConstraint.BIDIRECTIONAL:
            return tuple(a for a in candidates if a % 180.0 == 0.0) or (0.0,)
        return candidates


@dataclass(frozen=True, slots=True)
class Part:
    """A distinct part plus how many of it the job needs.

    Attributes:
        id: Stable identifier, usually the source filename or a drawing number.
        shape: Geometry in millimetres.
        quantity: How many copies to nest. Must be at least one.
        grain: Rotation constraint imposed by the material.
        allow_mirror: Whether the part may be flipped over. Valid only for
            stock with no face - never for printed or laminated material.
        priority: Higher values are placed first. Used to guarantee that the
            parts a customer is waiting on make it onto the sheet even when the
            job over-runs the available stock.
        material: Material key. A part is only ever nested onto stock whose
            material matches, so one job can span aluminium and acrylic safely.
        source: Origin file, carried through for reporting.
    """

    id: str
    shape: Shape
    quantity: int = 1
    grain: GrainConstraint = GrainConstraint.FREE
    allow_mirror: bool = False
    priority: int = 0
    material: str = ""
    source: str = ""

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("part id must be a non-empty string")
        if self.quantity < 1:
            raise ValueError(
                f"part {self.id!r}: quantity must be >= 1, got {self.quantity}"
            )

    @property
    def area(self) -> float:
        """Material area of a single copy, in mm^2."""
        return self.shape.area

    @property
    def total_area(self) -> float:
        """Material area of every copy required, in mm^2."""
        return self.shape.area * self.quantity

    def permitted_angles(self, candidates: tuple[float, ...]) -> tuple[float, ...]:
        """Candidate rotations this part actually allows, honouring its grain."""
        return self.grain.allowed_angles(candidates)

    def instances(self) -> list[PartInstance]:
        """Expand into one :class:`PartInstance` per required copy."""
        return [PartInstance(part=self, index=i) for i in range(self.quantity)]


@dataclass(frozen=True, slots=True)
class PartInstance:
    """One specific copy of a part. Nesting places instances, not parts."""

    part: Part
    index: int

    @property
    def key(self) -> str:
        """Unique label for this copy, e.g. ``bracket-a#3``."""
        return f"{self.part.id}#{self.index}"

    @property
    def shape(self) -> Shape:
        return self.part.shape

    @property
    def area(self) -> float:
        return self.part.shape.area

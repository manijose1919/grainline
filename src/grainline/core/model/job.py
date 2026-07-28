"""The nesting job: parts, stock and the machine settings that bind them."""

from __future__ import annotations

from dataclasses import dataclass, field

from .part import Part
from .stock import Stock

__all__ = ["NestConfig", "Job", "FREE_ROTATIONS"]

#: Rotations the free core will try. Ninety-degree steps are what a guillotine
#: nest can exploit; arbitrary angles need the irregular kernel, because
#: rotating a bounding box off-axis makes it strictly larger and always loses.
FREE_ROTATIONS: tuple[float, ...] = (0.0, 90.0, 180.0, 270.0)


@dataclass(frozen=True, slots=True)
class NestConfig:
    """Machine and process settings for a nesting run.

    Attributes:
        kerf: Width of material the cutting tool destroys, in mm. A fibre laser
            is around 0.15 mm; a table saw blade is 3.2 mm.
        part_spacing: Desired clear gap between neighbouring parts, in mm.
        sheet_margin: Clear border kept inside the usable sheet area, in mm.
        allow_rotation: Master switch for trying rotations at all. Individual
            parts can still be pinned via their grain constraint.
        rotations: Candidate rotation angles in degrees.
        strategy: Name of the registered nesting strategy to use.
        seed: Random seed for stochastic strategies. Fixed by default so that
            the same job always produces the same nest - a shop that re-runs a
            job must get the same sheet back, or their setup sheets are wrong.
        time_limit_s: Soft wall-clock budget for optimisation. Zero means the
            strategy's own default.
        sort_key: How parts are ordered before placement. ``area`` is the
            classic choice; ``longest_side`` packs elongated parts better.
        allow_part_in_hole: Permit small parts to be nested inside larger
            parts' holes. Off by default and deliberately so: a part cut free
            inside a bore drops into the machine bed unless micro-joints hold
            it, so enabling this is a commitment to tabbing and hand-dressing.
        micro_joint_width: Uncut tab length in mm for parts that need holding.
            Zero derives it from ``material_thickness`` instead.
        material_thickness: Sheet thickness in mm. Used to size micro-joints,
            because what holds a part is the tab's cross-section, not its length.
    """

    kerf: float = 0.2
    part_spacing: float = 2.0
    sheet_margin: float = 5.0
    allow_rotation: bool = True
    rotations: tuple[float, ...] = FREE_ROTATIONS
    strategy: str = "guillotine"
    seed: int = 0
    time_limit_s: float = 0.0
    sort_key: str = "area"
    allow_part_in_hole: bool = False
    micro_joint_width: float = 0.0
    material_thickness: float = 3.0

    def __post_init__(self) -> None:
        if self.kerf < 0:
            raise ValueError(f"kerf cannot be negative, got {self.kerf}")
        if self.part_spacing < 0:
            raise ValueError(
                f"part_spacing cannot be negative, got {self.part_spacing}"
            )
        if self.sheet_margin < 0:
            raise ValueError(
                f"sheet_margin cannot be negative, got {self.sheet_margin}"
            )
        if not self.rotations:
            raise ValueError("at least one rotation angle is required")
        if self.sort_key not in {"area", "longest_side", "perimeter", "height"}:
            raise ValueError(
                f"unknown sort_key {self.sort_key!r}; expected one of "
                f"area, longest_side, perimeter, height"
            )
        if self.micro_joint_width < 0:
            raise ValueError(
                f"micro_joint_width cannot be negative, got {self.micro_joint_width}"
            )
        if self.material_thickness <= 0:
            raise ValueError(
                f"material_thickness must be positive, got {self.material_thickness}"
            )

    @property
    def gap(self) -> float:
        """Centre-to-centre clearance actually reserved between parts, in mm.

        This is the single most commonly misconfigured value in the product.
        Spacing is what the operator *wants* between parts; kerf is what the
        tool *destroys*. If spacing is smaller than kerf the blade eats into the
        neighbour, so the reserved gap is the larger of the two rather than
        either one alone.
        """
        return max(self.kerf, self.part_spacing)

    @property
    def effective_rotations(self) -> tuple[float, ...]:
        """Rotations to try, collapsing to no-rotation when disabled."""
        if not self.allow_rotation:
            return (0.0,)
        # De-duplicate while preserving order so that a config listing
        # (0, 90, 0) does not triple the search for no benefit.
        seen: dict[float, None] = {}
        for angle in self.rotations:
            seen.setdefault(angle % 360.0, None)
        return tuple(seen)


@dataclass(slots=True)
class Job:
    """Everything needed to produce a nest.

    Attributes:
        parts: The parts to place, with quantities.
        stock: Sheet grades available, in preference order. The nester consumes
            them in the order given, so put remnants first to burn them off
            before opening a fresh sheet.
        config: Machine and process settings.
        name: Human label for reports and exported filenames.
    """

    parts: list[Part] = field(default_factory=list)
    stock: list[Stock] = field(default_factory=list)
    config: NestConfig = field(default_factory=NestConfig)
    name: str = "untitled"

    def validate(self) -> list[str]:
        """Return human-readable problems that would make this job unnestable.

        Returning a list rather than raising lets the CLI and web UI show every
        problem at once. A shop fixing three issues one error message at a time
        is a bad experience and a support ticket.
        """
        problems: list[str] = []

        if not self.parts:
            problems.append("job has no parts")
        if not self.stock:
            problems.append("job has no stock defined")

        seen_parts: set[str] = set()
        for part in self.parts:
            if part.id in seen_parts:
                problems.append(f"duplicate part id {part.id!r}")
            seen_parts.add(part.id)

        seen_stock: set[str] = set()
        for sheet in self.stock:
            if sheet.id in seen_stock:
                problems.append(f"duplicate stock id {sheet.id!r}")
            seen_stock.add(sheet.id)

        gap = self.config.gap
        for part in self.parts:
            candidates = [s for s in self.stock if s.accepts(part.material)]
            if not candidates:
                problems.append(
                    f"part {part.id!r} needs material {part.material!r} but no "
                    f"matching stock is defined"
                )
                continue
            if not any(self._fits_any_orientation(part, s, gap) for s in candidates):
                problems.append(
                    f"part {part.id!r} ({part.shape.width:.1f}x"
                    f"{part.shape.height:.1f}mm) does not fit on any available "
                    f"stock even alone"
                )

        return problems

    def _fits_any_orientation(self, part: Part, sheet: Stock, gap: float) -> bool:
        """True when the part fits the sheet in at least one permitted rotation."""
        margin = self.config.sheet_margin
        avail_w = sheet.usable_width - 2.0 * margin
        avail_h = sheet.usable_height - 2.0 * margin
        if avail_w <= 0 or avail_h <= 0:
            return False

        angles = part.permitted_angles(self.config.effective_rotations)
        for angle in angles:
            # Only right-angle steps change a bounding box predictably; other
            # angles are handled by the irregular kernel, and for the fit test a
            # conservative swap check is correct and cheap.
            if angle % 180.0 == 90.0:
                w, h = part.shape.height, part.shape.width
            else:
                w, h = part.shape.width, part.shape.height
            if w + gap <= avail_w + gap and h + gap <= avail_h + gap:
                if w <= avail_w and h <= avail_h:
                    return True
        return False

    @property
    def total_part_area(self) -> float:
        """Material required by every part copy, in mm^2."""
        return sum(p.total_area for p in self.parts)

    @property
    def total_instances(self) -> int:
        """How many individual pieces must be placed."""
        return sum(p.quantity for p in self.parts)

    @property
    def materials(self) -> set[str]:
        """Distinct material keys referenced by the parts."""
        return {p.material for p in self.parts if p.material}

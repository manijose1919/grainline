"""The output of a nesting run: where every part went, and what it cost.

Yield is deliberately measured against **whole sheets consumed**, not against
the area the nest happens to span. A shop pays for the full sheet the moment
they take it off the rack, so a nest that uses 40% of a sheet has 40% yield even
if the parts are packed perfectly into one corner. Reporting the flattering
number instead is the single easiest way to lose a customer's trust when their
material spend does not fall the way the software promised.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..geometry.primitives import Bounds, Shape
from ..geometry.transform import Transform
from .part import Part
from .stock import Stock

__all__ = ["Placement", "SheetLayout", "NestResult"]


@dataclass(frozen=True, slots=True)
class Placement:
    """One part copy positioned on one sheet."""

    part_id: str
    instance: int
    transform: Transform
    #: Cached bounding box of the placed part, in sheet coordinates.
    bounds: Bounds
    #: Key of the part whose hole this one sits inside, if any.
    #:
    #: Plain data, deliberately. The free core never produces a value here, but
    #: it must be able to *carry* one so that reports, exporters and the cut-path
    #: stage all work identically whichever engine produced the nest.
    inside_hole_of: str = ""

    @property
    def key(self) -> str:
        """Unique label matching :attr:`PartInstance.key`."""
        return f"{self.part_id}#{self.instance}"

    @property
    def requires_tabs(self) -> bool:
        """True when this part will drop out unless micro-joints are left.

        A part nested inside a bore is surrounded by cut on every side. The
        moment its contour closes it falls into the machine bed, so tabs are
        mandatory rather than a finishing preference.
        """
        return bool(self.inside_hole_of)


@dataclass(slots=True)
class SheetLayout:
    """Everything placed on a single physical sheet."""

    index: int
    stock: Stock
    placements: list[Placement] = field(default_factory=list)
    #: Net material area of the parts on this sheet, in mm^2.
    placed_area: float = 0.0

    @property
    def utilisation(self) -> float:
        """Placed part area divided by full sheet area, in ``[0, 1]``."""
        return self.placed_area / self.stock.area if self.stock.area > 0 else 0.0

    @property
    def waste_area(self) -> float:
        """Sheet area not occupied by parts, in mm^2."""
        return max(0.0, self.stock.area - self.placed_area)

    @property
    def part_count(self) -> int:
        return len(self.placements)

    @property
    def occupied_bounds(self) -> Bounds | None:
        """Bounding box enclosing every placement, or ``None`` when empty.

        The Pro tier uses this to decide whether the unused tail of a sheet is
        large enough to be worth keeping as a tracked remnant.
        """
        if not self.placements:
            return None
        xs0 = min(p.bounds[0] for p in self.placements)
        ys0 = min(p.bounds[1] for p in self.placements)
        xs1 = max(p.bounds[2] for p in self.placements)
        ys1 = max(p.bounds[3] for p in self.placements)
        return (xs0, ys0, xs1, ys1)

    @property
    def cost(self) -> float:
        """Cost of the sheet consumed."""
        return self.stock.cost


@dataclass(slots=True)
class NestResult:
    """The complete outcome of a nesting run."""

    layouts: list[SheetLayout] = field(default_factory=list)
    #: Part id -> number of copies that could not be placed anywhere.
    unplaced: dict[str, int] = field(default_factory=dict)
    strategy: str = ""
    elapsed_s: float = 0.0
    #: Free-form notes from the strategy, surfaced verbatim in reports.
    notes: list[str] = field(default_factory=list)
    #: Parts indexed by id, so a report can resolve geometry without the job.
    parts_by_id: dict[str, Part] = field(default_factory=dict)

    # -- headline numbers -------------------------------------------------

    @property
    def sheets_used(self) -> int:
        """Number of sheets with at least one part on them."""
        return sum(1 for layout in self.layouts if layout.placements)

    @property
    def placed_count(self) -> int:
        """Total part copies successfully placed."""
        return sum(len(layout.placements) for layout in self.layouts)

    @property
    def unplaced_count(self) -> int:
        """Total part copies that could not be placed."""
        return sum(self.unplaced.values())

    @property
    def placed_area(self) -> float:
        """Net material area of every placed part, in mm^2."""
        return sum(layout.placed_area for layout in self.layouts)

    @property
    def consumed_area(self) -> float:
        """Full area of every sheet consumed, in mm^2."""
        return sum(
            layout.stock.area for layout in self.layouts if layout.placements
        )

    @property
    def utilisation(self) -> float:
        """Material yield in ``[0, 1]`` - the number the whole product sells on."""
        consumed = self.consumed_area
        return self.placed_area / consumed if consumed > 0 else 0.0

    @property
    def yield_percent(self) -> float:
        """Material yield as a percentage, for display."""
        return self.utilisation * 100.0

    @property
    def waste_area(self) -> float:
        """Sheet area consumed but not turned into parts, in mm^2."""
        return max(0.0, self.consumed_area - self.placed_area)

    @property
    def material_cost(self) -> float:
        """Total cost of the sheets consumed, in the shop's currency."""
        return sum(layout.cost for layout in self.layouts if layout.placements)

    @property
    def wasted_cost(self) -> float:
        """Currency value of the material thrown away."""
        return sum(
            layout.stock.cost_per_mm2 * layout.waste_area
            for layout in self.layouts
            if layout.placements
        )

    @property
    def is_complete(self) -> bool:
        """True when every requested copy found a home."""
        return self.unplaced_count == 0

    # -- helpers ----------------------------------------------------------

    def sheets(self) -> list[SheetLayout]:
        """Only the layouts that actually carry parts."""
        return [layout for layout in self.layouts if layout.placements]

    def placed_shapes(self, sheet_index: int) -> list[tuple[str, Shape]]:
        """Transformed geometry for one sheet, ready to render or export.

        Raises:
            KeyError: A placement references a part not present in
                :attr:`parts_by_id`, which means the result was assembled
                inconsistently.
        """
        out: list[tuple[str, Shape]] = []
        for layout in self.layouts:
            if layout.index != sheet_index:
                continue
            for placement in layout.placements:
                part = self.parts_by_id.get(placement.part_id)
                if part is None:
                    raise KeyError(
                        f"result references unknown part {placement.part_id!r}"
                    )
                out.append((placement.key, placement.transform.apply(part.shape)))
        return out

    def summary(self) -> str:
        """One-line human summary for the CLI and web UI."""
        bits = [
            f"{self.yield_percent:.1f}% yield",
            f"{self.sheets_used} sheet(s)",
            f"{self.placed_count} part(s) placed",
        ]
        if self.unplaced_count:
            bits.append(f"{self.unplaced_count} UNPLACED")
        if self.material_cost:
            bits.append(f"cost {self.material_cost:.2f}")
        return " | ".join(bits)

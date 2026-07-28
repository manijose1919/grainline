"""The nesting strategy contract and helpers shared by every implementation.

A strategy takes a :class:`~grainline.core.model.job.Job` and returns a
:class:`~grainline.core.model.result.NestResult`. That is the entire interface,
and it is deliberately narrow: the free guillotine packer, the Premium
no-fit-polygon kernel and any future strategy are interchangeable behind it, so
the CLI, web UI and REST API never learn which tier is running.
"""

from __future__ import annotations

import abc
import time
from collections import Counter
from dataclasses import dataclass
from typing import Iterable, Sequence

from ..geometry.primitives import Bounds, Shape
from ..geometry.transform import Transform
from ..model.job import Job
from ..model.part import PartInstance
from ..model.result import NestResult, Placement, SheetLayout
from ..model.stock import Stock

__all__ = [
    "NestingStrategy",
    "OrientationCache",
    "Orientation",
    "StockPool",
    "sort_instances",
    "iter_sheets",
    "NestingError",
]


class NestingError(RuntimeError):
    """Raised when a job cannot be nested for a structural reason."""


@dataclass(frozen=True, slots=True)
class Orientation:
    """A shape's footprint at one rotation angle.

    ``offset`` is what makes placement arithmetic simple: after rotating about
    the origin, a shape's bounding box no longer starts at ``(0, 0)``. Storing
    the correction here means the packer only ever thinks in terms of "put the
    bottom-left of the footprint at ``(x, y)``" and never has to reason about
    where the geometry drifted to.
    """

    angle: float
    width: float
    height: float
    offset: tuple[float, float]

    def transform_to(self, x: float, y: float, *, mirror: bool = False) -> Transform:
        """Transform placing this orientation's bottom-left corner at ``(x, y)``."""
        return Transform(
            rotation=self.angle,
            tx=x - self.offset[0],
            ty=y - self.offset[1],
            mirror_x=mirror,
        )

    def bounds_at(self, x: float, y: float) -> Bounds:
        """Axis-aligned bounds once placed at ``(x, y)``."""
        return (x, y, x + self.width, y + self.height)


class OrientationCache:
    """Precomputed footprints for every part at every permitted angle.

    Rotating a 2,000-vertex contour to measure its bounding box costs real time,
    and a packer asks for the same measurement thousands of times. Computing
    each one exactly once and reusing it is the difference between a nest that
    takes a second and one that takes a minute.
    """

    __slots__ = ("_cache",)

    def __init__(self) -> None:
        self._cache: dict[tuple[str, float, bool], Orientation] = {}

    def get(
        self, key: str, shape: Shape, angle: float, *, mirror: bool = False
    ) -> Orientation:
        """Footprint of ``shape`` at ``angle``, computed once per key/angle pair."""
        cache_key = (key, angle, mirror)
        hit = self._cache.get(cache_key)
        if hit is not None:
            return hit

        if angle == 0.0 and not mirror:
            min_x, min_y, max_x, max_y = shape.bounds
        else:
            probe = Transform(rotation=angle, mirror_x=mirror)
            pts = probe.apply_points(shape.outer.points)
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            min_x, min_y, max_x, max_y = min(xs), min(ys), max(xs), max(ys)

        orientation = Orientation(
            angle=angle,
            width=max_x - min_x,
            height=max_y - min_y,
            offset=(min_x, min_y),
        )
        self._cache[cache_key] = orientation
        return orientation

    def orientations(
        self, key: str, shape: Shape, angles: Sequence[float], *, mirror: bool = False
    ) -> list[Orientation]:
        """Footprints at every angle, de-duplicated by footprint size.

        A square is identical at 0 and 90 degrees; trying both doubles the
        search for nothing. Collapsing duplicates here is a pure win and is
        invisible to the packer.
        """
        seen: dict[tuple[float, float], Orientation] = {}
        out: list[Orientation] = []
        for angle in angles:
            orientation = self.get(key, shape, angle, mirror=mirror)
            footprint = (round(orientation.width, 6), round(orientation.height, 6))
            if footprint in seen:
                continue
            seen[footprint] = orientation
            out.append(orientation)
        return out


def sort_instances(
    instances: Iterable[PartInstance], sort_key: str
) -> list[PartInstance]:
    """Order instances for placement.

    Descending size is the classic first-fit-decreasing insight: place the
    awkward big pieces while the sheet is still empty and there is somewhere for
    them to go, then let small parts fill the gaps they leave. Part priority
    always outranks size, so a rush job still makes the sheet.
    """
    items = list(instances)

    def measure(inst: PartInstance) -> float:
        shape = inst.shape
        if sort_key == "longest_side":
            return max(shape.width, shape.height)
        if sort_key == "perimeter":
            return shape.outer.perimeter
        if sort_key == "height":
            return shape.height
        return shape.area

    items.sort(key=lambda i: (-i.part.priority, -measure(i), i.key))
    return items


def iter_sheets(stock: Sequence[Stock], material: str = "") -> Iterable[Stock]:
    """Yield sheets in preference order, respecting on-hand quantities.

    Stock with a finite quantity yields that many times and then stops; stock
    with unlimited quantity yields forever. Callers must therefore impose their
    own termination condition — always "no parts left to place".
    """
    for sheet in stock:
        if material and not sheet.accepts(material):
            continue
        if sheet.quantity is None:
            while True:
                yield sheet
        else:
            for _ in range(sheet.quantity):
                yield sheet


class StockPool:
    """Hands out sheets in preference order while honouring on-hand quantities.

    Shared by every strategy rather than reimplemented per engine, because stock
    accounting must be correct *across materials*: an aluminium run and an
    acrylic run in the same job must not both believe they hold the last sheet
    of a shared grade.
    """

    __slots__ = ("_order", "_remaining", "_consumed")

    def __init__(self, stock: Sequence[Stock]) -> None:
        self._order = list(stock)
        self._remaining: dict[str, int | None] = {s.id: s.quantity for s in stock}
        self._consumed: Counter[str] = Counter()

    def take(self, material: str) -> Stock | None:
        """Reserve the next suitable sheet, or ``None`` when none is available."""
        for sheet in self._order:
            if not sheet.accepts(material):
                continue
            remaining = self._remaining[sheet.id]
            if remaining is None:
                self._consumed[sheet.id] += 1
                return sheet
            if remaining > 0:
                self._remaining[sheet.id] = remaining - 1
                self._consumed[sheet.id] += 1
                return sheet
        return None

    def give_back(self, sheet: Stock) -> None:
        """Return an unused sheet to the pool.

        Called when a sheet is opened but nothing will fit on it. Without this,
        a job that trips the "nothing fits" path silently burns inventory that
        was never cut.
        """
        self._consumed[sheet.id] -= 1
        remaining = self._remaining[sheet.id]
        if remaining is not None:
            self._remaining[sheet.id] = remaining + 1

    @property
    def consumed(self) -> Counter[str]:
        """Sheets taken and not given back, by stock id."""
        return Counter(self._consumed)


class NestingStrategy(abc.ABC):
    """Base class for every nesting implementation."""

    #: Registry key. Subclasses must override.
    name: str = "abstract"
    #: One-line description shown by ``grainline strategies``.
    summary: str = ""

    @abc.abstractmethod
    def nest(self, job: Job) -> NestResult:
        """Produce a nest for ``job``.

        Implementations must place as many part instances as they can and record
        the rest in :attr:`NestResult.unplaced`. Raising because a part does not
        fit is wrong: a shop with one oversized part still wants the nest for
        the other forty.
        """

    # -- shared plumbing --------------------------------------------------

    def _new_result(self, job: Job) -> NestResult:
        """A result pre-populated with this strategy's identity and the part map."""
        return NestResult(
            strategy=self.name,
            parts_by_id={part.id: part for part in job.parts},
        )

    @staticmethod
    def _finalise(
        result: NestResult,
        layouts: list[SheetLayout],
        unplaced: dict[str, int],
        started: float,
    ) -> NestResult:
        """Attach layouts and timing, dropping sheets that ended up empty."""
        result.layouts = [layout for layout in layouts if layout.placements]
        # Renumber so sheet indices are contiguous after empties are dropped;
        # operators reference sheets by number on the shop floor and a gap in
        # the sequence reads as a missing setup sheet.
        for new_index, layout in enumerate(result.layouts):
            layout.index = new_index
        result.unplaced = {k: v for k, v in unplaced.items() if v > 0}
        result.elapsed_s = time.perf_counter() - started
        return result

    def _place(
        self,
        layout: SheetLayout,
        instance: PartInstance,
        orientation: Orientation,
        x: float,
        y: float,
        *,
        mirror: bool = False,
    ) -> Placement:
        """Record a placement on ``layout`` and update its accumulated area."""
        placement = Placement(
            part_id=instance.part.id,
            instance=instance.index,
            transform=orientation.transform_to(x, y, mirror=mirror),
            bounds=orientation.bounds_at(x, y),
        )
        layout.placements.append(placement)
        layout.placed_area += instance.area
        return placement

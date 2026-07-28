"""Rectangular guillotine nesting — the Free-tier engine.

A guillotine nest contains only cuts that run edge to edge across whatever is
left of the board. That is not an arbitrary restriction: it is exactly what a
panel saw, a shear and a slitter can physically do. For those machines this
result is not a compromise, it is the only legal answer.

The algorithm is a free-rectangle packer:

1. Start with one free rectangle: the usable sheet area, pre-inflated by half a
   gap on every side (see below).
2. Take parts largest-first and, for each, find the free rectangle and rotation
   that leaves the least wasted area (Best Area Fit).
3. Place at that rectangle's bottom-left corner and guillotine-split the
   remainder into two new free rectangles along the shorter leftover axis.
4. Merge and prune free rectangles so that adjacent offcuts can host a part
   neither could hold alone.

**Clearance is modelled as a symmetric halo**, not as padding on two sides. Each
part claims a box of ``width + gap`` by ``height + gap`` and sits at its centre,
inset by ``gap/2`` on every side. Two neighbouring parts each contribute half the
clearance, summing to exactly one gap.

The one-sided alternative is subtly and dangerously wrong. Padding only the
right and top means the free rectangle carved out *above* a part inherits that
part's right-hand padding as usable width — so a wider part placed above can
reach into a gap band already reserved against a different neighbour, and two
parts that never shared a free rectangle end up touching. Pre-inflating the
initial rectangle by ``gap/2`` keeps edge parts flush with the sheet margin, so
the symmetric model costs no material at the boundary.

Its ceiling is that it works on bounding boxes. Two L-brackets that interlock
perfectly are, to this packer, two rectangles that cannot overlap. Recovering
that material requires the no-fit-polygon kernel in the Premium tier — which is
an honest limitation of the method, not a withheld feature.
"""

from __future__ import annotations

import time
from collections import Counter, defaultdict
from dataclasses import dataclass

from ..model.job import Job
from ..model.part import PartInstance
from ..model.result import NestResult, SheetLayout
from ..model.stock import Stock
from .base import (
    NestingStrategy,
    Orientation,
    OrientationCache,
    StockPool,
    sort_instances,
)

__all__ = ["GuillotineNester", "FreeRect"]

#: Numerical slack for fit comparisons, in mm. Well below machine resolution but
#: large enough to absorb the floating-point error of rotating a contour.
_FIT_EPS: float = 1e-7

#: Hard ceiling on sheets opened for a single job. Unlimited stock means the
#: sheet generator never terminates on its own; this converts a pathological
#: job into a reported failure instead of a hang.
_MAX_SHEETS: int = 10_000

#: Free rectangles smaller than this in either axis are discarded. Slivers a
#: fraction of a millimetre wide can never hold a part and only slow the search.
_MIN_USEFUL_DIM: float = 0.5


@dataclass(slots=True)
class FreeRect:
    """An axis-aligned region of a sheet still available for parts."""

    x: float
    y: float
    width: float
    height: float

    @property
    def area(self) -> float:
        return self.width * self.height

    @property
    def right(self) -> float:
        return self.x + self.width

    @property
    def top(self) -> float:
        return self.y + self.height

    def contains(self, other: "FreeRect") -> bool:
        """True when ``other`` lies entirely within this rectangle."""
        return (
            other.x >= self.x - _FIT_EPS
            and other.y >= self.y - _FIT_EPS
            and other.right <= self.right + _FIT_EPS
            and other.top <= self.top + _FIT_EPS
        )

    def is_useful(self, min_dim: float = _MIN_USEFUL_DIM) -> bool:
        """True when this rectangle is big enough to be worth considering.

        Callers pass ``gap + floor`` so that a rectangle too narrow to hold any
        part once its clearance halo is accounted for is discarded immediately
        rather than re-scanned on every subsequent placement.
        """
        return self.width >= min_dim and self.height >= min_dim


class GuillotineNester(NestingStrategy):
    """Best-Area-Fit guillotine packer with shorter-leftover-axis splitting."""

    name = "guillotine"
    summary = "Rectangular guillotine nesting - edge-to-edge cuts only (Free tier)"

    def nest(self, job: Job) -> NestResult:
        started = time.perf_counter()
        result = self._new_result(job)

        if not job.parts or not job.stock:
            result.notes.append("job has no parts or no stock; nothing to nest")
            return self._finalise(result, [], {}, started)

        cache = OrientationCache()
        pool = StockPool(job.stock)
        layouts: list[SheetLayout] = []
        unplaced: Counter[str] = Counter()

        # Group by material so each run only ever sees stock it can legally use.
        by_material: dict[str, list[PartInstance]] = defaultdict(list)
        for part in job.parts:
            by_material[part.material].extend(part.instances())

        for material in sorted(by_material, key=lambda m: (m == "", m)):
            pending = sort_instances(by_material[material], job.config.sort_key)

            while pending:
                if len(layouts) >= _MAX_SHEETS:
                    result.notes.append(
                        f"stopped after {_MAX_SHEETS} sheets; check that part "
                        f"sizes and stock sizes are in the same units"
                    )
                    for inst in pending:
                        unplaced[inst.part.id] += 1
                    pending = []
                    break

                sheet = pool.take(material)
                if sheet is None:
                    for inst in pending:
                        unplaced[inst.part.id] += 1
                    result.notes.append(
                        f"ran out of stock for material {material or 'default'!r}; "
                        f"{len(pending)} part(s) unplaced"
                    )
                    break

                layout = SheetLayout(index=len(layouts), stock=sheet)
                leftovers = self._fill_sheet(layout, pending, job, cache)

                if not layout.placements:
                    # Nothing on this sheet fits at all. Return it unused and
                    # report the remaining parts rather than looping forever.
                    pool.give_back(sheet)
                    for inst in leftovers:
                        unplaced[inst.part.id] += 1
                    result.notes.append(
                        f"{len(leftovers)} part(s) do not fit on any available "
                        f"stock for material {material or 'default'!r}"
                    )
                    break

                layouts.append(layout)
                pending = leftovers

        return self._finalise(result, layouts, dict(unplaced), started)

    # -- sheet filling ----------------------------------------------------

    def _fill_sheet(
        self,
        layout: SheetLayout,
        pending: list[PartInstance],
        job: Job,
        cache: OrientationCache,
    ) -> list[PartInstance]:
        """Place as many pending instances as fit; return those that did not."""
        config = job.config
        sheet = layout.stock
        gap = config.gap

        origin_x = sheet.trim_margin + config.sheet_margin
        origin_y = sheet.trim_margin + config.sheet_margin
        usable_w = sheet.usable_width - 2.0 * config.sheet_margin
        usable_h = sheet.usable_height - 2.0 * config.sheet_margin

        if usable_w <= 0 or usable_h <= 0:
            return list(pending)

        # Pre-inflate by half a gap on every side. A part then sits at its
        # rectangle's origin plus gap/2, which puts an edge part exactly on the
        # margin while still giving interior neighbours a full gap between them.
        half = gap / 2.0
        free: list[FreeRect] = [
            FreeRect(origin_x - half, origin_y - half, usable_w + gap, usable_h + gap)
        ]
        leftovers: list[PartInstance] = []
        min_dim = gap + _MIN_USEFUL_DIM

        for instance in pending:
            orientations = cache.orientations(
                instance.part.id,
                instance.shape,
                instance.part.permitted_angles(config.effective_rotations),
            )

            choice = self._best_fit(free, orientations, gap)
            if choice is None:
                leftovers.append(instance)
                continue

            rect_index, orientation = choice
            rect = free[rect_index]
            self._place(layout, instance, orientation, rect.x + half, rect.y + half)

            free.pop(rect_index)
            free.extend(
                self._split(
                    rect,
                    orientation.width + gap,
                    orientation.height + gap,
                    min_dim=min_dim,
                )
            )
            free = self._prune(free, min_dim=min_dim)

        return leftovers

    @staticmethod
    def _best_fit(
        free: list[FreeRect], orientations: list[Orientation], gap: float
    ) -> tuple[int, Orientation] | None:
        """Pick the free rectangle and rotation wasting the least area.

        Best Area Fit beats first-fit substantially on mixed part sizes because
        it protects large free rectangles from being spent on small parts that
        a cramped offcut could have held instead. Ties break on the shorter
        leftover side, which keeps the surviving offcuts squarer and therefore
        more useful to later parts.
        """
        best: tuple[int, Orientation] | None = None
        best_score: tuple[float, float] = (float("inf"), float("inf"))

        for index, rect in enumerate(free):
            for orientation in orientations:
                # The claimed box is the part plus its full clearance halo; that
                # is what must fit, not the bare geometry.
                slack_w = rect.width - (orientation.width + gap)
                slack_h = rect.height - (orientation.height + gap)
                if slack_w < -_FIT_EPS or slack_h < -_FIT_EPS:
                    continue
                score = (
                    rect.area - (orientation.width + gap) * (orientation.height + gap),
                    min(slack_w, slack_h),
                )
                if score < best_score:
                    best_score = score
                    best = (index, orientation)

        return best

    @staticmethod
    def _split(
        rect: FreeRect,
        used_w: float,
        used_h: float,
        *,
        min_dim: float = _MIN_USEFUL_DIM,
    ) -> list[FreeRect]:
        """Guillotine-split ``rect`` after consuming a ``used_w`` x ``used_h`` corner.

        Splitting along the *shorter* leftover axis keeps the larger remaining
        piece intact. The alternative leaves two mediocre strips where one good
        rectangle and one scrap would have served better.
        """
        leftover_w = rect.width - used_w
        leftover_h = rect.height - used_h

        pieces: list[FreeRect] = []
        if leftover_w < leftover_h:
            # Horizontal cut: the right-hand strip is limited to the used height.
            pieces.append(FreeRect(rect.x + used_w, rect.y, leftover_w, used_h))
            pieces.append(FreeRect(rect.x, rect.y + used_h, rect.width, leftover_h))
        else:
            # Vertical cut: the right-hand strip runs the full rectangle height.
            pieces.append(FreeRect(rect.x + used_w, rect.y, leftover_w, rect.height))
            pieces.append(FreeRect(rect.x, rect.y + used_h, used_w, leftover_h))

        return [p for p in pieces if p.is_useful(min_dim)]

    @staticmethod
    def _prune(
        free: list[FreeRect], *, min_dim: float = _MIN_USEFUL_DIM
    ) -> list[FreeRect]:
        """Merge edge-sharing rectangles and drop any fully contained in another.

        Guillotine splitting fragments the sheet; without merging, two offcuts
        that together could hold a part stay permanently unusable. One merge
        pass per placement is enough in practice and keeps the cost linear
        rather than letting it drift quadratic.
        """
        merged = True
        while merged:
            merged = False
            for i in range(len(free)):
                for j in range(i + 1, len(free)):
                    a, b = free[i], free[j]

                    # Side by side, same vertical span.
                    if (
                        abs(a.y - b.y) < _FIT_EPS
                        and abs(a.height - b.height) < _FIT_EPS
                        and (abs(a.right - b.x) < _FIT_EPS or abs(b.right - a.x) < _FIT_EPS)
                    ):
                        free[i] = FreeRect(
                            min(a.x, b.x), a.y, a.width + b.width, a.height
                        )
                        free.pop(j)
                        merged = True
                        break

                    # Stacked, same horizontal span.
                    if (
                        abs(a.x - b.x) < _FIT_EPS
                        and abs(a.width - b.width) < _FIT_EPS
                        and (abs(a.top - b.y) < _FIT_EPS or abs(b.top - a.y) < _FIT_EPS)
                    ):
                        free[i] = FreeRect(
                            a.x, min(a.y, b.y), a.width, a.height + b.height
                        )
                        free.pop(j)
                        merged = True
                        break
                if merged:
                    break

        # Drop rectangles wholly inside another; they add search cost and can
        # never win a Best Area Fit comparison against their container.
        kept: list[FreeRect] = []
        for i, rect in enumerate(free):
            if not rect.is_useful(min_dim):
                continue
            if any(
                other.contains(rect)
                for k, other in enumerate(free)
                if k != i and other.area > rect.area
            ):
                continue
            kept.append(rect)
        return kept


def register() -> None:
    """Register the free guillotine strategy with the capability registry."""
    from ..licensing.tier import Tier
    from ..registry import register_strategy

    register_strategy(
        GuillotineNester.name,
        GuillotineNester,
        tier=Tier.FREE,
        summary=GuillotineNester.summary,
        replace=True,
    )

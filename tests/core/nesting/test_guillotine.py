"""Tests for the Free-tier guillotine packer, including geometric invariants."""

from __future__ import annotations

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from grainline.core.geometry.primitives import Shape
from grainline.core.model.job import Job, NestConfig
from grainline.core.model.part import GrainConstraint, Part
from grainline.core.model.result import NestResult
from grainline.core.model.stock import Stock
from grainline.core.nesting import nest
from grainline.core.nesting.guillotine import FreeRect, GuillotineNester

from tests.conftest import l_bracket, regular_polygon, washer

pytestmark = pytest.mark.free


# ---------------------------------------------------------------------------
# Invariant helpers - reused by both example and property tests
# ---------------------------------------------------------------------------


def assert_no_overlaps(result: NestResult, gap: float) -> None:
    """Every pair of parts on a sheet must be disjoint and respect the gap.

    This is the single most important property in the product. A nest with
    overlapping parts is not a suboptimal nest, it is a destroyed sheet of
    material and a scrapped job.
    """
    eps = 1e-6
    for layout in result.sheets():
        placements = layout.placements
        for i in range(len(placements)):
            for j in range(i + 1, len(placements)):
                a, b = placements[i].bounds, placements[j].bounds
                sep_x = max(a[0], b[0]) - min(a[2], b[2])
                sep_y = max(a[1], b[1]) - min(a[3], b[3])
                assert sep_x >= -eps or sep_y >= -eps, (
                    f"bounding boxes overlap on sheet {layout.index}: "
                    f"{placements[i].key} vs {placements[j].key}"
                )
                # Guillotine reserves the gap along the split axis, so at least
                # one axis must be clear by the full gap.
                assert sep_x >= gap - eps or sep_y >= gap - eps, (
                    f"gap violated on sheet {layout.index}: "
                    f"{placements[i].key} vs {placements[j].key} "
                    f"(sep {sep_x:.4f}, {sep_y:.4f}; required {gap})"
                )


def assert_within_sheet(result: NestResult, config: NestConfig) -> None:
    """No part may hang off the usable area of its sheet."""
    eps = 1e-6
    for layout in result.sheets():
        stock = layout.stock
        lo_x = stock.trim_margin + config.sheet_margin
        lo_y = stock.trim_margin + config.sheet_margin
        hi_x = stock.width - stock.trim_margin - config.sheet_margin
        hi_y = stock.height - stock.trim_margin - config.sheet_margin
        for placement in layout.placements:
            x0, y0, x1, y1 = placement.bounds
            assert x0 >= lo_x - eps, f"{placement.key} crosses left margin"
            assert y0 >= lo_y - eps, f"{placement.key} crosses bottom margin"
            assert x1 <= hi_x + eps, f"{placement.key} crosses right margin"
            assert y1 <= hi_y + eps, f"{placement.key} crosses top margin"


def assert_conservation(result: NestResult, job: Job) -> None:
    """Every requested copy is either placed or explicitly reported unplaced."""
    for part in job.parts:
        placed = sum(
            1
            for layout in result.sheets()
            for p in layout.placements
            if p.part_id == part.id
        )
        unplaced = result.unplaced.get(part.id, 0)
        assert placed + unplaced == part.quantity, (
            f"{part.id}: {placed} placed + {unplaced} unplaced != "
            f"{part.quantity} required"
        )


def _sheet(width=1000.0, height=1000.0, **kw) -> Stock:
    return Stock(id=kw.pop("id", "sheet"), width=width, height=height, **kw)


def _simple_job(**config_kw) -> Job:
    return Job(
        parts=[Part(id="sq", shape=Shape.rectangle(100.0, 100.0), quantity=8)],
        stock=[_sheet()],
        config=NestConfig(**config_kw),
        name="simple",
    )


# ---------------------------------------------------------------------------
# Core behaviour
# ---------------------------------------------------------------------------


def test_places_all_parts_when_they_fit():
    job = _simple_job()
    result = nest(job)
    assert result.is_complete
    assert result.placed_count == 8
    assert result.sheets_used == 1
    assert_no_overlaps(result, job.config.gap)
    assert_within_sheet(result, job.config)
    assert_conservation(result, job)


def test_yield_is_measured_against_whole_sheets():
    job = Job(
        parts=[Part(id="sq", shape=Shape.rectangle(500.0, 500.0), quantity=1)],
        stock=[_sheet(1000.0, 1000.0)],
        config=NestConfig(),
    )
    result = nest(job)
    # 250,000 mm2 of part on a 1,000,000 mm2 sheet is 25% - never 100% just
    # because the parts are packed tightly into one corner.
    assert result.yield_percent == pytest.approx(25.0)


def test_opens_a_second_sheet_when_the_first_is_full():
    job = Job(
        parts=[Part(id="big", shape=Shape.rectangle(600.0, 600.0), quantity=3)],
        stock=[_sheet(1000.0, 1000.0)],
        config=NestConfig(),
    )
    result = nest(job)
    assert result.sheets_used == 3
    assert result.is_complete
    assert_no_overlaps(result, job.config.gap)


def test_sheet_indices_are_contiguous():
    job = Job(
        parts=[Part(id="big", shape=Shape.rectangle(600.0, 600.0), quantity=4)],
        stock=[_sheet(1000.0, 1000.0)],
    )
    result = nest(job)
    assert [layout.index for layout in result.sheets()] == list(
        range(result.sheets_used)
    )


def test_respects_finite_stock_quantity():
    job = Job(
        parts=[Part(id="big", shape=Shape.rectangle(600.0, 600.0), quantity=5)],
        stock=[_sheet(1000.0, 1000.0, quantity=2)],
    )
    result = nest(job)
    assert result.sheets_used == 2
    assert result.unplaced == {"big": 3}
    assert not result.is_complete
    assert any("ran out of stock" in n for n in result.notes)


def test_oversized_part_is_reported_not_raised():
    job = Job(
        parts=[
            Part(id="ok", shape=Shape.rectangle(100.0, 100.0), quantity=2),
            Part(id="huge", shape=Shape.rectangle(5000.0, 5000.0), quantity=1),
        ],
        stock=[_sheet(1000.0, 1000.0)],
    )
    result = nest(job)
    assert result.unplaced == {"huge": 1}
    # The rest of the job must still nest - a shop with one bad part still
    # wants the sheet for the other forty.
    assert result.placed_count == 2


def test_unlimited_stock_does_not_hang_on_an_unplaceable_part():
    job = Job(
        parts=[Part(id="huge", shape=Shape.rectangle(9000.0, 9000.0), quantity=4)],
        stock=[_sheet(1000.0, 1000.0)],
    )
    result = nest(job)
    assert result.sheets_used == 0
    assert result.unplaced == {"huge": 4}


def test_unused_sheet_is_returned_to_the_pool():
    """A sheet opened but never cut must not be counted as consumed."""
    job = Job(
        parts=[Part(id="huge", shape=Shape.rectangle(9000.0, 9000.0), quantity=1)],
        stock=[_sheet(1000.0, 1000.0, quantity=1)],
    )
    result = nest(job)
    assert result.consumed_area == 0.0
    assert result.material_cost == 0.0


# ---------------------------------------------------------------------------
# Rotation and grain
# ---------------------------------------------------------------------------


def test_rotation_lets_a_tall_part_fit_a_wide_sheet():
    part = Part(id="strip", shape=Shape.rectangle(50.0, 900.0), quantity=1)
    sheet = Stock(id="wide", width=1000.0, height=100.0)

    rotated = nest(Job(parts=[part], stock=[sheet], config=NestConfig()))
    assert rotated.is_complete

    pinned = nest(
        Job(parts=[part], stock=[sheet], config=NestConfig(allow_rotation=False))
    )
    assert pinned.unplaced == {"strip": 1}


def test_fixed_grain_prevents_rotation():
    part = Part(
        id="veneer",
        shape=Shape.rectangle(50.0, 900.0),
        quantity=1,
        grain=GrainConstraint.FIXED,
    )
    sheet = Stock(id="wide", width=1000.0, height=100.0)
    result = nest(Job(parts=[part], stock=[sheet]))
    assert result.unplaced == {"veneer": 1}


def test_bidirectional_grain_allows_only_half_turns():
    part = Part(
        id="grain",
        shape=Shape.rectangle(100.0, 200.0),
        quantity=2,
        grain=GrainConstraint.BIDIRECTIONAL,
    )
    result = nest(Job(parts=[part], stock=[_sheet(1000.0, 1000.0)]))
    for layout in result.sheets():
        for placement in layout.placements:
            assert placement.transform.rotation % 180.0 == 0.0


# ---------------------------------------------------------------------------
# Kerf, spacing and margins
# ---------------------------------------------------------------------------


def test_gap_is_the_larger_of_kerf_and_spacing():
    assert NestConfig(kerf=3.2, part_spacing=1.0).gap == pytest.approx(3.2)
    assert NestConfig(kerf=0.15, part_spacing=4.0).gap == pytest.approx(4.0)


def test_wide_kerf_is_honoured_between_parts():
    config = NestConfig(kerf=10.0, part_spacing=0.0)
    job = Job(
        parts=[Part(id="sq", shape=Shape.rectangle(100.0, 100.0), quantity=6)],
        stock=[_sheet(1000.0, 1000.0)],
        config=config,
    )
    result = nest(job)
    assert_no_overlaps(result, 10.0)


def test_sheet_margin_keeps_parts_off_the_edge():
    config = NestConfig(sheet_margin=25.0)
    job = Job(
        parts=[Part(id="sq", shape=Shape.rectangle(100.0, 100.0), quantity=10)],
        stock=[_sheet(1000.0, 1000.0)],
        config=config,
    )
    result = nest(job)
    assert_within_sheet(result, config)
    for layout in result.sheets():
        for placement in layout.placements:
            assert placement.bounds[0] >= 25.0 - 1e-6


def test_trim_margin_shrinks_the_usable_area():
    job = Job(
        parts=[Part(id="sq", shape=Shape.rectangle(100.0, 100.0), quantity=4)],
        stock=[_sheet(1000.0, 1000.0, trim_margin=50.0)],
        config=NestConfig(sheet_margin=0.0),
    )
    result = nest(job)
    for layout in result.sheets():
        for placement in layout.placements:
            assert placement.bounds[0] >= 50.0 - 1e-6
            assert placement.bounds[2] <= 950.0 + 1e-6


# ---------------------------------------------------------------------------
# Materials and stock selection
# ---------------------------------------------------------------------------


def test_parts_only_nest_on_matching_material():
    job = Job(
        parts=[
            Part(id="al", shape=Shape.rectangle(200.0, 200.0), quantity=2,
                 material="aluminium"),
            Part(id="ac", shape=Shape.rectangle(200.0, 200.0), quantity=2,
                 material="acrylic"),
        ],
        stock=[
            Stock(id="al-sheet", width=1000.0, height=1000.0, material="aluminium"),
            Stock(id="ac-sheet", width=1000.0, height=1000.0, material="acrylic"),
        ],
    )
    result = nest(job)
    assert result.is_complete
    for layout in result.sheets():
        for placement in layout.placements:
            expected = "al-sheet" if placement.part_id == "al" else "ac-sheet"
            assert layout.stock.id == expected


def test_material_shortage_is_reported_per_material():
    job = Job(
        parts=[
            Part(id="al", shape=Shape.rectangle(900.0, 900.0), quantity=2,
                 material="aluminium"),
            Part(id="ac", shape=Shape.rectangle(200.0, 200.0), quantity=1,
                 material="acrylic"),
        ],
        stock=[
            Stock(id="al-sheet", width=1000.0, height=1000.0,
                  material="aluminium", quantity=1),
            Stock(id="ac-sheet", width=1000.0, height=1000.0, material="acrylic"),
        ],
    )
    result = nest(job)
    assert result.unplaced == {"al": 1}
    assert result.placed_count == 2


def test_remnant_stock_is_consumed_before_full_sheets():
    """Stock order is preference order, so remnants listed first burn off first."""
    job = Job(
        parts=[Part(id="p", shape=Shape.rectangle(180.0, 180.0), quantity=1)],
        stock=[
            Stock(id="remnant", width=200.0, height=200.0, quantity=1, is_remnant=True),
            Stock(id="full", width=2440.0, height=1220.0),
        ],
        config=NestConfig(sheet_margin=0.0, part_spacing=1.0),
    )
    result = nest(job)
    assert result.sheets()[0].stock.id == "remnant"


# ---------------------------------------------------------------------------
# Determinism and reporting
# ---------------------------------------------------------------------------


def test_nesting_is_deterministic():
    """A shop re-running a job must get the identical sheet back."""
    def run():
        return nest(
            Job(
                parts=[
                    Part(id="a", shape=l_bracket(), quantity=5),
                    Part(id="b", shape=washer(30.0, 12.0, 24), quantity=7),
                    Part(id="c", shape=Shape.rectangle(120.0, 45.0), quantity=4),
                ],
                stock=[_sheet(1200.0, 1200.0)],
            )
        )

    first, second = run(), run()
    assert first.placed_count == second.placed_count
    assert first.utilisation == pytest.approx(second.utilisation)
    keys_a = [(p.key, p.transform) for lay in first.sheets() for p in lay.placements]
    keys_b = [(p.key, p.transform) for lay in second.sheets() for p in lay.placements]
    assert keys_a == keys_b


def test_priority_parts_are_placed_first():
    job = Job(
        parts=[
            Part(id="filler", shape=Shape.rectangle(400.0, 400.0), quantity=4),
            Part(id="rush", shape=Shape.rectangle(100.0, 100.0), quantity=1,
                 priority=10),
        ],
        stock=[_sheet(900.0, 900.0, quantity=1)],
    )
    result = nest(job)
    placed_ids = {p.part_id for lay in result.sheets() for p in lay.placements}
    assert "rush" in placed_ids


def test_result_summary_mentions_yield_and_sheets():
    result = nest(_simple_job())
    summary = result.summary()
    assert "yield" in summary
    assert "sheet(s)" in summary


def test_empty_job_raises_nesting_error():
    from grainline.core.nesting.base import NestingError

    with pytest.raises(NestingError, match="no parts"):
        nest(Job(parts=[], stock=[_sheet()]))


def test_holes_do_not_count_as_material():
    solid = nest(
        Job(
            parts=[Part(id="s", shape=Shape.rectangle(200.0, 200.0), quantity=1)],
            stock=[_sheet(1000.0, 1000.0)],
        )
    )
    holed = nest(
        Job(
            parts=[Part(id="h", shape=washer(100.0, 60.0, 64), quantity=1)],
            stock=[_sheet(1000.0, 1000.0)],
        )
    )
    assert holed.placed_area < solid.placed_area


# ---------------------------------------------------------------------------
# FreeRect unit behaviour
# ---------------------------------------------------------------------------


def test_freerect_containment():
    outer = FreeRect(0.0, 0.0, 100.0, 100.0)
    inner = FreeRect(10.0, 10.0, 20.0, 20.0)
    assert outer.contains(inner)
    assert not inner.contains(outer)


def test_split_along_shorter_leftover_axis():
    rect = FreeRect(0.0, 0.0, 100.0, 100.0)
    # Consuming 90x10 leaves 10 wide and 90 tall - the shorter leftover is width.
    pieces = GuillotineNester._split(rect, 90.0, 10.0)
    assert len(pieces) == 2
    right = next(p for p in pieces if p.x > 0)
    assert right.height == pytest.approx(10.0)


def test_split_discards_sliver_pieces():
    rect = FreeRect(0.0, 0.0, 100.0, 100.0)
    pieces = GuillotineNester._split(rect, 99.9, 99.9)
    assert pieces == []


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------

part_dimension = st.floats(min_value=15.0, max_value=380.0, allow_nan=False, width=32)


@settings(
    max_examples=60,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
)
@given(
    st.lists(
        st.tuples(part_dimension, part_dimension, st.integers(1, 4)),
        min_size=1,
        max_size=6,
    ),
    st.floats(min_value=0.0, max_value=8.0, allow_nan=False, width=32),
)
def test_property_nest_never_overlaps_and_stays_on_sheet(specs, spacing: float):
    """The load-bearing invariant, over randomly generated jobs."""
    parts = [
        Part(id=f"p{i}", shape=Shape.rectangle(w, h), quantity=q)
        for i, (w, h, q) in enumerate(specs)
    ]
    config = NestConfig(kerf=0.2, part_spacing=spacing, sheet_margin=5.0)
    job = Job(parts=parts, stock=[_sheet(1000.0, 800.0)], config=config)

    result = nest(job)

    assert_no_overlaps(result, config.gap)
    assert_within_sheet(result, config)
    assert_conservation(result, job)
    assert 0.0 <= result.utilisation <= 1.0


@settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(st.integers(min_value=3, max_value=10), st.integers(min_value=1, max_value=6))
def test_property_irregular_parts_never_overlap(sides: int, count: int):
    """Same invariant with concave and holed geometry rather than rectangles."""
    parts = [
        Part(id="poly", shape=Shape.of(regular_polygon(sides, 60.0)), quantity=count),
        Part(id="bracket", shape=l_bracket(120.0, 70.0, 22.0), quantity=count),
        Part(id="ring", shape=washer(45.0, 20.0, 24), quantity=count),
    ]
    config = NestConfig(part_spacing=3.0)
    job = Job(parts=parts, stock=[_sheet(1400.0, 1000.0)], config=config)

    result = nest(job)
    assert_no_overlaps(result, config.gap)
    assert_within_sheet(result, config)
    assert_conservation(result, job)

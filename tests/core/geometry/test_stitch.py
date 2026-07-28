"""Tests for CAD loop reconstruction - the make-or-break importer component."""

from __future__ import annotations

import math

import pytest

from grainline.core.geometry.stitch import Polyline, stitch_loops

from tests.conftest import regular_polygon

pytestmark = pytest.mark.free


def _segments(points, *, jitter: float = 0.0) -> list[Polyline]:
    """Explode a ring into individual two-point segments, optionally jittered.

    ``jitter`` simulates the endpoint rounding error that real CAD exports
    carry, which is precisely what the welding tolerance exists to absorb.
    """
    out: list[Polyline] = []
    n = len(points)
    for i in range(n):
        a = points[i]
        b = points[(i + 1) % n]
        if jitter:
            a = (a[0] + jitter, a[1] - jitter)
            b = (b[0] - jitter, b[1] + jitter)
        out.append(Polyline(points=[a, b], closed=False, source="LINE"))
    return out


def test_already_closed_polyline_passes_through():
    ring = regular_polygon(6, 25.0)
    result = stitch_loops([Polyline(points=list(ring), closed=True)])
    assert result.closed_count == 1
    assert result.open_count == 0
    assert len(result.rings[0]) == 6


def test_closed_polyline_with_repeated_last_point_is_trimmed():
    ring = regular_polygon(5, 10.0)
    pl = Polyline(points=[*ring, ring[0]], closed=True)
    result = stitch_loops([pl])
    assert len(result.rings[0]) == 5


def test_exploded_square_is_reassembled():
    square = [(0.0, 0.0), (100.0, 0.0), (100.0, 100.0), (0.0, 100.0)]
    result = stitch_loops(_segments(square))
    assert result.closed_count == 1
    assert result.open_count == 0
    assert len(result.rings[0]) == 4


def test_exploded_segments_are_welded_across_rounding_error():
    """The core scenario: endpoints that agree only to CAD precision."""
    square = [(0.0, 0.0), (100.0, 0.0), (100.0, 100.0), (0.0, 100.0)]
    result = stitch_loops(_segments(square, jitter=2e-4), tolerance=1e-3)
    assert result.closed_count == 1
    assert result.open_count == 0


def test_weld_tolerance_too_tight_reports_failure_rather_than_silently_dropping():
    square = [(0.0, 0.0), (100.0, 0.0), (100.0, 100.0), (0.0, 100.0)]
    result = stitch_loops(_segments(square, jitter=0.5), tolerance=1e-6)
    assert result.closed_count == 0
    # Every segment must be accounted for; nothing may vanish.
    assert result.open_count == 4


def test_segments_supplied_out_of_order_still_close():
    ring = regular_polygon(8, 40.0)
    segs = _segments(ring)
    shuffled = [segs[3], segs[7], segs[0], segs[5], segs[1], segs[6], segs[2], segs[4]]
    result = stitch_loops(shuffled)
    assert result.closed_count == 1
    assert len(result.rings[0]) == 8


def test_reversed_segments_still_close():
    ring = regular_polygon(6, 30.0)
    segs = _segments(ring)
    # Flip the direction of half the segments, as an exploded polyline often has.
    for i in (1, 3, 5):
        segs[i].points.reverse()
    result = stitch_loops(segs)
    assert result.closed_count == 1
    assert len(result.rings[0]) == 6


def test_two_independent_loops_are_separated():
    a = _segments([(0, 0), (10, 0), (10, 10), (0, 10)])
    b = _segments([(50, 50), (60, 50), (60, 60), (50, 60)])
    result = stitch_loops(a + b)
    assert result.closed_count == 2
    assert result.open_count == 0


def test_mixed_closed_and_exploded_input():
    closed = Polyline(points=regular_polygon(5, 8.0, centre=(200.0, 200.0)), closed=True)
    exploded = _segments([(0, 0), (20, 0), (20, 20), (0, 20)])
    result = stitch_loops([closed, *exploded])
    assert result.closed_count == 2


def test_dangling_tail_is_reported_not_hidden():
    square = _segments([(0, 0), (10, 0), (10, 10), (0, 10)])
    stray = Polyline(points=[(500.0, 500.0), (520.0, 505.0)], closed=False)
    result = stitch_loops([*square, stray])
    assert result.closed_count == 1
    assert result.open_count == 1


def test_backtracks_when_the_straightest_branch_is_a_dead_end():
    """A spur that is *straighter* than the true edge must not destroy the loop.

    Walking east along the bottom edge, the spur to (140, -40) is a 45 degree
    turn while the real next edge turns a full 90. Straightest-continuation
    therefore takes the spur first. Only backtracking recovers the part.
    """
    square = [(0.0, 0.0), (100.0, 0.0), (100.0, 100.0), (0.0, 100.0)]
    segs = _segments(square)
    segs.append(Polyline(points=[(100.0, 0.0), (140.0, -40.0)], closed=False))

    result = stitch_loops(segs)
    assert result.closed_count == 1
    assert len(result.rings[0]) == 4
    assert result.open_count == 1


def test_backtracks_past_a_multi_segment_blind_alley():
    """The dead end is three segments deep, so one level of undo is not enough."""
    square = [(0.0, 0.0), (100.0, 0.0), (100.0, 100.0), (0.0, 100.0)]
    segs = _segments(square)
    segs.append(Polyline(points=[(100.0, 0.0), (150.0, -50.0)], closed=False))
    segs.append(Polyline(points=[(150.0, -50.0), (200.0, -100.0)], closed=False))
    segs.append(Polyline(points=[(200.0, -100.0), (250.0, -150.0)], closed=False))

    result = stitch_loops(segs)
    assert result.closed_count == 1
    assert len(result.rings[0]) == 4


def test_straightest_branch_is_still_preferred_when_both_close():
    """Backtracking must not cost us the heuristic when the first guess is right."""
    ring = regular_polygon(10, 50.0)
    result = stitch_loops(_segments(ring))
    assert result.closed_count == 1
    assert len(result.rings[0]) == 10


def test_shared_edge_between_two_loops_does_not_starve_the_second():
    """Consuming a failed seed must not consume its neighbours."""
    a = _segments([(0, 0), (10, 0), (10, 10), (0, 10)])
    stray = Polyline(points=[(0.0, 0.0), (-40.0, -40.0)], closed=False)
    b = _segments([(100, 100), (110, 100), (110, 110), (100, 110)])
    result = stitch_loops([stray, *a, *b])
    assert result.closed_count == 2
    assert result.open_count == 1


def test_grid_boundary_points_are_welded():
    """Two points either side of a hash-cell boundary must still weld.

    A naive implementation that rounds coordinates to a grid fails this: the
    points land in different buckets and never meet.
    """
    tol = 1e-3
    a = (tol * 3.0, 0.0)
    b = (tol * 3.0 + tol * 0.4, 0.0)
    segs = [
        Polyline(points=[(0.0, 0.0), a], closed=False),
        Polyline(points=[b, (0.0, 50.0)], closed=False),
        Polyline(points=[(0.0, 50.0), (0.0, 0.0)], closed=False),
    ]
    result = stitch_loops(segs, tolerance=tol)
    assert result.closed_count == 1


def test_single_segment_cannot_form_a_loop():
    result = stitch_loops([Polyline(points=[(0, 0), (10, 0)], closed=False)])
    assert result.closed_count == 0
    assert result.open_count == 1


def test_degenerate_input_is_ignored():
    result = stitch_loops([Polyline(points=[(0, 0)], closed=False)])
    assert result.closed_count == 0
    assert result.open_count == 0


def test_open_run_that_meets_itself_is_treated_as_closed():
    ring = regular_polygon(7, 15.0)
    pl = Polyline(points=[*ring, ring[0]], closed=False)
    result = stitch_loops([pl])
    assert result.closed_count == 1
    assert len(result.rings[0]) == 7


def test_stitched_ring_has_no_duplicate_consecutive_points():
    ring = regular_polygon(12, 33.0)
    result = stitch_loops(_segments(ring))
    pts = result.rings[0]
    for i in range(len(pts)):
        assert math.dist(pts[i - 1], pts[i]) > 1e-6

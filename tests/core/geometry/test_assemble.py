"""Tests for containment-parity hole assignment and ring assembly."""

from __future__ import annotations

import math

import pytest

from grainline.core.geometry.assemble import assign_hierarchy, rings_to_shapes
from grainline.core.geometry.validate import simplify_points

from tests.conftest import regular_polygon

pytestmark = pytest.mark.free


def _square(size: float, offset: tuple[float, float] = (0.0, 0.0)) -> list:
    ox, oy = offset
    return [
        (ox, oy),
        (ox + size, oy),
        (ox + size, oy + size),
        (ox, oy + size),
    ]


def test_two_concentric_rings_become_one_shape_with_a_hole():
    shapes = rings_to_shapes(
        [regular_polygon(48, 40.0), regular_polygon(48, 20.0)]
    )
    assert len(shapes) == 1
    assert len(shapes[0].holes) == 1
    expected = math.pi * (40.0**2 - 20.0**2)
    assert shapes[0].area == pytest.approx(expected, rel=1e-2)


def test_disjoint_rings_become_separate_shapes():
    shapes = rings_to_shapes([_square(20.0), _square(20.0, (100.0, 0.0))])
    assert len(shapes) == 2
    assert all(not s.holes for s in shapes)


def test_depth_two_ring_is_a_solid_island_not_a_hole():
    """A part sitting inside a window cut-out is material, not a hole."""
    shapes = rings_to_shapes(
        [
            regular_polygon(48, 100.0),  # depth 0 - solid
            regular_polygon(48, 60.0),   # depth 1 - hole
            regular_polygon(48, 25.0),   # depth 2 - solid island
        ]
    )
    assert len(shapes) == 2
    frame = max(shapes, key=lambda s: s.gross_area)
    island = min(shapes, key=lambda s: s.gross_area)
    assert len(frame.holes) == 1
    assert not island.holes


def test_letter_b_gets_two_holes():
    outer = _square(100.0)
    hole_a = _square(20.0, (20.0, 20.0))
    hole_b = _square(20.0, (20.0, 60.0))
    shapes = rings_to_shapes([outer, hole_a, hole_b])
    assert len(shapes) == 1
    assert len(shapes[0].holes) == 2
    assert shapes[0].area == pytest.approx(100.0 * 100.0 - 2 * 400.0)


def test_min_area_filters_noise_rings():
    shapes = rings_to_shapes([_square(100.0), _square(0.2, (500.0, 500.0))], min_area=1.0)
    assert len(shapes) == 1


def test_min_hole_area_ignores_tiny_holes():
    outer = _square(100.0)
    tiny = _square(0.5, (10.0, 10.0))
    shapes = rings_to_shapes([outer, tiny], min_hole_area=1.0)
    assert len(shapes) == 1
    assert not shapes[0].holes


def test_shapes_are_returned_largest_first():
    shapes = rings_to_shapes(
        [_square(10.0), _square(50.0, (200.0, 0.0)), _square(30.0, (400.0, 0.0))]
    )
    areas = [s.area for s in shapes]
    assert areas == sorted(areas, reverse=True)


def test_self_intersecting_ring_is_repaired_into_pieces():
    bowtie = [(0.0, 0.0), (100.0, 100.0), (100.0, 0.0), (0.0, 100.0)]
    shapes = rings_to_shapes([bowtie], min_area=1.0)
    assert len(shapes) == 2
    assert all(s.polygon.is_valid for s in shapes)


def test_assign_hierarchy_returns_immediate_parent():
    from shapely.geometry import Polygon

    big = Polygon([*_square(100.0), _square(100.0)[0]])
    mid = Polygon([*_square(60.0, (20.0, 20.0)), _square(60.0, (20.0, 20.0))[0]])
    small = Polygon([*_square(20.0, (40.0, 40.0)), _square(20.0, (40.0, 40.0))[0]])

    parents = assign_hierarchy([big, mid, small])
    assert parents[0] == -1
    assert parents[1] == 0
    assert parents[2] == 1  # immediate parent, not the outermost


def test_empty_input_yields_no_shapes():
    assert rings_to_shapes([]) == []


def test_simplify_tolerance_reduces_vertex_count():
    dense = regular_polygon(720, 100.0)
    detailed = rings_to_shapes([dense], simplify_tolerance=0.0)
    coarse = rings_to_shapes([dense], simplify_tolerance=0.5)
    assert len(coarse[0].outer) < len(detailed[0].outer)


def test_simplify_respects_its_deviation_guarantee():
    """Douglas-Peucker bounds *deviation*, not area - assert the real contract.

    An area-based assertion passes even when a simplification shifts an edge
    wildly as long as the losses happen to balance. Hausdorff distance is the
    property the algorithm actually promises, and the property that matters to a
    machinist: no point of the cut path strays further than the tolerance.
    """
    dense = regular_polygon(720, 100.0)
    detailed = rings_to_shapes([dense], simplify_tolerance=0.0)[0]
    tolerance = 0.5
    coarse = rings_to_shapes([dense], simplify_tolerance=tolerance)[0]

    deviation = coarse.polygon.hausdorff_distance(detailed.polygon)
    assert deviation <= tolerance * 1.5

    # Simplification only ever removes material from a convex ring, and the loss
    # must stay small relative to the part.
    assert coarse.area <= detailed.area
    assert coarse.area == pytest.approx(detailed.area, rel=1e-2)


def test_simplification_error_shrinks_as_tolerance_tightens():
    dense = regular_polygon(720, 100.0)
    reference = rings_to_shapes([dense], simplify_tolerance=0.0)[0]
    loose = rings_to_shapes([dense], simplify_tolerance=1.0)[0]
    tight = rings_to_shapes([dense], simplify_tolerance=0.05)[0]

    assert tight.polygon.hausdorff_distance(reference.polygon) < loose.polygon.hausdorff_distance(
        reference.polygon
    )


def test_simplify_never_destroys_a_ring():
    """Aggressive tolerance must back off rather than return a degenerate ring."""
    pts = regular_polygon(8, 5.0)
    result = simplify_points(pts, tolerance=1000.0, closed=True)
    assert len(result) >= 3

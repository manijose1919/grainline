"""Tests for Contour and Shape construction, measurement and invariants."""

from __future__ import annotations

import math

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from grainline.core.geometry.primitives import (
    AREA_TOL,
    Contour,
    DegenerateGeometryError,
    Shape,
    signed_area,
)

from tests.conftest import convex_shapes, l_bracket, regular_polygon, washer

pytestmark = pytest.mark.free


# --- Contour ---------------------------------------------------------------


def test_contour_strips_repeated_closing_point():
    c = Contour.of([(0, 0), (10, 0), (10, 10), (0, 10), (0, 0)])
    assert len(c) == 4
    assert c.points[0] == (0.0, 0.0)


def test_contour_strips_interior_duplicates():
    c = Contour.of([(0, 0), (0, 0), (10, 0), (10, 0), (10, 10)])
    assert len(c) == 3


def test_contour_rejects_too_few_points():
    with pytest.raises(DegenerateGeometryError, match="at least 3"):
        Contour.of([(0, 0), (1, 1)])


def test_contour_rejects_collinear_points():
    with pytest.raises(DegenerateGeometryError, match="collinear"):
        Contour.of([(0, 0), (5, 0), (10, 0)])


def test_signed_area_sign_follows_winding():
    ccw = [(0, 0), (10, 0), (10, 10), (0, 10)]
    assert signed_area(ccw) == pytest.approx(100.0)
    assert signed_area(list(reversed(ccw))) == pytest.approx(-100.0)


def test_contour_orientation_is_idempotent():
    c = Contour.of(list(reversed(regular_polygon(6, 20.0))))
    once = c.oriented(ccw=True)
    twice = once.oriented(ccw=True)
    assert once.is_ccw
    assert once.points == twice.points
    # Already-correct orientation must not allocate a new object.
    assert once.oriented(ccw=True) is once


def test_contour_perimeter_of_square():
    c = Contour.of([(0, 0), (10, 0), (10, 10), (0, 10)])
    assert c.perimeter == pytest.approx(40.0)


def test_contour_bounds():
    c = Contour.of([(-5, 2), (7, -3), (1, 11)])
    assert c.bounds == (-5.0, -3.0, 7.0, 11.0)


# --- Shape -----------------------------------------------------------------


def test_shape_normalises_winding_on_construction():
    outer = list(reversed(regular_polygon(8, 30.0)))  # clockwise
    hole = regular_polygon(8, 10.0)  # counter-clockwise
    s = Shape.of(outer, [hole])
    assert s.outer.is_ccw
    assert not s.holes[0].is_ccw


def test_shape_area_subtracts_holes():
    s = washer(outer_r=40.0, inner_r=20.0, segments=256)
    expected = math.pi * (40.0**2 - 20.0**2)
    # Polygonal approximation of a circle under-reports; 256 segments is within 0.01%.
    assert s.area == pytest.approx(expected, rel=1e-3)
    assert s.gross_area > s.area


def test_symmetric_bowtie_is_rejected_at_the_contour_stage():
    """A symmetric bow-tie's lobes cancel, so it has zero *signed* area.

    It never reaches the Shapely validity check; the contour guard catches it
    first. The error message must therefore name self-intersection as a cause.
    """
    bowtie = [(0, 0), (10, 10), (10, 0), (0, 10)]
    with pytest.raises(DegenerateGeometryError, match="self-intersects"):
        Shape.of(bowtie)


def test_asymmetric_self_intersection_is_rejected_by_polygon_validity():
    """An unbalanced bow-tie has non-zero signed area and must be caught later."""
    bowtie = [(0.0, 0.0), (10.0, 10.0), (10.0, 0.0), (0.0, 30.0)]
    with pytest.raises(DegenerateGeometryError, match="invalid polygon"):
        Shape.of(bowtie)


def test_shape_rejects_nonpositive_rectangle():
    with pytest.raises(DegenerateGeometryError, match="positive dimensions"):
        Shape.rectangle(0.0, 10.0)


def test_shape_utilisation_flags_wasteful_parts():
    square = Shape.rectangle(50.0, 50.0)
    bracket = l_bracket(100.0, 60.0, 20.0)
    assert square.utilisation == pytest.approx(1.0)
    # L-bracket: 100*20 + 20*40 = 2800 mm2 inside a 100x60 = 6000 mm2 box.
    assert bracket.utilisation == pytest.approx(2800.0 / 6000.0)
    assert bracket.utilisation < 0.5


def test_shape_dimensions():
    s = l_bracket(100.0, 60.0, 20.0)
    assert s.width == pytest.approx(100.0)
    assert s.height == pytest.approx(60.0)
    assert s.bbox_area == pytest.approx(6000.0)


def test_shape_polygon_is_cached():
    s = Shape.rectangle(10.0, 10.0)
    assert s.polygon is s.polygon


def test_from_shapely_roundtrip_preserves_holes():
    original = washer(30.0, 12.0, 32)
    rebuilt = Shape.from_shapely(original.polygon)
    assert len(rebuilt.holes) == 1
    assert rebuilt.area == pytest.approx(original.area)


# --- Properties ------------------------------------------------------------


@settings(max_examples=120, deadline=None)
@given(convex_shapes())
def test_property_area_is_positive_and_bounded_by_bbox(shape: Shape):
    assert shape.area > 0.0
    # A shape can never contain more material than its own bounding box.
    assert shape.area <= shape.bbox_area + 1e-6


@settings(max_examples=120, deadline=None)
@given(convex_shapes())
def test_property_outer_is_always_ccw(shape: Shape):
    assert shape.outer.is_ccw
    assert shape.utilisation <= 1.0 + 1e-9


@settings(max_examples=120, deadline=None)
@given(convex_shapes())
def test_property_shapely_polygon_stays_valid(shape: Shape):
    assert shape.polygon.is_valid
    assert shape.polygon.area == pytest.approx(shape.area, rel=1e-6, abs=AREA_TOL)


@settings(max_examples=80, deadline=None)
@given(st.integers(min_value=3, max_value=64), st.floats(1.0, 200.0))
def test_property_regular_polygon_area_matches_formula(n: int, radius: float):
    pts = regular_polygon(n, radius)
    expected = 0.5 * n * radius * radius * math.sin(2.0 * math.pi / n)
    assert abs(signed_area(pts)) == pytest.approx(expected, rel=1e-9)

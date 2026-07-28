"""Tests for rigid transforms, including area/orientation preservation."""

from __future__ import annotations

import math

import pytest
from hypothesis import given, settings

from grainline.core.geometry.primitives import Shape
from grainline.core.geometry.transform import (
    IDENTITY,
    Transform,
    normalise_angle,
)

from tests.conftest import convex_shapes, l_bracket, transforms, washer

pytestmark = pytest.mark.free


def test_identity_changes_nothing():
    s = l_bracket()
    out = IDENTITY.apply(s)
    assert IDENTITY.is_identity
    assert out.outer.points == s.outer.points


def test_translation_moves_bounds_exactly():
    s = Shape.rectangle(10.0, 20.0)
    moved = Transform.translation(5.0, -3.0).apply(s)
    assert moved.bounds == pytest.approx((5.0, -3.0, 15.0, 17.0))


def test_rotation_90_swaps_extents():
    s = Shape.rectangle(30.0, 10.0)
    rotated = Transform.rotation_only(90.0).apply(s)
    assert rotated.width == pytest.approx(10.0)
    assert rotated.height == pytest.approx(30.0)


def test_mirror_preserves_winding_direction():
    s = l_bracket()
    mirrored = Transform(mirror_x=True).apply(s)
    # Reflection flips signed area; apply_contour must re-reverse to compensate.
    assert mirrored.outer.is_ccw
    assert mirrored.area == pytest.approx(s.area)


def test_mirror_preserves_hole_winding():
    s = washer(30.0, 10.0, 32)
    mirrored = Transform(mirror_x=True).apply(s)
    assert mirrored.outer.is_ccw
    assert not mirrored.holes[0].is_ccw
    assert mirrored.area == pytest.approx(s.area)


def test_offset_by_accumulates():
    t = Transform.rotation_only(45.0).offset_by(10.0, 0.0).offset_by(0.0, 5.0)
    assert t.rotation == pytest.approx(45.0)
    assert (t.tx, t.ty) == pytest.approx((10.0, 5.0))


def test_normalise_angle_folds_multiples_of_360():
    assert normalise_angle(720.0) == pytest.approx(0.0)
    assert normalise_angle(-90.0) == pytest.approx(270.0)
    assert normalise_angle(45.0) == pytest.approx(45.0)


def test_apply_points_matches_apply_point_elementwise():
    t = Transform(37.0, 4.0, -9.0, mirror_x=True)
    pts = [(0.0, 0.0), (10.0, 0.0), (10.0, 5.0), (-3.0, 8.0)]
    batch = t.apply_points(pts)
    for src, got in zip(pts, batch):
        assert got == pytest.approx(t.apply_point(src))


# --- Composition -----------------------------------------------------------


@settings(max_examples=200, deadline=None)
@given(transforms(), transforms())
def test_property_then_matches_sequential_application(a: Transform, b: Transform):
    """``a.then(b)`` must equal applying ``a`` and then ``b``."""
    probe = (13.0, -7.0)
    sequential = b.apply_point(a.apply_point(probe))
    composed = a.then(b).apply_point(probe)
    assert composed == pytest.approx(sequential, abs=1e-6)


def test_two_mirrors_cancel():
    m = Transform(mirror_x=True)
    combined = m.then(m)
    assert not combined.mirror_x
    assert combined.apply_point((3.0, 4.0)) == pytest.approx((3.0, 4.0))


# --- Invariants ------------------------------------------------------------


@settings(max_examples=150, deadline=None)
@given(convex_shapes(), transforms())
def test_property_rigid_transform_preserves_area(shape: Shape, t: Transform):
    """Rigid motions are isometries: area is invariant."""
    moved = t.apply(shape)
    assert moved.area == pytest.approx(shape.area, rel=1e-6)


@settings(max_examples=150, deadline=None)
@given(convex_shapes(), transforms())
def test_property_rigid_transform_preserves_winding(shape: Shape, t: Transform):
    moved = t.apply(shape)
    assert moved.outer.is_ccw


@settings(max_examples=150, deadline=None)
@given(convex_shapes(), transforms())
def test_property_rigid_transform_preserves_perimeter(shape: Shape, t: Transform):
    moved = t.apply(shape)
    assert moved.outer.perimeter == pytest.approx(shape.outer.perimeter, rel=1e-6)


@settings(max_examples=100, deadline=None)
@given(convex_shapes())
def test_property_full_turn_returns_original(shape: Shape):
    turned = Transform.rotation_only(360.0).apply(shape)
    assert turned.bounds == pytest.approx(shape.bounds, abs=1e-6)

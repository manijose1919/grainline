"""Shared fixtures and Hypothesis strategies for the GRAINLINE test suite."""

from __future__ import annotations

import math
from pathlib import Path

import pytest
from hypothesis import strategies as st

from grainline.core.geometry.primitives import Contour, Point, Shape

# ---------------------------------------------------------------------------
# Deterministic geometry fixtures
# ---------------------------------------------------------------------------


def regular_polygon(
    n: int, radius: float, *, centre: Point = (0.0, 0.0), phase: float = 0.0
) -> list[Point]:
    """Vertices of a regular ``n``-gon, counter-clockwise."""
    cx, cy = centre
    return [
        (
            cx + radius * math.cos(phase + 2.0 * math.pi * i / n),
            cy + radius * math.sin(phase + 2.0 * math.pi * i / n),
        )
        for i in range(n)
    ]


def l_bracket(width: float = 100.0, height: float = 60.0, thickness: float = 20.0) -> Shape:
    """A concave L-shaped bracket - the canonical case rectangular nesting wastes."""
    return Shape.of(
        [
            (0.0, 0.0),
            (width, 0.0),
            (width, thickness),
            (thickness, thickness),
            (thickness, height),
            (0.0, height),
        ]
    )


def washer(outer_r: float = 40.0, inner_r: float = 18.0, segments: int = 64) -> Shape:
    """An annulus: outer circle with a concentric hole."""
    return Shape.of(
        regular_polygon(segments, outer_r),
        [regular_polygon(segments, inner_r)],
    )


@pytest.fixture
def bracket() -> Shape:
    return l_bracket()


@pytest.fixture
def ring() -> Shape:
    return washer()


@pytest.fixture
def unit_square() -> Shape:
    return Shape.rectangle(10.0, 10.0)


@pytest.fixture
def tmp_cad_dir(tmp_path: Path) -> Path:
    d = tmp_path / "cad"
    d.mkdir()
    return d


# ---------------------------------------------------------------------------
# Hypothesis strategies
# ---------------------------------------------------------------------------

#: Coordinates are bounded and rounded so that generated geometry stays in a
#: numerically sane range. Unbounded floats produce polygons whose area
#: underflows, which tests the float spec rather than our code.
coordinate = st.floats(
    min_value=-500.0, max_value=500.0, allow_nan=False, allow_infinity=False, width=32
)


@st.composite
def convex_shapes(draw, min_points: int = 3, max_points: int = 12) -> Shape:
    """Generate a random *convex* shape via the convex hull of random points.

    Taking a hull guarantees simplicity (no self-intersection), so these are
    valid inputs by construction. That is what we want for invariant tests:
    a failure means our transform or measurement code is wrong, not that
    Hypothesis handed us a bow-tie.
    """
    from shapely.geometry import MultiPoint

    n = draw(st.integers(min_value=min_points, max_value=max_points))
    pts = draw(
        st.lists(
            st.tuples(coordinate, coordinate),
            min_size=n,
            max_size=n,
        )
    )
    hull = MultiPoint(pts).convex_hull
    if hull.geom_type != "Polygon" or hull.area < 1.0:
        # Degenerate draw (collinear or tiny); fall back to a known-good shape
        # rather than rejecting, which keeps the example budget productive.
        return Shape.rectangle(10.0, 10.0)
    return Shape.from_shapely(hull)


@st.composite
def transforms(draw):
    """Generate a random rigid transform."""
    from grainline.core.geometry.transform import Transform

    return Transform(
        rotation=draw(st.floats(min_value=0.0, max_value=360.0, allow_nan=False)),
        tx=draw(coordinate),
        ty=draw(coordinate),
        mirror_x=draw(st.booleans()),
    )

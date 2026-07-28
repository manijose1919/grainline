"""Reassemble closed loops from disconnected CAD segments.

Real production DXF files are rarely made of tidy closed polylines. A part
outline that draws correctly on screen is very often several hundred separate
``LINE`` and ``ARC`` entities whose endpoints coincide only to within CAD
rounding. Nothing downstream can nest such a file: there are no polygons in it.

This module rebuilds them. Endpoints are welded into shared nodes using a
spatial hash with a real distance test (not naive coordinate rounding, which
splits points that straddle a grid boundary), then chains are walked end to end
until they close.

Where a node has more than two incident chains — a T-junction, common when a
part shares an edge with a construction line — the walk prefers the chain with
the smallest turn angle. That "straightest continuation" heuristic reflects how
a draughtsman actually drew the outline, but it is only a *preference*: a spur
leaving a corner can easily be straighter than the true next edge. Committing to
it greedily silently destroys the loop and loses the part.

The walk is therefore a depth-first search with backtracking. Branches are tried
straightest-first, and a dead end unwinds and takes the next candidate. An
explicit expansion budget bounds the search so a pathological file degrades to a
reported failure rather than an unbounded hang.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable, Sequence

from .primitives import GEOM_TOL, Point

__all__ = ["Polyline", "StitchResult", "stitch_loops", "DEFAULT_WELD_TOL"]

#: Default endpoint welding tolerance in millimetres. Chosen well below machine
#: precision but comfortably above the rounding error of a CAD export written
#: with six decimal places.
DEFAULT_WELD_TOL: float = 1e-3

#: Backtracking search budget. A clean file closes each loop in roughly one
#: expansion per chain; the multiplier buys room to unwind past spurs and
#: T-junctions, and the constant keeps small files from being budget-starved.
#: Exceeding the budget reports an unclosed run rather than hanging.
_EXPANSION_BUDGET_BASE: int = 4096
_EXPANSION_BUDGET_PER_CHAIN: int = 8


@dataclass(slots=True)
class Polyline:
    """An open or closed run of points recovered from a CAD entity."""

    points: list[Point]
    closed: bool = False
    #: Source entity label, kept for diagnostics in import reports.
    source: str = ""

    @property
    def start(self) -> Point:
        return self.points[0]

    @property
    def end(self) -> Point:
        return self.points[-1]

    def __len__(self) -> int:
        return len(self.points)


@dataclass(slots=True)
class StitchResult:
    """Outcome of a stitch pass, including what could not be closed.

    ``open_runs`` is deliberately surfaced rather than silently dropped: an
    import that quietly discards half a part is far worse than one that reports
    "12 segments could not be closed into a loop".
    """

    rings: list[list[Point]] = field(default_factory=list)
    open_runs: list[list[Point]] = field(default_factory=list)

    @property
    def closed_count(self) -> int:
        return len(self.rings)

    @property
    def open_count(self) -> int:
        return len(self.open_runs)


class _NodeIndex:
    """Spatial hash that welds nearby endpoints into shared node ids.

    Bucketing alone is not enough: two points 1e-9 apart can land either side of
    a cell boundary. Every lookup therefore scans the 3x3 neighbourhood and
    applies a true distance test, so welding is correct regardless of where the
    grid happens to fall.
    """

    __slots__ = ("_tol", "_cell", "_cells", "_positions")

    def __init__(self, tol: float) -> None:
        self._tol = tol
        # Cell size equal to the tolerance guarantees any point within tol lies
        # in the 3x3 neighbourhood of the query cell.
        self._cell = max(tol, GEOM_TOL)
        self._cells: dict[tuple[int, int], list[int]] = {}
        self._positions: list[Point] = []

    def _key(self, p: Point) -> tuple[int, int]:
        return (int(math.floor(p[0] / self._cell)), int(math.floor(p[1] / self._cell)))

    def get_or_create(self, p: Point) -> int:
        cx, cy = self._key(p)
        best_id, best_dist = -1, self._tol
        for i in range(cx - 1, cx + 2):
            for j in range(cy - 1, cy + 2):
                for nid in self._cells.get((i, j), ()):
                    d = math.dist(self._positions[nid], p)
                    if d <= best_dist:
                        best_id, best_dist = nid, d
        if best_id >= 0:
            return best_id

        nid = len(self._positions)
        self._positions.append(p)
        self._cells.setdefault((cx, cy), []).append(nid)
        return nid

    def position(self, node_id: int) -> Point:
        return self._positions[node_id]


def _direction(a: Point, b: Point) -> tuple[float, float]:
    """Unit vector from ``a`` to ``b``; zero vector if the points coincide."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = math.hypot(dx, dy)
    if length <= GEOM_TOL:
        return (0.0, 0.0)
    return (dx / length, dy / length)


def _turn_cost(incoming: tuple[float, float], outgoing: tuple[float, float]) -> float:
    """Cost in ``[0, 2]`` for turning from one direction to another.

    Zero means dead straight, two means a full reversal. Implemented as
    ``1 - dot`` so it needs no ``acos`` — the ordering is all that matters and
    the transform is monotonic.
    """
    dot = incoming[0] * outgoing[0] + incoming[1] * outgoing[1]
    return 1.0 - max(-1.0, min(1.0, dot))


def stitch_loops(
    polylines: Iterable[Polyline],
    *,
    tolerance: float = DEFAULT_WELD_TOL,
) -> StitchResult:
    """Weld open polylines into closed rings.

    Already-closed polylines pass straight through. Open runs are welded at
    their endpoints and walked until they return to their starting node.

    Args:
        polylines: Runs recovered from CAD entities.
        tolerance: Endpoint welding distance in mm.

    Returns:
        A :class:`StitchResult` holding closed rings (without a repeated closing
        vertex) and any runs that could not be closed.
    """
    result = StitchResult()
    open_chains: list[list[Point]] = []

    for pl in polylines:
        pts = list(pl.points)
        if len(pts) < 2:
            continue
        if pl.closed:
            ring = pts[:-1] if math.dist(pts[0], pts[-1]) <= tolerance else pts
            if len(ring) >= 3:
                result.rings.append(ring)
            continue
        # An "open" run whose ends already meet is really a closed ring.
        if len(pts) >= 4 and math.dist(pts[0], pts[-1]) <= tolerance:
            result.rings.append(pts[:-1])
            continue
        open_chains.append(pts)

    if not open_chains:
        return result

    index = _NodeIndex(tolerance)
    # ends[i] = (start_node, end_node) for chain i
    ends: list[tuple[int, int]] = []
    for chain in open_chains:
        ends.append((index.get_or_create(chain[0]), index.get_or_create(chain[-1])))

    # node -> list of (chain_index, end_flag) where end_flag 0=start, 1=end
    incident: dict[int, list[tuple[int, int]]] = {}
    for i, (a, b) in enumerate(ends):
        incident.setdefault(a, []).append((i, 0))
        incident.setdefault(b, []).append((i, 1))

    consumed = [False] * len(open_chains)
    budget = _EXPANSION_BUDGET_BASE + _EXPANSION_BUDGET_PER_CHAIN * len(open_chains)

    def _oriented_points(chain_idx: int, end_flag: int) -> list[Point]:
        """Chain points running away from the node it was entered at."""
        pts = open_chains[chain_idx]
        return pts if end_flag == 0 else pts[::-1]

    def _candidates(
        node: int, incoming_dir: tuple[float, float]
    ) -> list[tuple[int, int]]:
        """Chains leaving ``node``, ordered straightest continuation first.

        Globally consumed chains are excluded here; chains taken earlier in the
        *current* walk are filtered at selection time, because backtracking can
        release them again.
        """
        scored: list[tuple[float, int, int]] = []
        for chain_idx, end_flag in incident.get(node, ()):
            if consumed[chain_idx]:
                continue
            pts = open_chains[chain_idx]
            outgoing = (
                _direction(pts[0], pts[1])
                if end_flag == 0
                else _direction(pts[-1], pts[-2])
            )
            scored.append((_turn_cost(incoming_dir, outgoing), chain_idx, end_flag))
        scored.sort()
        return [(c, e) for _, c, e in scored]

    def _find_loop(seed: int) -> list[tuple[int, int]] | None:
        """Depth-first search for a closed walk starting at ``seed``.

        Returns the trail as ``(chain_index, end_flag)`` pairs, or ``None`` when
        no closure exists within the expansion budget.
        """
        start_node, node = ends[seed]
        seed_pts = open_chains[seed]
        incoming = _direction(seed_pts[-2], seed_pts[-1])

        in_walk: set[int] = {seed}
        trail: list[tuple[int, int]] = [(seed, 0)]
        # Frame layout: [node, incoming_dir, candidate_list, cursor, taken_or_None]
        frames: list[list] = []
        expansions = 0

        while True:
            if node == start_node:
                return trail
            if expansions >= budget:
                return None

            frames.append([node, incoming, _candidates(node, incoming), 0, None])

            advanced = False
            while frames:
                frame = frames[-1]
                taken = frame[4]
                if taken is not None:
                    # Release the choice made at this frame before trying the next.
                    in_walk.discard(taken[0])
                    trail.pop()
                    frame[4] = None

                options: list[tuple[int, int]] = frame[2]
                cursor: int = frame[3]
                if cursor >= len(options):
                    frames.pop()
                    continue

                frame[3] = cursor + 1
                chain_idx, end_flag = options[cursor]
                if chain_idx in in_walk:
                    continue

                in_walk.add(chain_idx)
                trail.append((chain_idx, end_flag))
                frame[4] = (chain_idx, end_flag)
                expansions += 1

                node = ends[chain_idx][1 - end_flag]
                seg = _oriented_points(chain_idx, end_flag)
                incoming = _direction(seg[-2], seg[-1])
                advanced = True
                break

            if not advanced:
                return None

    def _trail_to_points(trail: list[tuple[int, int]]) -> list[Point]:
        """Concatenate a trail into a point run, dropping shared weld vertices."""
        points: list[Point] = list(_oriented_points(*trail[0]))
        for chain_idx, end_flag in trail[1:]:
            points.extend(_oriented_points(chain_idx, end_flag)[1:])
        return points

    for seed in range(len(open_chains)):
        if consumed[seed]:
            continue

        trail = _find_loop(seed)
        if trail is not None:
            for chain_idx, _ in trail:
                consumed[chain_idx] = True
            path = _trail_to_points(trail)
            if len(path) >= 4:
                # The walk returned to its origin; drop the duplicated closing point.
                result.rings.append(path[:-1])
                continue
            result.open_runs.append(path)
            continue

        # No closure from this seed. Consume only the seed itself so its
        # neighbours remain available to close a different loop.
        consumed[seed] = True
        result.open_runs.append(list(open_chains[seed]))

    return result

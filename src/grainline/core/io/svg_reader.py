"""Read part geometry out of SVG files.

Sign shops and laser cutters live in Illustrator and Inkscape, so SVG is at
least as common an input as DXF. The awkward part of SVG is not the path data
but the coordinate system: nested ``transform`` attributes, a ``viewBox`` that
rescales everything, and physical units on the root element that may or may not
agree with the internal numbers.

``svgelements`` resolves the whole transform stack for us and normalises to CSS
pixels at a known PPI, which reduces the problem to a single final scale factor
of 25.4/96 mm per pixel. Everything else here is curve flattening.
"""

from __future__ import annotations

import math
from pathlib import Path as FilePath
from typing import Iterator

from ..geometry.assemble import rings_to_shapes
from ..geometry.primitives import Point
from ..geometry.stitch import Polyline, stitch_loops
from .base import (
    ImportOptions,
    ImportReport,
    ImportedGeometry,
    ImportError_,
    resolve_unit_scale,
)

__all__ = ["read_svg", "read_svg_stream", "SVG_PPI", "UnsafeSvgError"]


class UnsafeSvgError(ImportError_):
    """Raised when an SVG declares XML entities or an external DTD."""


#: Maximum SVG size accepted, in bytes. A 64 MB path file is not a real part;
#: it is either a mistake or an attempt to exhaust memory during parsing.
_MAX_SVG_BYTES: int = 64 * 1024 * 1024

#: Byte markers for XML constructs that have no legitimate use in a CAD export
#: and are the entry point for both XXE file disclosure and the "billion
#: laughs" entity-expansion denial of service.
_UNSAFE_MARKERS: tuple[bytes, ...] = (b"<!ENTITY", b"<!DOCTYPE")


def _reject_unsafe_xml(payload: bytes, source: str) -> None:
    """Refuse SVG containing entity or DTD declarations.

    ``svgelements`` parses with the standard library's ``xml.etree``, which
    resolves entities and has no expansion limit, and it gives us no way to
    substitute a hardened parser. Screening the bytes before they reach it is
    therefore the only place this can be stopped.

    Rejecting outright rather than stripping is deliberate: no CAD tool in this
    market emits entity declarations, so a file containing them is either
    corrupt or hostile, and in both cases the shop wants to know rather than to
    silently nest a file that was quietly rewritten.
    """
    if len(payload) > _MAX_SVG_BYTES:
        raise UnsafeSvgError(
            f"{source}: SVG is {len(payload) / 1e6:.1f} MB, above the "
            f"{_MAX_SVG_BYTES / 1e6:.0f} MB limit"
        )
    # Only the prolog can carry a DTD, and scanning the whole file for a marker
    # that may legitimately appear inside path data would cause false positives.
    head = payload[:8192].upper()
    for marker in _UNSAFE_MARKERS:
        if marker in head:
            raise UnsafeSvgError(
                f"{source}: SVG declares {marker.decode()} in its prolog. "
                f"Entity and DTD declarations are rejected because they enable "
                f"external-entity disclosure and entity-expansion attacks. "
                f"Re-export the file from your CAD tool."
            )

#: Pixels per inch used when interpreting the SVG viewport. 96 is the CSS
#: reference value and what both Illustrator and modern Inkscape emit.
SVG_PPI: float = 96.0

#: Hard ceiling on samples per curve segment. Protects against a pathological
#: path (a 40-metre spiral in a 3 mm part) generating millions of vertices.
_MAX_SAMPLES_PER_SEGMENT = 512


def _flatten_segment(segment, step: float) -> list[Point]:
    """Sample one path segment into points, excluding its start vertex.

    The start is omitted because the previous segment already contributed it;
    including it would seed a duplicate vertex at every joint and inflate the
    contour by 2x.
    """
    try:
        length = float(segment.length(error=step / 10.0))
    except (TypeError, AttributeError):
        try:
            length = float(segment.length())
        except Exception:  # noqa: BLE001 - degenerate segment
            length = 0.0

    if not math.isfinite(length) or length <= step:
        end = segment.end
        return [(float(end.x), float(end.y))]

    count = min(_MAX_SAMPLES_PER_SEGMENT, max(2, int(math.ceil(length / step))))
    pts: list[Point] = []
    for i in range(1, count + 1):
        p = segment.point(i / count)
        pts.append((float(p.x), float(p.y)))
    return pts


def _subpath_to_points(subpath, step: float) -> tuple[list[Point], bool]:
    """Flatten an SVG subpath into a point run and report whether it closes."""
    from svgelements import Close, Line, Move

    points: list[Point] = []
    closed = False

    for segment in subpath:
        if isinstance(segment, Move):
            start = segment.end
            if start is not None:
                points.append((float(start.x), float(start.y)))
            continue
        if isinstance(segment, Close):
            closed = True
            continue
        if segment.start is not None and not points:
            points.append((float(segment.start.x), float(segment.start.y)))
        if isinstance(segment, Line):
            end = segment.end
            points.append((float(end.x), float(end.y)))
        else:
            points.extend(_flatten_segment(segment, step))

    return points, closed


def _iter_svg_shapes(svg) -> Iterator:
    """Yield every renderable shape element, transforms already applied."""
    from svgelements import Shape as SvgShape

    for element in svg.elements():
        if isinstance(element, SvgShape):
            yield element


def _build(svg, options: ImportOptions, report: ImportReport) -> ImportedGeometry:
    """Shared pipeline once an SVG document is parsed."""
    from svgelements import Path as SvgPath

    unit = options.unit_override or "px"
    canonical, scale = resolve_unit_scale(unit, default="px")
    report.unit = canonical
    report.scale_to_mm = scale

    # Sample in source units so the post-scale chord error equals the request.
    step = max(options.chord_tolerance * 8.0 / scale, 1e-9)

    polylines: list[Polyline] = []
    for element in _iter_svg_shapes(svg):
        type_name = type(element).__name__

        label = ""
        values = getattr(element, "values", None)
        if isinstance(values, dict):
            label = str(values.get("inkscape:label") or values.get("id") or "")
        layer = label or type_name
        report.layers_seen.add(layer)

        if not options.layer_allowed(layer):
            report.entities_skipped[f"layer:{layer}"] += 1
            continue

        try:
            path = SvgPath(element)
        except Exception:  # noqa: BLE001 - unsupported element
            report.entities_skipped[type_name] += 1
            continue

        subpaths = list(path.as_subpaths())
        if not subpaths:
            continue

        for subpath in subpaths:
            pts, closed = _subpath_to_points(subpath, step)
            if len(pts) < 2:
                continue
            report.entities_read += 1
            if scale != 1.0:
                pts = [(x * scale, y * scale) for x, y in pts]
            polylines.append(Polyline(points=pts, closed=closed, source=type_name))

    if not polylines:
        report.warn("no path geometry found in SVG")
        return ImportedGeometry([], report)

    stitched = stitch_loops(polylines, tolerance=options.weld_tolerance)
    report.rings_closed = stitched.closed_count
    report.runs_unclosed = stitched.open_count
    if stitched.open_runs:
        report.warn(
            f"{stitched.open_count} path run(s) were not closed; open strokes "
            f"cannot be nested and were excluded"
        )

    candidate_count = len(stitched.rings)
    shapes = rings_to_shapes(
        stitched.rings,
        min_area=options.min_area,
        min_hole_area=options.min_hole_area,
        simplify_tolerance=options.simplify_tolerance,
    )
    report.shapes_built = len(shapes)
    absorbed = sum(len(s.holes) for s in shapes)
    dropped = max(0, candidate_count - len(shapes) - absorbed)
    report.shapes_dropped_small = dropped
    if dropped:
        report.warn(
            f"{dropped} ring(s) discarded below min_area={options.min_area} mm2"
        )

    return ImportedGeometry(shapes, report)


def read_svg(
    path: str | FilePath, options: ImportOptions | None = None
) -> ImportedGeometry:
    """Read an SVG file into millimetre-space shapes.

    Args:
        path: Path to a ``.svg`` file.
        options: Import tunables; sensible defaults are used when omitted.

    Raises:
        ImportError_: The file is missing or cannot be parsed as SVG.
    """
    from svgelements import SVG

    opts = options or ImportOptions()
    file_path = FilePath(path)
    report = ImportReport(source=str(file_path))

    if not file_path.is_file():
        raise ImportError_(f"SVG file not found: {file_path}")

    _reject_unsafe_xml(file_path.read_bytes(), str(file_path))

    try:
        svg = SVG.parse(str(file_path), reify=True, ppi=SVG_PPI)
    except Exception as exc:  # noqa: BLE001 - svgelements raises broadly
        raise ImportError_(f"could not read SVG {file_path}: {exc}") from exc

    return _build(svg, opts, report)


def read_svg_stream(stream, options: ImportOptions | None = None) -> ImportedGeometry:
    """Read SVG content from an already-open stream (used by the web uploader).

    The stream is fully read into memory so the safety screen can run before any
    XML parser sees the bytes. Handing an unscreened stream straight to a parser
    would defeat the guard entirely.
    """
    import io

    from svgelements import SVG

    opts = options or ImportOptions()
    report = ImportReport(source="<stream>")

    payload = stream.read()
    if isinstance(payload, str):
        payload = payload.encode("utf-8")
    _reject_unsafe_xml(payload, "<stream>")

    try:
        svg = SVG.parse(io.BytesIO(payload), reify=True, ppi=SVG_PPI)
    except Exception as exc:  # noqa: BLE001
        raise ImportError_(f"could not parse SVG stream: {exc}") from exc

    return _build(svg, opts, report)

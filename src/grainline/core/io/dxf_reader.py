"""Read part geometry out of DXF files.

Rather than handling LINE, ARC, CIRCLE, ELLIPSE, LWPOLYLINE, POLYLINE and
SPLINE each by hand, this reader funnels every entity through
``ezdxf.path.make_path``, which produces a uniform curve representation, and
then flattens that to a chord tolerance. One code path, one place for the
tolerance to be honoured, and new entity types come along for free.

``INSERT`` (block reference) entities are expanded recursively. This matters
more than it sounds: shops build a library of standard brackets as blocks and
place them dozens of times, so a reader that ignores ``INSERT`` sees an empty
file for a large fraction of real jobs.
"""

from __future__ import annotations

from pathlib import Path as FilePath
from typing import Iterable, Iterator

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

__all__ = ["read_dxf", "read_dxf_stream", "INSUNITS_TO_NAME"]

#: DXF ``$INSUNITS`` header codes mapped to GRAINLINE unit names. Codes outside
#: this table (miles, angstroms, light years) are real but never describe a
#: sheet-goods part, so they fall back to the configured default with a warning.
INSUNITS_TO_NAME: dict[int, str] = {
    0: "",       # unitless - file declares nothing
    1: "in",
    2: "ft",
    4: "mm",
    5: "cm",
    6: "m",
    14: "cm",    # decimetre has no direct entry; handled explicitly below
}

#: Maximum recursion depth when expanding nested block references. Real files
#: nest two or three deep; anything past this is a malformed self-referential
#: block and would otherwise hang the importer.
_MAX_BLOCK_DEPTH = 12

#: Entity types that never carry cuttable outlines. Counted and reported, not
#: warned about, so the operator sees they were intentionally ignored.
_IGNORED_TYPES = frozenset(
    {
        "TEXT", "MTEXT", "ATTRIB", "ATTDEF", "DIMENSION", "LEADER", "MLEADER",
        "POINT", "HATCH", "IMAGE", "WIPEOUT", "VIEWPORT", "SOLID", "3DFACE",
        "TOLERANCE", "RAY", "XLINE", "MESH", "BODY", "REGION",
    }
)


def _dxf_unit_name(doc, options: ImportOptions, report: ImportReport) -> str:
    """Determine the source unit for a DXF document."""
    if options.unit_override:
        return options.unit_override

    try:
        code = int(doc.header.get("$INSUNITS", 0))
    except (AttributeError, TypeError, ValueError):
        code = 0

    if code == 14:  # decimetres
        report.warn("file is in decimetres; converted via centimetres")
        return "cm"

    name = INSUNITS_TO_NAME.get(code, "")
    if not name:
        report.warn(
            f"DXF declares no usable unit ($INSUNITS={code}); "
            f"assuming {options.default_unit}"
        )
        return options.default_unit
    return name


def _iter_geometry_entities(entities: Iterable, depth: int = 0) -> Iterator:
    """Yield drawable entities, expanding block references recursively."""
    for entity in entities:
        dxftype = entity.dxftype()
        if dxftype == "INSERT":
            if depth >= _MAX_BLOCK_DEPTH:
                continue
            try:
                yield from _iter_geometry_entities(
                    entity.virtual_entities(), depth + 1
                )
            except Exception:  # noqa: BLE001 - malformed block, skip it
                continue
        else:
            yield entity


def _entity_to_polyline(
    entity, sagitta: float, report: ImportReport
) -> Polyline | None:
    """Flatten one DXF entity into a point run, or ``None`` if it carries none."""
    from ezdxf.path import make_path

    try:
        path = make_path(entity)
    except (TypeError, ValueError, AttributeError):
        report.entities_skipped[entity.dxftype()] += 1
        return None

    if path is None or len(path) == 0:
        return None

    points: list[Point] = [(v.x, v.y) for v in path.flattening(sagitta)]
    if len(points) < 2:
        return None

    return Polyline(points=points, closed=bool(path.is_closed), source=entity.dxftype())


def _build(doc, options: ImportOptions, report: ImportReport) -> ImportedGeometry:
    """Shared pipeline once a document is open: flatten -> stitch -> assemble."""
    unit = _dxf_unit_name(doc, options, report)
    canonical, scale = resolve_unit_scale(unit, default=options.default_unit)
    report.unit = canonical
    report.scale_to_mm = scale

    # Flatten in *source* units so the resulting sagitta is exactly the
    # requested tolerance once scaled to millimetres.
    sagitta = max(options.chord_tolerance / scale, 1e-9)

    polylines: list[Polyline] = []
    for entity in _iter_geometry_entities(doc.modelspace()):
        dxftype = entity.dxftype()
        layer = getattr(entity.dxf, "layer", "0")
        report.layers_seen.add(layer)

        if dxftype in _IGNORED_TYPES:
            report.entities_skipped[dxftype] += 1
            continue
        if not options.layer_allowed(layer):
            report.entities_skipped[f"layer:{layer}"] += 1
            continue

        pl = _entity_to_polyline(entity, sagitta, report)
        if pl is None:
            continue

        report.entities_read += 1
        if scale != 1.0:
            pl.points = [(x * scale, y * scale) for x, y in pl.points]
        polylines.append(pl)

    if not polylines:
        report.warn("no cuttable geometry found (check layer filters and units)")
        return ImportedGeometry([], report)

    stitched = stitch_loops(polylines, tolerance=options.weld_tolerance)
    report.rings_closed = stitched.closed_count
    report.runs_unclosed = stitched.open_count
    if stitched.open_runs:
        report.warn(
            f"{stitched.open_count} segment run(s) could not be closed into a loop; "
            f"they were excluded - try raising weld_tolerance"
        )

    candidate_count = len(stitched.rings)
    shapes = rings_to_shapes(
        stitched.rings,
        min_area=options.min_area,
        min_hole_area=options.min_hole_area,
        simplify_tolerance=options.simplify_tolerance,
    )
    report.shapes_built = len(shapes)
    # Rings that vanished were either below min_area or absorbed as holes;
    # only report the genuinely discarded ones.
    absorbed = sum(len(s.holes) for s in shapes)
    dropped = max(0, candidate_count - len(shapes) - absorbed)
    report.shapes_dropped_small = dropped
    if dropped:
        report.warn(
            f"{dropped} ring(s) discarded below min_area={options.min_area} mm2"
        )

    return ImportedGeometry(shapes, report)


def read_dxf(
    path: str | FilePath, options: ImportOptions | None = None
) -> ImportedGeometry:
    """Read a DXF file into millimetre-space shapes.

    Args:
        path: Path to a ``.dxf`` file (any version ezdxf supports, R12 upward).
        options: Import tunables; sensible defaults are used when omitted.

    Raises:
        ImportError_: The file is missing, unreadable or not valid DXF.
    """
    import ezdxf
    from ezdxf import DXFError

    opts = options or ImportOptions()
    file_path = FilePath(path)
    report = ImportReport(source=str(file_path))

    if not file_path.is_file():
        raise ImportError_(f"DXF file not found: {file_path}")

    try:
        doc = ezdxf.readfile(str(file_path))
    except (DXFError, OSError, UnicodeDecodeError) as exc:
        # Recovery mode salvages files written by non-conforming exporters,
        # which is a large slice of what small shops actually produce.
        try:
            from ezdxf import recover

            doc, auditor = recover.readfile(str(file_path))
            if auditor.has_errors:
                report.warn(
                    f"file required recovery; {len(auditor.errors)} structural "
                    f"error(s) were repaired"
                )
        except Exception as recover_exc:  # noqa: BLE001
            raise ImportError_(
                f"could not read DXF {file_path}: {exc}"
            ) from recover_exc

    return _build(doc, opts, report)


def read_dxf_bytes(
    payload: bytes, options: ImportOptions | None = None, *, source: str = "<upload>"
) -> ImportedGeometry:
    """Read DXF content from raw bytes.

    This is the path the web uploader uses, and it takes *bytes* on purpose.
    DXF has no single encoding: files are commonly cp1252, may declare their own
    ``$DWGCODEPAGE``, and binary DXF exists. Decoding to text ourselves before
    handing it over would corrupt layer names on any file that is not UTF-8 —
    and a corrupted layer name means the operator's layer filter silently stops
    matching, so their dimension lines get nested as parts.

    ``ezdxf.recover.read`` consumes a binary stream, detects the encoding, and
    repairs structural damage in one pass, which is exactly right for content
    arriving from a browser.
    """
    import io

    from ezdxf import DXFError, recover

    opts = options or ImportOptions()
    report = ImportReport(source=source)

    try:
        doc, auditor = recover.read(io.BytesIO(payload))
    except (DXFError, OSError, UnicodeDecodeError) as exc:
        raise ImportError_(f"could not parse DXF upload: {exc}") from exc

    if auditor.has_errors:
        report.warn(
            f"file required recovery; {len(auditor.errors)} structural error(s) "
            f"were repaired"
        )

    return _build(doc, opts, report)


def read_dxf_stream(stream, options: ImportOptions | None = None) -> ImportedGeometry:
    """Read DXF content from an already-open stream, text or binary.

    Binary streams are routed through :func:`read_dxf_bytes` so they get proper
    encoding detection; text streams are read directly for callers that have
    already decoded.
    """
    import ezdxf
    from ezdxf import DXFError

    opts = options or ImportOptions()
    report = ImportReport(source="<stream>")

    data = stream.read()
    if isinstance(data, bytes):
        return read_dxf_bytes(data, opts, source="<stream>")

    import io

    try:
        doc = ezdxf.read(io.StringIO(data))
    except (DXFError, UnicodeDecodeError) as exc:
        raise ImportError_(f"could not parse DXF stream: {exc}") from exc

    return _build(doc, opts, report)

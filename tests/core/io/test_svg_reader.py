"""Tests for the SVG importer, including viewBox and unit resolution."""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from grainline.core.io.base import ImportError_, ImportOptions
from grainline.core.io.reader import read_geometry
from grainline.core.io.svg_reader import read_svg

pytestmark = pytest.mark.free

PX_TO_MM = 25.4 / 96.0


def _write_svg(directory: Path, name: str, body: str, *, root_attrs: str = "") -> Path:
    attrs = root_attrs or 'width="400" height="400" viewBox="0 0 400 400"'
    content = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<svg xmlns="http://www.w3.org/2000/svg" {attrs}>\n{body}\n</svg>\n'
    )
    path = directory / name
    path.write_text(content, encoding="utf-8")
    return path


def test_reads_a_rectangle(tmp_cad_dir: Path):
    path = _write_svg(tmp_cad_dir, "rect.svg", '<rect x="0" y="0" width="96" height="48"/>')
    result = read_svg(path)
    assert len(result.shapes) == 1
    shape = result.shapes[0]
    assert shape.width == pytest.approx(96 * PX_TO_MM)
    assert shape.height == pytest.approx(48 * PX_TO_MM)
    assert result.report.unit == "px"


def test_reads_a_circle_within_tolerance(tmp_cad_dir: Path):
    path = _write_svg(tmp_cad_dir, "circle.svg", '<circle cx="200" cy="200" r="100"/>')
    result = read_svg(path, ImportOptions(chord_tolerance=0.01))
    expected = math.pi * (100 * PX_TO_MM) ** 2
    assert result.shapes[0].area == pytest.approx(expected, rel=1e-3)


def test_reads_explicit_polygon(tmp_cad_dir: Path):
    path = _write_svg(
        tmp_cad_dir, "poly.svg", '<polygon points="0,0 100,0 100,100 0,100"/>'
    )
    result = read_svg(path)
    assert result.shapes[0].area == pytest.approx((100 * PX_TO_MM) ** 2)


def test_reads_closed_path_with_curves(tmp_cad_dir: Path):
    body = '<path d="M 0,0 C 50,-40 150,-40 200,0 L 200,100 L 0,100 Z"/>'
    path = _write_svg(tmp_cad_dir, "curve.svg", body)
    result = read_svg(path, ImportOptions(chord_tolerance=0.02))
    assert len(result.shapes) == 1
    assert result.shapes[0].area > 0.0


def test_path_with_subpath_produces_a_hole(tmp_cad_dir: Path):
    """A single path with two subpaths is the standard way a hole is authored."""
    body = (
        '<path d="M 0,0 L 200,0 L 200,200 L 0,200 Z '
        'M 60,60 L 140,60 L 140,140 L 60,140 Z"/>'
    )
    path = _write_svg(tmp_cad_dir, "hole.svg", body)
    result = read_svg(path)
    assert len(result.shapes) == 1
    assert len(result.shapes[0].holes) == 1
    expected = ((200 * PX_TO_MM) ** 2) - ((80 * PX_TO_MM) ** 2)
    assert result.shapes[0].area == pytest.approx(expected)


def test_group_transforms_are_applied(tmp_cad_dir: Path):
    body = '<g transform="translate(100,50)"><rect x="0" y="0" width="96" height="96"/></g>'
    path = _write_svg(tmp_cad_dir, "transform.svg", body)
    result = read_svg(path)
    min_x, min_y, _, _ = result.shapes[0].bounds
    assert min_x == pytest.approx(100 * PX_TO_MM)
    assert min_y == pytest.approx(50 * PX_TO_MM)


def test_nested_transforms_compose(tmp_cad_dir: Path):
    body = (
        '<g transform="translate(50,0)">'
        '<g transform="scale(2)"><rect x="0" y="0" width="48" height="48"/></g>'
        "</g>"
    )
    path = _write_svg(tmp_cad_dir, "nested.svg", body)
    result = read_svg(path)
    shape = result.shapes[0]
    assert shape.width == pytest.approx(96 * PX_TO_MM)
    assert shape.bounds[0] == pytest.approx(50 * PX_TO_MM)


def test_physical_root_units_resolve_to_real_size(tmp_cad_dir: Path):
    """width="100mm" with a matching viewBox must import as exactly 100 mm."""
    path = _write_svg(
        tmp_cad_dir,
        "mm.svg",
        '<rect x="0" y="0" width="100" height="50"/>',
        root_attrs='width="100mm" height="100mm" viewBox="0 0 100 100"',
    )
    result = read_svg(path)
    shape = result.shapes[0]
    assert shape.width == pytest.approx(100.0, rel=1e-3)
    assert shape.height == pytest.approx(50.0, rel=1e-3)


def test_unit_override_reinterprets_coordinates(tmp_cad_dir: Path):
    path = _write_svg(tmp_cad_dir, "asmm.svg", '<rect x="0" y="0" width="10" height="10"/>')
    result = read_svg(path, ImportOptions(unit_override="mm"))
    assert result.report.unit == "mm"
    assert result.shapes[0].width == pytest.approx(10.0)


def test_multiple_disjoint_shapes(tmp_cad_dir: Path):
    body = (
        '<rect x="0" y="0" width="50" height="50"/>'
        '<rect x="200" y="0" width="50" height="50"/>'
        '<circle cx="100" cy="300" r="30"/>'
    )
    path = _write_svg(tmp_cad_dir, "many.svg", body)
    result = read_svg(path)
    assert len(result.shapes) == 3


def test_open_stroke_is_reported_not_silently_dropped(tmp_cad_dir: Path):
    body = (
        '<rect x="0" y="0" width="100" height="100"/>'
        '<path d="M 300,300 L 350,320"/>'
    )
    path = _write_svg(tmp_cad_dir, "openpath.svg", body)
    result = read_svg(path)
    assert len(result.shapes) == 1
    assert result.report.runs_unclosed == 1
    assert any("not closed" in w for w in result.report.warnings)


def test_layer_filter_uses_inkscape_label(tmp_cad_dir: Path):
    body = (
        '<rect id="cutline" x="0" y="0" width="100" height="100"/>'
        '<rect id="guide" x="200" y="200" width="100" height="100"/>'
    )
    path = _write_svg(tmp_cad_dir, "labels.svg", body)
    result = read_svg(path, ImportOptions(exclude_layers=frozenset({"guide"})))
    assert len(result.shapes) == 1


def test_empty_svg_warns(tmp_cad_dir: Path):
    path = _write_svg(tmp_cad_dir, "empty.svg", "")
    result = read_svg(path)
    assert result.shapes == []
    assert any("no path geometry" in w for w in result.report.warnings)


def test_missing_file_raises():
    with pytest.raises(ImportError_, match="not found"):
        read_svg("nope.svg")


def test_malformed_svg_raises(tmp_path: Path):
    bad = tmp_path / "bad.svg"
    bad.write_text("<svg><this is not xml", encoding="utf-8")
    with pytest.raises(ImportError_):
        read_svg(bad)


def test_dispatcher_routes_svg(tmp_cad_dir: Path):
    path = _write_svg(tmp_cad_dir, "d.svg", '<rect x="0" y="0" width="96" height="96"/>')
    assert len(read_geometry(path).shapes) == 1


def test_billion_laughs_is_rejected_before_parsing(tmp_cad_dir: Path):
    """Entity expansion must be refused, not parsed and then survived."""
    from grainline.core.io.svg_reader import UnsafeSvgError

    bomb = (
        '<?xml version="1.0"?>\n'
        "<!DOCTYPE svg [\n"
        '  <!ENTITY a "aaaaaaaaaa">\n'
        '  <!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">\n'
        '  <!ENTITY c "&b;&b;&b;&b;&b;&b;&b;&b;&b;&b;">\n'
        "]>\n"
        '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">'
        '<rect x="0" y="0" width="&c;" height="10"/></svg>\n'
    )
    path = tmp_cad_dir / "bomb.svg"
    path.write_text(bomb, encoding="utf-8")

    with pytest.raises(UnsafeSvgError, match="ENTITY|DOCTYPE"):
        read_svg(path)


def test_external_entity_disclosure_is_rejected(tmp_cad_dir: Path):
    from grainline.core.io.svg_reader import UnsafeSvgError

    xxe = (
        '<?xml version="1.0"?>\n'
        '<!DOCTYPE svg [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>\n'
        '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10">'
        "<desc>&xxe;</desc></svg>\n"
    )
    path = tmp_cad_dir / "xxe.svg"
    path.write_text(xxe, encoding="utf-8")

    with pytest.raises(UnsafeSvgError):
        read_svg(path)


def test_unsafe_screen_also_guards_the_stream_path(tmp_cad_dir: Path):
    """The web uploader must not bypass the screen by streaming."""
    import io

    from grainline.core.io.svg_reader import UnsafeSvgError, read_svg_stream

    payload = (
        '<!DOCTYPE svg [<!ENTITY x "y">]>'
        '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"/>'
    )
    with pytest.raises(UnsafeSvgError):
        read_svg_stream(io.BytesIO(payload.encode("utf-8")))


def test_ordinary_svg_still_reads_through_the_stream_path(tmp_cad_dir: Path):
    import io

    from grainline.core.io.svg_reader import read_svg_stream

    payload = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="400" height="400" '
        'viewBox="0 0 400 400"><rect x="0" y="0" width="96" height="96"/></svg>'
    )
    result = read_svg_stream(io.BytesIO(payload.encode("utf-8")))
    assert len(result.shapes) == 1


def test_min_area_filters_specks(tmp_cad_dir: Path):
    body = (
        '<rect x="0" y="0" width="200" height="200"/>'
        '<rect x="300" y="300" width="1" height="1"/>'
    )
    path = _write_svg(tmp_cad_dir, "speck.svg", body)
    result = read_svg(path, ImportOptions(min_area=1.0))
    assert len(result.shapes) == 1
    assert result.report.shapes_dropped_small == 1

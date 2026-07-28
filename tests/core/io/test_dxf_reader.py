"""Tests for the DXF importer, using files generated with ezdxf."""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from grainline.core.io.base import ImportError_, ImportOptions
from grainline.core.io.dxf_reader import read_dxf
from grainline.core.io.reader import read_geometry

from tests.conftest import regular_polygon

pytestmark = pytest.mark.free


def _new_doc(insunits: int = 4):
    """A fresh DXF document with the given ``$INSUNITS`` code (4 = mm)."""
    import ezdxf

    doc = ezdxf.new("R2010", setup=True)
    doc.header["$INSUNITS"] = insunits
    return doc


def _write(doc, path: Path, name: str) -> Path:
    out = path / name
    doc.saveas(str(out))
    return out


def test_reads_closed_lwpolyline(tmp_cad_dir: Path):
    doc = _new_doc()
    msp = doc.modelspace()
    msp.add_lwpolyline(
        [(0, 0), (100, 0), (100, 50), (0, 50)], close=True
    )
    path = _write(doc, tmp_cad_dir, "rect.dxf")

    result = read_dxf(path)
    assert len(result.shapes) == 1
    assert result.shapes[0].area == pytest.approx(5000.0)
    assert result.report.unit == "mm"
    assert result.report.scale_to_mm == 1.0


def test_reads_circle_as_shape(tmp_cad_dir: Path):
    doc = _new_doc()
    doc.modelspace().add_circle((0, 0), radius=25.0)
    path = _write(doc, tmp_cad_dir, "circle.dxf")

    result = read_dxf(path, ImportOptions(chord_tolerance=0.01))
    assert len(result.shapes) == 1
    assert result.shapes[0].area == pytest.approx(math.pi * 625.0, rel=1e-3)


def test_reassembles_exploded_lines_into_a_part(tmp_cad_dir: Path):
    """The headline importer capability: no closed entity exists in this file."""
    doc = _new_doc()
    msp = doc.modelspace()
    ring = [(0, 0), (80, 0), (80, 40), (0, 40)]
    for i in range(len(ring)):
        msp.add_line(ring[i], ring[(i + 1) % len(ring)])
    path = _write(doc, tmp_cad_dir, "exploded.dxf")

    result = read_dxf(path)
    assert result.report.entities_read == 4
    assert result.report.rings_closed == 1
    assert result.report.runs_unclosed == 0
    assert len(result.shapes) == 1
    assert result.shapes[0].area == pytest.approx(3200.0)


def test_concentric_circles_become_a_washer(tmp_cad_dir: Path):
    doc = _new_doc()
    msp = doc.modelspace()
    msp.add_circle((0, 0), radius=40.0)
    msp.add_circle((0, 0), radius=15.0)
    path = _write(doc, tmp_cad_dir, "washer.dxf")

    result = read_dxf(path, ImportOptions(chord_tolerance=0.01))
    assert len(result.shapes) == 1
    assert len(result.shapes[0].holes) == 1
    expected = math.pi * (40.0**2 - 15.0**2)
    assert result.shapes[0].area == pytest.approx(expected, rel=1e-3)


def test_inch_units_are_converted_to_millimetres(tmp_cad_dir: Path):
    doc = _new_doc(insunits=1)  # inches
    doc.modelspace().add_lwpolyline(
        [(0, 0), (4, 0), (4, 2), (0, 2)], close=True
    )
    path = _write(doc, tmp_cad_dir, "inches.dxf")

    result = read_dxf(path)
    assert result.report.unit == "in"
    assert result.report.scale_to_mm == pytest.approx(25.4)
    shape = result.shapes[0]
    assert shape.width == pytest.approx(4 * 25.4)
    assert shape.area == pytest.approx(4 * 2 * 25.4**2)


def test_unitless_file_warns_and_uses_default(tmp_cad_dir: Path):
    doc = _new_doc(insunits=0)
    doc.modelspace().add_lwpolyline([(0, 0), (10, 0), (10, 10)], close=True)
    path = _write(doc, tmp_cad_dir, "unitless.dxf")

    result = read_dxf(path)
    assert any("no usable unit" in w for w in result.report.warnings)
    assert result.report.unit == "mm"


def test_unit_override_beats_file_header(tmp_cad_dir: Path):
    doc = _new_doc(insunits=4)  # says mm
    doc.modelspace().add_lwpolyline([(0, 0), (1, 0), (1, 1), (0, 1)], close=True)
    path = _write(doc, tmp_cad_dir, "override.dxf")

    result = read_dxf(path, ImportOptions(unit_override="in", min_area=0.1))
    assert result.report.unit == "in"
    assert result.shapes[0].width == pytest.approx(25.4)


def test_excluded_layer_is_skipped(tmp_cad_dir: Path):
    doc = _new_doc()
    msp = doc.modelspace()
    doc.layers.add("PART")
    doc.layers.add("DIMENSIONS")
    msp.add_lwpolyline(
        [(0, 0), (50, 0), (50, 50), (0, 50)], close=True, dxfattribs={"layer": "PART"}
    )
    msp.add_lwpolyline(
        [(200, 200), (260, 200), (260, 260)], close=True,
        dxfattribs={"layer": "DIMENSIONS"},
    )
    path = _write(doc, tmp_cad_dir, "layers.dxf")

    result = read_dxf(path, ImportOptions(exclude_layers=frozenset({"dimensions"})))
    assert len(result.shapes) == 1
    assert result.shapes[0].area == pytest.approx(2500.0)
    assert "layer:DIMENSIONS" in result.report.entities_skipped


def test_include_layers_whitelist(tmp_cad_dir: Path):
    doc = _new_doc()
    msp = doc.modelspace()
    doc.layers.add("CUT")
    msp.add_lwpolyline(
        [(0, 0), (30, 0), (30, 30), (0, 30)], close=True, dxfattribs={"layer": "CUT"}
    )
    msp.add_lwpolyline([(100, 100), (140, 100), (140, 140)], close=True)
    path = _write(doc, tmp_cad_dir, "whitelist.dxf")

    result = read_dxf(path, ImportOptions(include_layers=frozenset({"CUT"})))
    assert len(result.shapes) == 1


def test_block_references_are_expanded(tmp_cad_dir: Path):
    """A file made entirely of INSERTs must not import as empty."""
    doc = _new_doc()
    block = doc.blocks.new(name="BRACKET")
    block.add_lwpolyline([(0, 0), (40, 0), (40, 20), (0, 20)], close=True)

    msp = doc.modelspace()
    msp.add_blockref("BRACKET", (0, 0))
    msp.add_blockref("BRACKET", (100, 0))
    msp.add_blockref("BRACKET", (200, 0))
    path = _write(doc, tmp_cad_dir, "blocks.dxf")

    result = read_dxf(path)
    assert len(result.shapes) == 3
    assert all(s.area == pytest.approx(800.0) for s in result.shapes)


def test_text_entities_are_counted_as_skipped(tmp_cad_dir: Path):
    doc = _new_doc()
    msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0), (60, 0), (60, 30), (0, 30)], close=True)
    msp.add_text("PART A", height=5).set_placement((10, 10))
    path = _write(doc, tmp_cad_dir, "withtext.dxf")

    result = read_dxf(path)
    assert len(result.shapes) == 1
    assert result.report.entities_skipped.get("TEXT", 0) == 1


def test_unclosed_geometry_is_reported(tmp_cad_dir: Path):
    doc = _new_doc()
    msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0), (50, 0), (50, 50), (0, 50)], close=True)
    msp.add_line((300, 300), (400, 350))
    path = _write(doc, tmp_cad_dir, "dangling.dxf")

    result = read_dxf(path)
    assert result.report.runs_unclosed == 1
    assert any("could not be closed" in w for w in result.report.warnings)
    assert not result.report.is_clean


def test_clean_file_reports_clean(tmp_cad_dir: Path):
    doc = _new_doc()
    doc.modelspace().add_lwpolyline(
        [(0, 0), (60, 0), (60, 60), (0, 60)], close=True
    )
    path = _write(doc, tmp_cad_dir, "clean.dxf")
    result = read_dxf(path)
    assert result.report.is_clean
    assert "1 shape(s)" in result.report.summary()


def test_arc_flattening_respects_chord_tolerance(tmp_cad_dir: Path):
    doc = _new_doc()
    doc.modelspace().add_circle((0, 0), radius=100.0)
    path = _write(doc, tmp_cad_dir, "arc_tol.dxf")

    coarse = read_dxf(path, ImportOptions(chord_tolerance=2.0))
    fine = read_dxf(path, ImportOptions(chord_tolerance=0.01))
    assert len(fine.shapes[0].outer) > len(coarse.shapes[0].outer)
    # The finer flattening must be closer to the true circle area.
    true_area = math.pi * 100.0**2
    assert abs(fine.shapes[0].area - true_area) < abs(coarse.shapes[0].area - true_area)


def test_min_area_drops_tiny_marks(tmp_cad_dir: Path):
    doc = _new_doc()
    msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0), (80, 0), (80, 80), (0, 80)], close=True)
    msp.add_lwpolyline(
        [(200, 200), (200.4, 200), (200.4, 200.4), (200, 200.4)], close=True
    )
    path = _write(doc, tmp_cad_dir, "marks.dxf")

    result = read_dxf(path, ImportOptions(min_area=1.0))
    assert len(result.shapes) == 1
    assert result.report.shapes_dropped_small == 1


def test_missing_file_raises():
    with pytest.raises(ImportError_, match="not found"):
        read_dxf("does_not_exist.dxf")


def test_dispatcher_routes_by_extension(tmp_cad_dir: Path):
    doc = _new_doc()
    doc.modelspace().add_lwpolyline([(0, 0), (10, 0), (10, 10), (0, 10)], close=True)
    path = _write(doc, tmp_cad_dir, "dispatch.dxf")
    assert len(read_geometry(path).shapes) == 1


def test_dispatcher_rejects_unknown_extension(tmp_path: Path):
    bad = tmp_path / "part.step"
    bad.write_text("not cad")
    with pytest.raises(ImportError_, match="unsupported file type"):
        read_geometry(bad)


def test_empty_modelspace_warns(tmp_cad_dir: Path):
    doc = _new_doc()
    path = _write(doc, tmp_cad_dir, "empty.dxf")
    result = read_dxf(path)
    assert result.shapes == []
    assert any("no cuttable geometry" in w for w in result.report.warnings)


def test_polygon_with_many_vertices_survives_roundtrip(tmp_cad_dir: Path):
    doc = _new_doc()
    doc.modelspace().add_lwpolyline(regular_polygon(200, 75.0), close=True)
    path = _write(doc, tmp_cad_dir, "dense.dxf")

    result = read_dxf(path, ImportOptions(simplify_tolerance=0.0))
    assert len(result.shapes) == 1
    assert len(result.shapes[0].outer) == 200

"""Tests for nest export to SVG and DXF, plus the yield report renderers."""

from __future__ import annotations

import json
import math
from pathlib import Path
from xml.etree import ElementTree

import pytest

from grainline.core.geometry.primitives import Shape
from grainline.core.io.dxf_writer import (
    LAYER_PARTS,
    LAYER_SHEET,
    sheet_to_dxf_document,
    write_nest_dxf,
)
from grainline.core.io.svg_writer import sheet_to_svg, write_nest_svg
from grainline.core.model.job import Job, NestConfig
from grainline.core.model.part import Part
from grainline.core.model.stock import Stock
from grainline.core.nesting import nest
from grainline.core.report import build_report, render_console, render_html

from tests.conftest import l_bracket, washer

pytestmark = pytest.mark.free


@pytest.fixture
def nested():
    job = Job(
        parts=[
            Part(id="bracket", shape=l_bracket(120.0, 70.0, 22.0), quantity=4),
            Part(id="ring", shape=washer(40.0, 18.0, 32), quantity=3),
            Part(id="plate", shape=Shape.rectangle(200.0, 90.0), quantity=2),
        ],
        stock=[Stock(id="alu-2440", width=1200.0, height=800.0, cost=180.0)],
        config=NestConfig(part_spacing=3.0),
        name="test-job",
    )
    return job, nest(job)


# --- SVG -------------------------------------------------------------------


def test_svg_is_well_formed_xml(nested):
    _, result = nested
    svg = sheet_to_svg(result, result.sheets()[0])
    root = ElementTree.fromstring(svg)
    assert root.tag.endswith("svg")


def test_svg_declares_physical_millimetre_size(nested):
    _, result = nested
    layout = result.sheets()[0]
    svg = sheet_to_svg(result, layout)
    root = ElementTree.fromstring(svg)
    assert root.get("width") == f"{layout.stock.width:.3f}mm"
    assert root.get("height") == f"{layout.stock.height:.3f}mm"


def test_svg_contains_one_path_per_placement(nested):
    _, result = nested
    layout = result.sheets()[0]
    svg = sheet_to_svg(result, layout)
    root = ElementTree.fromstring(svg)
    paths = root.findall(".//{http://www.w3.org/2000/svg}path")
    assert len(paths) == layout.part_count


def test_svg_flips_y_once_at_the_top_level(nested):
    """CAD Y grows up, SVG Y grows down. One wrapping flip, never per-shape."""
    _, result = nested
    layout = result.sheets()[0]
    svg = sheet_to_svg(result, layout)
    assert svg.count("scale(1 -1)") == 1


def test_svg_escapes_part_keys(nested):
    _, result = nested
    job = Job(
        parts=[Part(id="a<b>&c", shape=Shape.rectangle(50.0, 50.0), quantity=1)],
        stock=[Stock(id="s", width=500.0, height=500.0)],
    )
    result = nest(job)
    svg = sheet_to_svg(result, result.sheets()[0])
    assert "a<b>&c" not in svg
    assert "&lt;b&gt;" in svg
    ElementTree.fromstring(svg)  # must still parse


def test_write_nest_svg_creates_one_file_per_sheet(tmp_path: Path):
    job = Job(
        parts=[Part(id="big", shape=Shape.rectangle(600.0, 600.0), quantity=3)],
        stock=[Stock(id="s", width=1000.0, height=1000.0)],
    )
    result = nest(job)
    written = write_nest_svg(result, tmp_path / "out")
    assert len(written) == 3
    assert all(p.exists() and p.stat().st_size > 0 for p in written)
    assert [p.name for p in written] == ["sheet-001.svg", "sheet-002.svg", "sheet-003.svg"]


def test_svg_watermark_is_rendered(nested):
    _, result = nested
    svg = sheet_to_svg(result, result.sheets()[0], show_watermark="FREE EDITION")
    assert "FREE EDITION" in svg


# --- DXF -------------------------------------------------------------------


def test_dxf_separates_sheet_outline_from_cut_geometry(nested):
    """If the sheet outline lands on the cut layer the machine cuts the board."""
    _, result = nested
    layout = result.sheets()[0]
    doc = sheet_to_dxf_document(result, layout)
    msp = doc.modelspace()

    sheet_entities = [e for e in msp if e.dxf.layer == LAYER_SHEET]
    part_entities = [e for e in msp if e.dxf.layer == LAYER_PARTS]

    assert len(sheet_entities) == 1
    assert len(part_entities) >= layout.part_count


def test_dxf_declares_millimetre_units(nested):
    _, result = nested
    doc = sheet_to_dxf_document(result, result.sheets()[0])
    assert doc.header["$INSUNITS"] == 4


def test_dxf_writes_holes_as_separate_polylines():
    job = Job(
        parts=[Part(id="ring", shape=washer(50.0, 25.0, 32), quantity=1)],
        stock=[Stock(id="s", width=400.0, height=400.0)],
    )
    result = nest(job)
    doc = sheet_to_dxf_document(result, result.sheets()[0], include_labels=False)
    part_entities = [e for e in doc.modelspace() if e.dxf.layer == LAYER_PARTS]
    # One outer contour plus one hole.
    assert len(part_entities) == 2


def test_dxf_roundtrips_through_the_reader(tmp_path: Path):
    """Export then re-import must preserve part count and area."""
    from grainline.core.io.base import ImportOptions
    from grainline.core.io.dxf_reader import read_dxf

    job = Job(
        parts=[Part(id="p", shape=Shape.rectangle(150.0, 90.0), quantity=3)],
        stock=[Stock(id="s", width=800.0, height=800.0)],
        config=NestConfig(part_spacing=5.0),
    )
    result = nest(job)
    written = write_nest_dxf(result, tmp_path, include_labels=False)
    assert len(written) == 1

    reimported = read_dxf(
        written[0],
        ImportOptions(
            include_layers=frozenset({LAYER_PARTS}), min_area=10.0
        ),
    )
    assert len(reimported.shapes) == 3
    for shape in reimported.shapes:
        assert shape.area == pytest.approx(150.0 * 90.0, rel=1e-6)


def test_write_nest_dxf_creates_one_file_per_sheet(tmp_path: Path):
    job = Job(
        parts=[Part(id="big", shape=Shape.rectangle(600.0, 600.0), quantity=2)],
        stock=[Stock(id="s", width=1000.0, height=1000.0)],
    )
    result = nest(job)
    written = write_nest_dxf(result, tmp_path)
    assert len(written) == 2
    assert all(p.suffix == ".dxf" and p.exists() for p in written)


# --- Report ----------------------------------------------------------------


def test_report_is_json_serialisable(nested):
    job, result = nested
    data = build_report(result, job)
    json.dumps(data)  # must not raise
    assert data["summary"]["sheets_used"] == result.sheets_used
    assert data["job"]["name"] == "test-job"


def test_report_part_rows_reconcile_with_the_job(nested):
    job, result = nested
    data = build_report(result, job)
    for row in data["parts"]:
        assert row["placed"] + row["unplaced"] == row["required"]


def test_report_flags_irregular_parts_as_upgrade_worthy(nested):
    _, result = nested
    signal = build_report(result)["upgrade_signal"]
    # L-brackets and washers fill well under 80% of their bounding boxes.
    assert signal["irregularity_index"] < 0.80
    assert signal["worthwhile"] is True
    assert "bounding boxes" in signal["message"]


def test_report_does_not_oversell_on_rectangular_jobs():
    """Honest reporting: a job of rectangles gets no upgrade pitch."""
    job = Job(
        parts=[Part(id="sq", shape=Shape.rectangle(200.0, 100.0), quantity=6)],
        stock=[Stock(id="s", width=1000.0, height=1000.0)],
    )
    result = nest(job)
    signal = build_report(result, job)["upgrade_signal"]
    assert signal["irregularity_index"] == pytest.approx(1.0)
    assert signal["worthwhile"] is False
    assert "near-optimal" in signal["message"]


def test_report_estimates_cost_savings_when_stock_is_priced(nested):
    job, result = nested
    signal = build_report(result, job)["upgrade_signal"]
    assert signal["estimated_cost_saved"] >= 0.0
    assert signal["trapped_area_mm2"] > 0.0


def test_console_report_contains_headline_numbers(nested):
    job, result = nested
    text = render_console(result, job)
    assert "Yield" in text
    assert "Sheets used" in text
    assert "test-job" in text


def test_html_report_is_well_formed(nested):
    job, result = nested
    html = render_html(result, job)
    assert html.startswith("<!DOCTYPE html>")
    assert "GRAINLINE nest report" in html
    assert "prefers-color-scheme" in html
    # Every sheet must appear as a table row.
    assert html.count("<tr>") >= result.sheets_used


def test_html_report_escapes_job_names():
    job = Job(
        parts=[Part(id="p", shape=Shape.rectangle(50.0, 50.0), quantity=1)],
        stock=[Stock(id="s", width=500.0, height=500.0)],
        name="<script>alert(1)</script>",
    )
    result = nest(job)
    html = render_html(result, job)
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_report_surfaces_unplaced_parts():
    job = Job(
        parts=[Part(id="huge", shape=Shape.rectangle(5000.0, 5000.0), quantity=2)],
        stock=[Stock(id="s", width=1000.0, height=1000.0)],
    )
    result = nest(job)
    data = build_report(result, job)
    assert data["unplaced"] == {"huge": 2}
    assert data["summary"]["complete"] is False
    assert "UNPLACED" in render_console(result, job)

"""Tests for the CLI and the local web interface."""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest
from typer.testing import CliRunner

from grainline.cli.main import EXIT_ERROR, EXIT_INCOMPLETE, EXIT_OK, app

pytestmark = pytest.mark.free

runner = CliRunner()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def isolated_license(monkeypatch, tmp_path):
    from grainline.core.licensing import ENV_HOME, ENV_LICENSE, clear_cache

    monkeypatch.delenv(ENV_LICENSE, raising=False)
    monkeypatch.setenv(ENV_HOME, str(tmp_path / "gl-home"))
    clear_cache()
    yield
    clear_cache()


def _write_dxf(path: Path, width: float, height: float) -> Path:
    import ezdxf

    doc = ezdxf.new("R2010")
    doc.header["$INSUNITS"] = 4
    doc.modelspace().add_lwpolyline(
        [(0, 0), (width, 0), (width, height), (0, height)], close=True
    )
    doc.saveas(str(path))
    return path


@pytest.fixture
def job_dir(tmp_path: Path) -> Path:
    parts = tmp_path / "parts"
    parts.mkdir()
    _write_dxf(parts / "plate.dxf", 300.0, 200.0)
    _write_dxf(parts / "tab.dxf", 90.0, 60.0)

    (tmp_path / "job.toml").write_text(
        """
name = "cli-test"

[config]
kerf = 0.2
part_spacing = 2.0
sheet_margin = 5.0

[[stock]]
id = "alu"
width = 2440
height = 1220
cost = 180.0

[[parts]]
id = "plate"
file = "parts/plate.dxf"
quantity = 6

[[parts]]
id = "tab"
file = "parts/tab.dxf"
quantity = 10
""",
        encoding="utf-8",
    )
    return tmp_path


# ---------------------------------------------------------------------------
# CLI - nest
# ---------------------------------------------------------------------------


def test_nest_succeeds_and_writes_output(job_dir: Path):
    out = job_dir / "out"
    result = runner.invoke(
        app,
        ["nest", str(job_dir / "job.toml"), "--out", str(out),
         "-f", "svg", "-f", "json", "-f", "html", "-f", "dxf"],
    )
    assert result.exit_code == EXIT_OK, result.output
    assert (out / "report.json").is_file()
    assert (out / "report.html").is_file()
    assert list(out.glob("sheet-*.svg"))
    assert list(out.glob("sheet-*.dxf"))


def test_nest_json_mode_emits_parsable_report(job_dir: Path):
    result = runner.invoke(
        app,
        ["nest", str(job_dir / "job.toml"), "--out", str(job_dir / "o"),
         "--json", "-f", "json"],
    )
    assert result.exit_code == EXIT_OK
    payload = json.loads((job_dir / "o" / "report.json").read_text())
    assert payload["summary"]["parts_placed"] == 16
    assert payload["job"]["name"] == "cli-test"


def test_nest_returns_exit_code_two_when_parts_are_unplaced(tmp_path: Path):
    """A nest that silently drops parts is how a job ships short."""
    parts = tmp_path / "parts"
    parts.mkdir()
    _write_dxf(parts / "huge.dxf", 5000.0, 5000.0)
    (tmp_path / "job.toml").write_text(
        """
name = "too-big"
[[stock]]
id = "s"
width = 1000
height = 1000
[[parts]]
id = "huge"
file = "parts/huge.dxf"
quantity = 2
""",
        encoding="utf-8",
    )
    result = runner.invoke(
        app, ["nest", str(tmp_path / "job.toml"), "--out", str(tmp_path / "o")]
    )
    assert result.exit_code == EXIT_INCOMPLETE
    assert "UNPLACED" in result.output


def test_nest_command_line_overrides_beat_the_file(job_dir: Path):
    tight = runner.invoke(
        app, ["nest", str(job_dir / "job.toml"), "--out", str(job_dir / "a"),
              "--spacing", "1", "-f", "json"]
    )
    loose = runner.invoke(
        app, ["nest", str(job_dir / "job.toml"), "--out", str(job_dir / "b"),
              "--spacing", "80", "-f", "json"]
    )
    assert tight.exit_code == EXIT_OK
    a = json.loads((job_dir / "a" / "report.json").read_text())
    b = json.loads((job_dir / "b" / "report.json").read_text())
    # Wider spacing must consume at least as much material.
    assert b["summary"]["sheets_used"] >= a["summary"]["sheets_used"]


def test_nest_rejects_unknown_output_format(job_dir: Path):
    result = runner.invoke(
        app, ["nest", str(job_dir / "job.toml"), "--out", str(job_dir / "o"),
              "-f", "pdf"]
    )
    assert result.exit_code == EXIT_ERROR
    assert "unknown output format" in result.output


def test_nest_reports_a_missing_job_file():
    result = runner.invoke(app, ["nest", "nope.toml"])
    assert result.exit_code == EXIT_ERROR
    assert "not found" in result.output


def test_nest_reports_a_malformed_job_file(tmp_path: Path):
    bad = tmp_path / "job.toml"
    bad.write_text("name = = broken", encoding="utf-8")
    result = runner.invoke(app, ["nest", str(bad)])
    assert result.exit_code == EXIT_ERROR
    assert "invalid TOML" in result.output


def test_unknown_job_setting_is_named_not_ignored(tmp_path: Path):
    """Silently ignoring a misspelled key produces undersized parts."""
    parts = tmp_path / "parts"
    parts.mkdir()
    _write_dxf(parts / "p.dxf", 100.0, 100.0)
    (tmp_path / "job.toml").write_text(
        """
[config]
spacing = 3.0
[[stock]]
id = "s"
width = 1000
height = 1000
[[parts]]
id = "p"
file = "parts/p.dxf"
""",
        encoding="utf-8",
    )
    result = runner.invoke(app, ["nest", str(tmp_path / "job.toml")])
    assert result.exit_code == EXIT_ERROR
    assert "unknown setting" in result.output
    assert "config.spacing" in result.output


def test_nest_reports_missing_geometry_file(tmp_path: Path):
    (tmp_path / "job.toml").write_text(
        """
[[stock]]
id = "s"
width = 1000
height = 1000
[[parts]]
id = "ghost"
file = "parts/ghost.dxf"
""",
        encoding="utf-8",
    )
    result = runner.invoke(app, ["nest", str(tmp_path / "job.toml")])
    assert result.exit_code == EXIT_ERROR
    assert "ghost" in result.output


def test_premium_strategy_is_refused_with_an_upgrade_message(job_dir: Path):
    from grainline.core.licensing import Tier
    from grainline.core.nesting.guillotine import GuillotineNester
    from grainline.core.registry import register_strategy

    register_strategy("cli-premium-fixture", GuillotineNester,
                      tier=Tier.PREMIUM, replace=True)

    result = runner.invoke(
        app, ["nest", str(job_dir / "job.toml"), "-s", "cli-premium-fixture"]
    )
    assert result.exit_code == EXIT_ERROR
    assert "Premium" in result.output


# ---------------------------------------------------------------------------
# CLI - inspect, strategies, init, licence
# ---------------------------------------------------------------------------


def test_inspect_reports_geometry(tmp_path: Path):
    path = _write_dxf(tmp_path / "p.dxf", 240.0, 120.0)
    result = runner.invoke(app, ["inspect", str(path)])
    assert result.exit_code == EXIT_OK
    assert "1 shape(s) built" in result.output
    assert "240.00 x 120.00" in result.output


def test_inspect_fails_on_an_empty_file(tmp_path: Path):
    import ezdxf

    doc = ezdxf.new("R2010")
    doc.saveas(str(tmp_path / "empty.dxf"))
    result = runner.invoke(app, ["inspect", str(tmp_path / "empty.dxf")])
    assert result.exit_code == EXIT_ERROR


def test_strategies_lists_guillotine_as_available():
    result = runner.invoke(app, ["strategies"])
    assert result.exit_code == EXIT_OK
    assert "guillotine" in result.output
    assert "Free" in result.output


def test_init_writes_a_usable_example(tmp_path: Path):
    result = runner.invoke(app, ["init", str(tmp_path)])
    assert result.exit_code == EXIT_OK
    job = tmp_path / "job.toml"
    assert job.is_file()
    assert (tmp_path / "parts").is_dir()

    import tomllib

    document = tomllib.loads(job.read_text(encoding="utf-8"))
    assert document["config"]["kerf"] == 0.2
    assert document["stock"][0]["width"] == 2440


def test_init_refuses_to_clobber_without_force(tmp_path: Path):
    runner.invoke(app, ["init", str(tmp_path)])
    again = runner.invoke(app, ["init", str(tmp_path)])
    assert again.exit_code == EXIT_ERROR
    assert "already exists" in again.output
    assert runner.invoke(app, ["init", str(tmp_path), "--force"]).exit_code == EXIT_OK


def test_license_show_lists_search_paths():
    result = runner.invoke(app, ["license", "show"])
    assert result.exit_code == EXIT_OK
    assert "Free edition" in result.output
    assert "license.key" in result.output


def test_license_set_rejects_a_forged_key():
    result = runner.invoke(app, ["license", "set", "GL1-abc.def"])
    assert result.exit_code == EXIT_ERROR
    assert "licence rejected" in result.output


def test_version_prints_the_edition():
    result = runner.invoke(app, ["version"])
    assert result.exit_code == EXIT_OK
    assert "GRAINLINE" in result.output


def test_serve_refuses_a_non_loopback_bind_without_opt_in():
    result = runner.invoke(app, ["serve", "--host", "0.0.0.0"])
    assert result.exit_code == EXIT_ERROR
    assert "--allow-network" in result.output
    assert "0.0.0.0" in result.output


def test_serve_allows_a_non_loopback_bind_with_opt_in(monkeypatch):
    import sys
    from types import SimpleNamespace

    called: dict[str, object] = {}

    def fake_run(_app, host: str, port: int, **_kwargs: object) -> None:
        called["host"] = host
        called["port"] = port

    monkeypatch.setitem(sys.modules, "uvicorn", SimpleNamespace(run=fake_run))
    result = runner.invoke(app, ["serve", "--host", "0.0.0.0", "--allow-network"])
    assert result.exit_code == EXIT_OK
    assert called == {"host": "0.0.0.0", "port": 8711}


def test_serve_api_refuses_a_non_loopback_bind_without_opt_in():
    result = runner.invoke(app, ["serve-api", "--host", "0.0.0.0"])
    assert result.exit_code == EXIT_ERROR
    assert "--allow-network" in result.output


# ---------------------------------------------------------------------------
# Web
# ---------------------------------------------------------------------------


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from grainline.web.app import create_app

    return TestClient(create_app())


def test_index_page_is_self_contained(client):
    """No CDN reference may appear: shop PCs have no route to the internet."""
    response = client.get("/")
    assert response.status_code == 200
    body = response.text
    assert "<!DOCTYPE html>" in body
    for marker in ("https://", "http://cdn", "unpkg.com", "cdn.jsdelivr", "//cdn."):
        assert marker not in body, f"external reference {marker!r} in the page"


def test_license_endpoint(client):
    payload = client.get("/api/license").json()
    assert payload["tier"] == "FREE"
    assert "Free edition" in payload["description"]


def test_strategies_endpoint_marks_availability(client):
    payload = client.get("/api/strategies").json()
    names = {s["name"]: s for s in payload["strategies"]}
    assert names["guillotine"]["available"] is True


def test_nest_endpoint_places_uploaded_parts(client, tmp_path: Path):
    path = _write_dxf(tmp_path / "plate.dxf", 300.0, 200.0)
    response = client.post(
        "/api/nest",
        files=[("files", ("plate.dxf", path.read_bytes(), "application/dxf"))],
        data={"config": json.dumps({
            "sheet_width": 1200, "sheet_height": 900, "quantity_per_part": 4
        })},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["report"]["summary"]["parts_placed"] == 4
    assert len(payload["sheets"]) == 1
    assert payload["sheets"][0]["svg"].lstrip().startswith("<?xml")


def test_nest_endpoint_preserves_non_utf8_layer_names(client, tmp_path: Path):
    """A cp1252 DXF must not have its layer names mangled on upload.

    A corrupted layer name makes the operator's layer filter stop matching, and
    their dimension lines get nested as parts.
    """
    import ezdxf

    doc = ezdxf.new("R2010")
    doc.header["$INSUNITS"] = 4
    doc.layers.add("MAßSTAB")
    doc.modelspace().add_lwpolyline(
        [(0, 0), (200, 0), (200, 100), (0, 100)],
        close=True,
        dxfattribs={"layer": "MAßSTAB"},
    )
    path = tmp_path / "german.dxf"
    doc.saveas(str(path), encoding="cp1252")

    response = client.post(
        "/api/nest",
        files=[("files", ("german.dxf", path.read_bytes(), "application/dxf"))],
        data={"config": json.dumps({"sheet_width": 800, "sheet_height": 600})},
    )
    assert response.status_code == 200, response.text
    assert response.json()["report"]["summary"]["parts_placed"] == 1


def test_nest_endpoint_accepts_svg(client, tmp_path: Path):
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="400" height="400" '
        'viewBox="0 0 400 400"><rect x="0" y="0" width="192" height="96"/></svg>'
    )
    response = client.post(
        "/api/nest",
        files=[("files", ("part.svg", svg.encode(), "image/svg+xml"))],
        data={"config": json.dumps({"sheet_width": 800, "sheet_height": 600})},
    )
    assert response.status_code == 200
    assert response.json()["report"]["summary"]["parts_placed"] == 1


def test_nest_endpoint_rejects_a_malicious_svg(client):
    """The XXE screen must apply to the upload path, not just to files on disk."""
    bomb = (
        '<!DOCTYPE svg [<!ENTITY a "aaaa">]>'
        '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"/>'
    )
    response = client.post(
        "/api/nest",
        files=[("files", ("bomb.svg", bomb.encode(), "image/svg+xml"))],
        data={"config": "{}"},
    )
    assert response.status_code == 400
    assert "no usable geometry" in response.json()["detail"]


def test_nest_endpoint_rejects_unsupported_types(client):
    response = client.post(
        "/api/nest",
        files=[("files", ("model.step", b"ISO-10303-21;", "application/step"))],
        data={"config": "{}"},
    )
    assert response.status_code == 400


def test_nest_endpoint_rejects_invalid_config(client, tmp_path: Path):
    path = _write_dxf(tmp_path / "p.dxf", 100.0, 100.0)
    response = client.post(
        "/api/nest",
        files=[("files", ("p.dxf", path.read_bytes(), "application/dxf"))],
        data={"config": "not json"},
    )
    assert response.status_code == 400
    assert "invalid config" in response.json()["detail"]


def test_nest_endpoint_enforces_a_file_count_limit(client, tmp_path: Path):
    from grainline.web.app import MAX_FILES

    path = _write_dxf(tmp_path / "p.dxf", 20.0, 20.0)
    payload = path.read_bytes()
    files = [("files", (f"p{i}.dxf", payload, "application/dxf"))
             for i in range(MAX_FILES + 1)]
    response = client.post("/api/nest", files=files, data={"config": "{}"})
    assert response.status_code == 413


def test_nest_endpoint_gates_premium_strategies_with_402(client, tmp_path: Path):
    from grainline.core.licensing import Tier
    from grainline.core.nesting.guillotine import GuillotineNester
    from grainline.core.registry import register_strategy

    register_strategy("web-premium-fixture", GuillotineNester,
                      tier=Tier.PREMIUM, replace=True)

    path = _write_dxf(tmp_path / "p.dxf", 100.0, 100.0)
    response = client.post(
        "/api/nest",
        files=[("files", ("p.dxf", path.read_bytes(), "application/dxf"))],
        data={"config": json.dumps({"strategy": "web-premium-fixture"})},
    )
    assert response.status_code == 402  # Payment Required - literally correct here
    assert "Premium" in response.json()["detail"]


def test_free_tier_output_is_watermarked(client, tmp_path: Path):
    path = _write_dxf(tmp_path / "p.dxf", 200.0, 100.0)
    response = client.post(
        "/api/nest",
        files=[("files", ("p.dxf", path.read_bytes(), "application/dxf"))],
        data={"config": "{}"},
    )
    assert "GRAINLINE Free edition" in response.json()["sheets"][0]["svg"]


def test_openapi_schema_is_disabled(client):
    """A local tool should not publish an API surface it does not support."""
    assert client.get("/openapi.json").status_code == 404
    assert client.get("/docs").status_code == 404

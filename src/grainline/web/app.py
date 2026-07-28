"""Local FastAPI application backing the GRAINLINE web interface.

Two properties are non-negotiable here, and both are about trust rather than
features:

**Nothing touches the disk.** Uploads are parsed from memory and results are
returned in the response. A shop's DXF files are their competitive advantage;
this server must not leave copies of them in a temp directory that nobody
remembers to clean out.

**Nothing leaves the machine.** No telemetry, no CDN, no outbound request of any
kind. The CLI binds to loopback by default and warns loudly otherwise.
"""

# NOTE: this module deliberately does *not* use `from __future__ import
# annotations`. FastAPI resolves route parameter annotations at decoration time,
# and the future import turns them into strings that are looked up in module
# globals. Because the FastAPI symbols are imported inside `create_app` - so that
# `grainline` stays importable without the optional web extras - those lookups
# would fail with an unresolvable forward reference. Evaluating annotations
# eagerly against the enclosing scope is what makes both properties hold at once.

import json
from typing import Any, Dict, List, Tuple

from ..core.licensing import Tier, active_license, active_license_problem, active_tier
from .ui import INDEX_HTML

__all__ = ["create_app", "MAX_UPLOAD_BYTES", "MAX_FILES"]

#: Largest single upload accepted, in bytes. Generous for CAD but far below what
#: it takes to exhaust memory on a shop PC.
MAX_UPLOAD_BYTES: int = 32 * 1024 * 1024

#: Most files accepted in one request.
MAX_FILES: int = 200

#: Sheets rendered back to the browser. A job needing more than this is a batch
#: job for the CLI; sending 400 inline SVGs would lock up the tab.
MAX_RENDERED_SHEETS: int = 40


def _build_job(config: Dict[str, Any], uploads: List[Tuple[str, bytes]]):
    """Turn an upload batch and a settings dict into a runnable job."""
    import io

    from ..core.io.base import ImportError_, ImportOptions
    from ..core.io.dxf_reader import read_dxf_bytes
    from ..core.io.svg_reader import read_svg_stream
    from ..core.model.job import Job, NestConfig
    from ..core.model.part import Part
    from ..core.model.stock import Stock

    options = ImportOptions(
        chord_tolerance=float(config.get("chord_tolerance", 0.05)),
        min_area=float(config.get("min_area", 1.0)),
    )

    per_part = max(1, int(config.get("quantity_per_part", 1)))
    parts: List[Part] = []
    warnings: List[str] = []

    for filename, payload in uploads:
        suffix = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
        try:
            if suffix == "dxf":
                # Bytes, not text. DXF is routinely cp1252 or carries its own
                # $DWGCODEPAGE, and forcing UTF-8 with errors="replace" silently
                # mangles layer names - which makes layer filters stop matching
                # and quietly nests the dimension lines.
                imported = read_dxf_bytes(payload, options)
            elif suffix == "svg":
                imported = read_svg_stream(io.BytesIO(payload), options)
            else:
                warnings.append(f"{filename}: unsupported file type, skipped")
                continue
        except ImportError_ as exc:
            warnings.append(f"{filename}: {exc}")
            continue

        if not imported.shapes:
            warnings.append(f"{filename}: no usable geometry ({imported.report.summary()})")
            continue

        warnings.extend(f"{filename}: {w}" for w in imported.report.warnings)

        stem = filename.rsplit(".", 1)[0]
        if len(imported.shapes) == 1:
            parts.append(Part(id=stem, shape=imported.shapes[0], quantity=per_part,
                              source=filename))
        else:
            for index, shape in enumerate(imported.shapes, start=1):
                parts.append(
                    Part(id=f"{stem}-{index}", shape=shape, quantity=per_part,
                         source=filename)
                )

    quantity = int(config.get("sheet_quantity", 0) or 0)
    stock = Stock(
        id="sheet",
        width=float(config.get("sheet_width", 2440.0)),
        height=float(config.get("sheet_height", 1220.0)),
        quantity=None if quantity <= 0 else quantity,
        cost=float(config.get("sheet_cost", 0.0) or 0.0),
    )

    nest_config = NestConfig(
        kerf=float(config.get("kerf", 0.2)),
        part_spacing=float(config.get("part_spacing", 2.0)),
        sheet_margin=float(config.get("sheet_margin", 5.0)),
        strategy=str(config.get("strategy", "guillotine")),
    )

    return Job(parts=parts, stock=[stock], config=nest_config, name="web"), warnings


def create_app():
    """Build the FastAPI application.

    Constructed inside a function rather than at import time so that
    ``grainline`` remains importable and testable on a machine that never
    installed the optional web dependencies.
    """
    from fastapi import FastAPI, File, Form, HTTPException, UploadFile
    from fastapi.responses import HTMLResponse, JSONResponse

    api = FastAPI(
        title="GRAINLINE",
        version="1.0.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @api.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        return HTMLResponse(INDEX_HTML)

    @api.get("/api/license")
    def license_info() -> Dict[str, Any]:
        licence = active_license()
        return {
            "tier": licence.effective_tier.name,
            "description": licence.describe(),
            "problem": active_license_problem(),
        }

    @api.get("/api/strategies")
    def strategies() -> Dict[str, Any]:
        from ..core.registry import available_strategies

        tier = active_tier()
        return {
            "tier": tier.name,
            "strategies": [
                {
                    "name": name,
                    "tier": capability.tier.label,
                    "available": capability.tier <= tier,
                    "summary": capability.summary,
                    "upgrade_hint": capability.upgrade_hint,
                }
                for name, capability in sorted(
                    available_strategies().items(), key=lambda kv: (kv[1].tier, kv[0])
                )
            ],
        }

    @api.post("/api/nest")
    async def run_nest(
        files: List[UploadFile] = File(...),
        config: str = Form("{}"),
    ) -> JSONResponse:
        from ..core.io.svg_writer import sheet_to_svg
        from ..core.nesting import nest
        from ..core.nesting.base import NestingError
        from ..core.registry import CapabilityError, TierRequiredError
        from ..core.report import build_report

        if len(files) > MAX_FILES:
            raise HTTPException(
                status_code=413,
                detail=f"too many files: {len(files)} (limit {MAX_FILES})",
            )

        try:
            settings = json.loads(config) if config else {}
            if not isinstance(settings, dict):
                raise ValueError("config must be a JSON object")
        except (json.JSONDecodeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=f"invalid config: {exc}")

        uploads: List[Tuple[str, bytes]] = []
        total = 0
        for upload in files:
            payload = await upload.read()
            total += len(payload)
            if len(payload) > MAX_UPLOAD_BYTES or total > MAX_UPLOAD_BYTES * 2:
                raise HTTPException(
                    status_code=413,
                    detail=f"upload too large (limit {MAX_UPLOAD_BYTES // 1024 // 1024} MB per file)",
                )
            uploads.append((upload.filename or "unnamed", payload))

        try:
            job, warnings = _build_job(settings, uploads)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

        if not job.parts:
            raise HTTPException(
                status_code=400,
                detail="no usable geometry was found in the uploaded files. "
                + (" ".join(warnings) if warnings else ""),
            )

        try:
            result = nest(job, tier=active_tier())
        except TierRequiredError as exc:
            raise HTTPException(status_code=402, detail=str(exc))
        except (CapabilityError, NestingError) as exc:
            raise HTTPException(status_code=400, detail=str(exc))

        watermark = "" if active_tier() >= Tier.PREMIUM else "GRAINLINE Free edition"
        sheets = result.sheets()
        if len(sheets) > MAX_RENDERED_SHEETS:
            warnings.append(
                f"{len(sheets)} sheets produced; only the first "
                f"{MAX_RENDERED_SHEETS} are shown. Use the CLI to export them all."
            )

        return JSONResponse(
            {
                "report": build_report(result, job),
                "warnings": warnings,
                "sheets": [
                    {
                        "index": layout.index,
                        "svg": sheet_to_svg(result, layout, show_watermark=watermark),
                    }
                    for layout in sheets[:MAX_RENDERED_SHEETS]
                ],
            }
        )

    return api

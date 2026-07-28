"""Yield reporting: console tables, HTML and machine-readable JSON."""

from __future__ import annotations

from .yield_report import (
    UpgradeSignal,
    build_report,
    estimate_irregular_upside,
    render_console,
    render_html,
)

__all__ = [
    "UpgradeSignal",
    "build_report",
    "estimate_irregular_upside",
    "render_console",
    "render_html",
]

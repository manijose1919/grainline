"""Canonical domain model: Part, Stock, Job, Placement, NestResult."""

from __future__ import annotations

from .job import FREE_ROTATIONS, Job, NestConfig
from .part import GrainConstraint, Part, PartInstance
from .result import NestResult, Placement, SheetLayout
from .stock import UNLIMITED, Stock

__all__ = [
    "FREE_ROTATIONS",
    "UNLIMITED",
    "GrainConstraint",
    "Job",
    "NestConfig",
    "NestResult",
    "Part",
    "PartInstance",
    "Placement",
    "SheetLayout",
    "Stock",
]

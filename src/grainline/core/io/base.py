"""Shared contracts for every GRAINLINE importer.

Both the DXF and SVG readers produce the same thing: a list of validated shapes
in millimetres plus a report explaining what happened. The report is not
optional polish. Shop-floor CAD files are messy, and an importer that returns
"14 shapes" without saying that it also failed to close 31 segments and skipped
a layer is actively dangerous — the operator cuts a sheet with a missing hole.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from ..geometry.primitives import Shape

__all__ = [
    "ImportOptions",
    "ImportReport",
    "ImportedGeometry",
    "UNIT_TO_MM",
    "resolve_unit_scale",
    "ImportError_",
]


class ImportError_(RuntimeError):
    """Raised when a source file cannot be read at all."""


#: Conversion factors into millimetres for every unit GRAINLINE recognises.
UNIT_TO_MM: dict[str, float] = {
    "mm": 1.0,
    "cm": 10.0,
    "m": 1000.0,
    "in": 25.4,
    "ft": 304.8,
    "px": 25.4 / 96.0,  # CSS reference pixel
    "pt": 25.4 / 72.0,
    "pc": 25.4 / 6.0,
}


def resolve_unit_scale(unit: str | None, *, default: str = "mm") -> tuple[str, float]:
    """Return ``(canonical_unit, scale_to_mm)`` for a unit name.

    Raises:
        ValueError: the unit is not one GRAINLINE knows how to convert.
    """
    name = (unit or default).strip().lower()
    aliases = {
        "millimeter": "mm", "millimetre": "mm", "millimeters": "mm", "millimetres": "mm",
        "centimeter": "cm", "centimetre": "cm",
        "meter": "m", "metre": "m",
        "inch": "in", "inches": "in", '"': "in",
        "foot": "ft", "feet": "ft", "'": "ft",
        "pixel": "px", "pixels": "px",
        "point": "pt", "points": "pt",
    }
    name = aliases.get(name, name)
    if name not in UNIT_TO_MM:
        raise ValueError(
            f"unknown unit {unit!r}; expected one of {sorted(UNIT_TO_MM)}"
        )
    return name, UNIT_TO_MM[name]


@dataclass(slots=True)
class ImportOptions:
    """Tunables for reading a CAD file.

    Attributes:
        chord_tolerance: Maximum sagitta in mm when flattening arcs, ellipses
            and splines into line segments. 0.05 mm is finer than any sheet
            cutter can hold and is a safe default.
        weld_tolerance: Endpoint welding distance in mm for loop stitching.
        simplify_tolerance: Douglas-Peucker tolerance in mm. Zero keeps every
            flattened vertex; 0.01 typically halves vertex count at no visible
            cost and materially speeds up irregular nesting.
        min_area: Discard shapes below this area in mm^2. Filters out dimension
            ticks and stray marks that would otherwise nest as tiny parts.
        min_hole_area: Discard holes below this area in mm^2.
        include_layers: When set, only these CAD layers are read.
        exclude_layers: Layers to ignore. Shops routinely keep dimensions,
            annotations and construction lines on dedicated layers.
        unit_override: Force a source unit instead of trusting file metadata.
        default_unit: Unit assumed when the file declares none.
    """

    chord_tolerance: float = 0.05
    weld_tolerance: float = 1e-3
    simplify_tolerance: float = 0.0
    min_area: float = 1.0
    min_hole_area: float = 0.0
    include_layers: frozenset[str] | None = None
    exclude_layers: frozenset[str] = field(default_factory=frozenset)
    unit_override: str | None = None
    default_unit: str = "mm"

    def layer_allowed(self, layer: str) -> bool:
        """True when geometry on ``layer`` should be imported.

        Comparison is case-insensitive because AutoCAD layer names are, and a
        shop that types ``dimensions`` should not silently keep importing a
        layer called ``DIMENSIONS``.
        """
        name = (layer or "").strip().lower()
        if self.include_layers is not None:
            return name in {s.lower() for s in self.include_layers}
        return name not in {s.lower() for s in self.exclude_layers}


@dataclass(slots=True)
class ImportReport:
    """What the importer saw, kept and threw away."""

    source: str = ""
    unit: str = "mm"
    scale_to_mm: float = 1.0
    entities_read: int = 0
    entities_skipped: Counter[str] = field(default_factory=Counter)
    layers_seen: set[str] = field(default_factory=set)
    rings_closed: int = 0
    runs_unclosed: int = 0
    shapes_built: int = 0
    shapes_dropped_small: int = 0
    warnings: list[str] = field(default_factory=list)

    def warn(self, message: str) -> None:
        """Record a non-fatal problem for the operator to review."""
        if message not in self.warnings:
            self.warnings.append(message)

    @property
    def is_clean(self) -> bool:
        """True when nothing was skipped, dropped or left unclosed."""
        return (
            not self.warnings
            and self.runs_unclosed == 0
            and not self.entities_skipped
            and self.shapes_dropped_small == 0
        )

    def summary(self) -> str:
        """One-line human summary, used by the CLI and web UI."""
        bits = [
            f"{self.shapes_built} shape(s)",
            f"{self.entities_read} entities",
            f"unit={self.unit}",
        ]
        if self.runs_unclosed:
            bits.append(f"{self.runs_unclosed} UNCLOSED")
        if self.shapes_dropped_small:
            bits.append(f"{self.shapes_dropped_small} below min area")
        if self.entities_skipped:
            skipped = ", ".join(f"{k}x{v}" for k, v in sorted(self.entities_skipped.items()))
            bits.append(f"skipped: {skipped}")
        return " | ".join(bits)


@dataclass(slots=True)
class ImportedGeometry:
    """Validated shapes in millimetres plus the report describing the import."""

    shapes: list[Shape] = field(default_factory=list)
    report: ImportReport = field(default_factory=ImportReport)

    def __len__(self) -> int:
        return len(self.shapes)

    @property
    def total_area(self) -> float:
        """Combined material area of every imported shape, in mm^2."""
        return sum(s.area for s in self.shapes)

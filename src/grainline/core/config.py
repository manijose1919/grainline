"""Job files: the declarative format a shop actually edits.

A job is described in TOML or JSON and references part geometry by path. TOML is
the primary format because shop staff hand-edit these files and TOML tolerates
comments and trailing commas, which JSON does not. Both are parsed with the
standard library — no YAML dependency, one less thing to install on a machine
that may never see the internet again after commissioning.

Validation is deliberately strict and the errors are deliberately verbose. The
person reading them is a fabricator at a workstation, not a developer with a
debugger, and "unknown field 'spacing' — did you mean 'part_spacing'?" saves a
support call that "ValidationError" does not.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .geometry.primitives import Shape
from .io.base import ImportOptions
from .io.reader import read_geometry
from .model.job import FREE_ROTATIONS, Job, NestConfig
from .model.part import GrainConstraint, Part
from .model.stock import Stock

__all__ = [
    "JobFile",
    "PartSpec",
    "StockSpec",
    "ConfigSpec",
    "ImportSpec",
    "load_job",
    "JobFileError",
]


class JobFileError(ValueError):
    """Raised when a job file is malformed or references unreadable geometry."""


class _Strict(BaseModel):
    """Base model that refuses unknown fields.

    Silently ignoring a misspelled key is the worst possible behaviour here: the
    operator believes they set a 3.2 mm kerf, the software uses 0.2, and the
    parts come off the machine undersized.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ImportSpec(_Strict):
    """Geometry import tunables, applied to every part unless overridden."""

    chord_tolerance: float = Field(0.05, gt=0.0, le=10.0)
    weld_tolerance: float = Field(1e-3, gt=0.0, le=10.0)
    simplify_tolerance: float = Field(0.0, ge=0.0, le=10.0)
    min_area: float = Field(1.0, ge=0.0)
    min_hole_area: float = Field(0.0, ge=0.0)
    include_layers: list[str] | None = None
    exclude_layers: list[str] = Field(default_factory=list)
    unit: str | None = None
    default_unit: str = "mm"

    def to_options(self) -> ImportOptions:
        return ImportOptions(
            chord_tolerance=self.chord_tolerance,
            weld_tolerance=self.weld_tolerance,
            simplify_tolerance=self.simplify_tolerance,
            min_area=self.min_area,
            min_hole_area=self.min_hole_area,
            include_layers=(
                frozenset(self.include_layers) if self.include_layers else None
            ),
            exclude_layers=frozenset(self.exclude_layers),
            unit_override=self.unit,
            default_unit=self.default_unit,
        )


class ConfigSpec(_Strict):
    """Machine and process settings."""

    kerf: float = Field(0.2, ge=0.0, le=100.0)
    part_spacing: float = Field(2.0, ge=0.0, le=1000.0)
    sheet_margin: float = Field(5.0, ge=0.0, le=1000.0)
    allow_rotation: bool = True
    rotations: list[float] = Field(default_factory=lambda: list(FREE_ROTATIONS))
    strategy: str = "guillotine"
    seed: int = 0
    time_limit_s: float = Field(0.0, ge=0.0)
    sort_key: Literal["area", "longest_side", "perimeter", "height"] = "area"
    allow_part_in_hole: bool = False
    micro_joint_width: float = Field(0.0, ge=0.0, le=20.0)
    material_thickness: float = Field(3.0, gt=0.0, le=500.0)

    @field_validator("rotations")
    @classmethod
    def _non_empty(cls, value: list[float]) -> list[float]:
        if not value:
            raise ValueError("rotations must list at least one angle")
        return value

    def to_config(self) -> NestConfig:
        return NestConfig(
            kerf=self.kerf,
            part_spacing=self.part_spacing,
            sheet_margin=self.sheet_margin,
            allow_rotation=self.allow_rotation,
            rotations=tuple(self.rotations),
            strategy=self.strategy,
            seed=self.seed,
            time_limit_s=self.time_limit_s,
            sort_key=self.sort_key,
            allow_part_in_hole=self.allow_part_in_hole,
            micro_joint_width=self.micro_joint_width,
            material_thickness=self.material_thickness,
        )


class StockSpec(_Strict):
    """One grade of sheet material."""

    id: str = Field(min_length=1)
    width: float = Field(gt=0.0)
    height: float = Field(gt=0.0)
    quantity: int | None = Field(None, ge=0)
    cost: float = Field(0.0, ge=0.0)
    material: str = ""
    trim_margin: float = Field(0.0, ge=0.0)

    def to_stock(self) -> Stock:
        return Stock(
            id=self.id,
            width=self.width,
            height=self.height,
            quantity=self.quantity,
            cost=self.cost,
            material=self.material,
            trim_margin=self.trim_margin,
        )


class PartSpec(_Strict):
    """One part, referencing its geometry by file path.

    Attributes:
        select: What to do when the referenced file contains several distinct
            shapes. ``largest`` takes only the biggest — right for a drawing
            that includes a title block or border. ``all`` nests every shape,
            suffixing ids — right for a file that is a whole kit of parts.
    """

    id: str = Field(min_length=1)
    file: str = Field(min_length=1)
    quantity: int = Field(1, ge=1)
    grain: Literal["free", "fixed", "bidirectional"] = "free"
    allow_mirror: bool = False
    priority: int = 0
    material: str = ""
    select: Literal["largest", "all"] = "largest"
    import_: ImportSpec | None = Field(None, alias="import")

    model_config = ConfigDict(
        extra="forbid", str_strip_whitespace=True, populate_by_name=True
    )


class JobFile(_Strict):
    """The whole job document."""

    name: str = "untitled"
    config: ConfigSpec = Field(default_factory=ConfigSpec)
    import_: ImportSpec = Field(default_factory=ImportSpec, alias="import")
    stock: list[StockSpec] = Field(default_factory=list)
    parts: list[PartSpec] = Field(default_factory=list)

    model_config = ConfigDict(
        extra="forbid", str_strip_whitespace=True, populate_by_name=True
    )

    def to_job(self, base_dir: Path) -> Job:
        """Resolve geometry references and build a runnable :class:`Job`.

        Args:
            base_dir: Directory that relative ``file`` paths resolve against —
                normally the job file's own directory, so a job folder can be
                copied between machines intact.

        Raises:
            JobFileError: A referenced file is missing, unreadable, or contains
                no usable geometry.
        """
        parts: list[Part] = []

        for spec in self.parts:
            options = (spec.import_ or self.import_).to_options()
            path = Path(spec.file)
            if not path.is_absolute():
                path = base_dir / path

            try:
                imported = read_geometry(path, options)
            except Exception as exc:  # noqa: BLE001 - surfaced with context below
                raise JobFileError(
                    f"part {spec.id!r}: could not read {path}: {exc}"
                ) from exc

            if not imported.shapes:
                raise JobFileError(
                    f"part {spec.id!r}: {path} contained no usable geometry "
                    f"({imported.report.summary()})"
                )

            shapes: list[tuple[str, Shape]]
            if spec.select == "all":
                shapes = [
                    (f"{spec.id}-{i + 1}", s) for i, s in enumerate(imported.shapes)
                ]
            else:
                shapes = [(spec.id, imported.shapes[0])]

            for part_id, shape in shapes:
                parts.append(
                    Part(
                        id=part_id,
                        shape=shape,
                        quantity=spec.quantity,
                        grain=GrainConstraint(spec.grain),
                        allow_mirror=spec.allow_mirror,
                        priority=spec.priority,
                        material=spec.material,
                        source=str(path),
                    )
                )

        return Job(
            parts=parts,
            stock=[s.to_stock() for s in self.stock],
            config=self.config.to_config(),
            name=self.name,
        )


def _parse_document(path: Path) -> dict[str, Any]:
    """Read a TOML or JSON job document into a plain dictionary."""
    suffix = path.suffix.lower()
    raw = path.read_bytes()

    if suffix == ".toml":
        import tomllib

        try:
            return tomllib.loads(raw.decode("utf-8"))
        except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
            raise JobFileError(f"{path}: invalid TOML: {exc}") from exc

    if suffix in {".json", ".jsonc"}:
        try:
            return json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise JobFileError(f"{path}: invalid JSON: {exc}") from exc

    raise JobFileError(
        f"{path}: unsupported job file type {suffix!r}; expected .toml or .json"
    )


def _humanise_validation_error(path: Path, exc: Exception) -> JobFileError:
    """Turn a pydantic ValidationError into something a fabricator can act on."""
    from pydantic import ValidationError

    if not isinstance(exc, ValidationError):
        return JobFileError(f"{path}: {exc}")

    lines = [f"{path}: {exc.error_count()} problem(s) in the job file:"]
    for error in exc.errors():
        location = ".".join(str(p) for p in error["loc"]) or "(root)"
        message = error["msg"]
        if error["type"] == "extra_forbidden":
            message = "unknown setting - check the spelling against the docs"
        lines.append(f"  - {location}: {message}")
    return JobFileError("\n".join(lines))


def load_job(path: str | Path) -> Job:
    """Load and validate a job file, resolving all referenced geometry.

    Raises:
        JobFileError: The file is missing, malformed, or references geometry
            that cannot be imported.
    """
    file_path = Path(path)
    if not file_path.is_file():
        raise JobFileError(f"job file not found: {file_path}")

    document = _parse_document(file_path)

    try:
        job_file = JobFile.model_validate(document)
    except Exception as exc:  # noqa: BLE001 - converted to a readable report
        raise _humanise_validation_error(file_path, exc) from exc

    if not job_file.parts:
        raise JobFileError(f"{file_path}: job defines no [[parts]]")
    if not job_file.stock:
        raise JobFileError(f"{file_path}: job defines no [[stock]]")

    return job_file.to_job(file_path.parent.resolve())

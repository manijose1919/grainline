"""Format dispatch for geometry import."""

from __future__ import annotations

from pathlib import Path as FilePath

from .base import ImportOptions, ImportedGeometry, ImportError_

__all__ = ["read_geometry", "SUPPORTED_SUFFIXES"]

#: File extensions the free core can import.
SUPPORTED_SUFFIXES: frozenset[str] = frozenset({".dxf", ".svg"})


def read_geometry(
    path: str | FilePath, options: ImportOptions | None = None
) -> ImportedGeometry:
    """Import geometry from a DXF or SVG file, chosen by extension.

    Raises:
        ImportError_: The extension is unsupported or the file cannot be read.
    """
    file_path = FilePath(path)
    suffix = file_path.suffix.lower()

    if suffix == ".dxf":
        from .dxf_reader import read_dxf

        return read_dxf(file_path, options)
    if suffix == ".svg":
        from .svg_reader import read_svg

        return read_svg(file_path, options)

    raise ImportError_(
        f"unsupported file type {suffix!r}; expected one of "
        f"{sorted(SUPPORTED_SUFFIXES)}"
    )

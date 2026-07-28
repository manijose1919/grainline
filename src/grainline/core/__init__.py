"""The GRAINLINE free core: geometry, import/export, models and rectangular nesting.

This subpackage is the standalone Free-tier product. It must never import from
``grainline.premium`` or ``grainline.pro``; commercial capability arrives only
through :mod:`grainline.core.registry`, which those modules populate at import
time.
"""

from __future__ import annotations

__all__: list[str] = []

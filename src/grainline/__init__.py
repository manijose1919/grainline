"""GRAINLINE â€” irregular-shape nesting and material-yield engine for sheet-goods job shops.

The package is deliberately split into a standalone free core and two isolated
commercial modules:

``grainline.core``
    The complete Free-tier product. Import, model, rectangular nesting, export
    and reporting. It has **no** knowledge that ``premium`` or ``pro`` exist and
    must remain fully functional when those packages are absent from the
    distribution.

``grainline.premium``
    Irregular (no-fit-polygon) nesting, rotation search and mixed stock.
    Self-registers into :mod:`grainline.core.registry` on import.

``grainline.pro``
    Remnant inventory ledger, cut-path sequencing, multi-machine assignment and
    the headless REST API. Also self-registering.

The dependency arrow points *inwards only*: commercial modules depend on core,
never the reverse.
"""

from __future__ import annotations

__version__ = "1.0.0"
__all__ = ["__version__"]

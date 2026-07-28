"""Stock: the sheets, boards or remnants a job may be cut from."""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["Stock", "UNLIMITED"]

#: Sentinel for stock a shop holds in effectively unlimited quantity - a
#: standard 4x8 sheet they can always buy more of. Modelled as ``None`` rather
#: than a huge integer so "how many did we consume" stays truthful.
UNLIMITED: None = None


@dataclass(frozen=True, slots=True)
class Stock:
    """A grade of sheet material available to the nester.

    Attributes:
        id: Stable identifier, e.g. ``3mm-acrylic-2440x1220``.
        width: Full sheet width in mm.
        height: Full sheet height in mm.
        quantity: How many sheets are on hand. ``None`` means unlimited.
        cost: Cost of one whole sheet, in the shop's currency. Drives the costed
            quote in the Pro tier and the savings figure everywhere else.
        material: Material key. Only parts with a matching material are nested
            onto this stock.
        trim_margin: Unusable border in mm. Rolled goods have a damaged selvedge
            and saw-cut panels have an out-of-square edge; both must be excluded
            from the usable area or the nest will not physically fit.
        is_remnant: True when this is a tracked offcut rather than a full sheet.
            The free core never sets this; the Pro remnant ledger does.
    """

    id: str
    width: float
    height: float
    quantity: int | None = UNLIMITED
    cost: float = 0.0
    material: str = ""
    trim_margin: float = 0.0
    is_remnant: bool = False

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("stock id must be a non-empty string")
        if self.width <= 0 or self.height <= 0:
            raise ValueError(
                f"stock {self.id!r}: dimensions must be positive, "
                f"got {self.width}x{self.height}"
            )
        if self.quantity is not None and self.quantity < 0:
            raise ValueError(f"stock {self.id!r}: quantity cannot be negative")
        if self.trim_margin < 0:
            raise ValueError(f"stock {self.id!r}: trim_margin cannot be negative")
        if self.usable_width <= 0 or self.usable_height <= 0:
            raise ValueError(
                f"stock {self.id!r}: trim_margin {self.trim_margin} consumes the "
                f"entire {self.width}x{self.height} sheet"
            )

    @property
    def area(self) -> float:
        """Full sheet area in mm^2. Yield is measured against this, because a
        shop pays for the whole sheet regardless of how much of it is usable."""
        return self.width * self.height

    @property
    def usable_width(self) -> float:
        """Width available after trimming both edges."""
        return self.width - 2.0 * self.trim_margin

    @property
    def usable_height(self) -> float:
        """Height available after trimming both edges."""
        return self.height - 2.0 * self.trim_margin

    @property
    def usable_area(self) -> float:
        """Area available after trimming, in mm^2."""
        return self.usable_width * self.usable_height

    @property
    def cost_per_mm2(self) -> float:
        """Sheet cost divided by full sheet area. Zero when no cost is set."""
        return self.cost / self.area if self.area > 0 else 0.0

    def accepts(self, material: str) -> bool:
        """True when a part of the given material may be nested on this stock.

        An empty material on either side is treated as a wildcard so that simple
        single-material jobs need no configuration at all.
        """
        if not self.material or not material:
            return True
        return self.material.strip().lower() == material.strip().lower()

    def with_quantity(self, quantity: int | None) -> Stock:
        """Return a copy with a different quantity on hand."""
        return Stock(
            id=self.id,
            width=self.width,
            height=self.height,
            quantity=quantity,
            cost=self.cost,
            material=self.material,
            trim_margin=self.trim_margin,
            is_remnant=self.is_remnant,
        )

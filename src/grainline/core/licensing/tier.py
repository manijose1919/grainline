"""Product tiers and their ordering.

This lives in ``core`` rather than alongside the commercial modules on purpose.
The free build needs to *understand* that higher tiers exist so it can explain
what an upgrade would unlock — it simply has nothing installed to unlock.
"""

from __future__ import annotations

from enum import IntEnum

__all__ = ["Tier"]


class Tier(IntEnum):
    """Ordered product tiers.

    ``IntEnum`` rather than ``Enum`` because the comparison ``tier >= required``
    is the single most common operation in the gating code, and spelling it as
    an integer comparison keeps that code honest and readable.
    """

    FREE = 0
    PREMIUM = 1
    PRO = 2

    @classmethod
    def parse(cls, value: str | int | None) -> "Tier":
        """Coerce a tier name or ordinal into a :class:`Tier`.

        Unknown values resolve to ``FREE``. Failing closed is deliberate: a
        corrupt or unrecognised licence must never grant more capability than it
        proves, and a typo in a config file must not silently unlock the Pro
        kernel.
        """
        if value is None:
            return cls.FREE
        if isinstance(value, int) and not isinstance(value, bool):
            try:
                return cls(value)
            except ValueError:
                return cls.FREE
        name = str(value).strip().upper()
        return cls.__members__.get(name, cls.FREE)

    @property
    def label(self) -> str:
        """Display name, e.g. ``Pro``."""
        return self.name.capitalize()

"""The capability registry: how commercial modules reach the free core.

This module is the dependency-inversion point that makes the open-core split
physically real rather than a naming convention.

``core`` **never imports** ``premium`` or ``pro``. Instead those packages, when
present and imported, call :func:`register_strategy` to add themselves to a
table that ``core`` owns. The free build can therefore ship with the commercial
source deleted outright — no stub modules, no ``try: import`` guards scattered
through the codebase, no dead branches. The registry is simply smaller.

Discovery is explicit rather than magical: :func:`discover` attempts to import
the known commercial package names and shrugs off ``ImportError``. That keeps
the mechanism inspectable — a support engineer can read one function and know
exactly what is loaded and why.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, Iterable

from .licensing.tier import Tier

if TYPE_CHECKING:  # pragma: no cover
    from .nesting.base import NestingStrategy

__all__ = [
    "Capability",
    "CapabilityError",
    "TierRequiredError",
    "register_strategy",
    "get_strategy",
    "available_strategies",
    "strategies_for_tier",
    "discover",
    "reset",
    "COMMERCIAL_PACKAGES",
]

#: Package names probed by :func:`discover`. Absent packages are not an error;
#: that absence *is* the free build.
COMMERCIAL_PACKAGES: tuple[str, ...] = ("grainline.premium", "grainline.pro")


class CapabilityError(RuntimeError):
    """Raised when a requested capability does not exist at all."""


class TierRequiredError(CapabilityError):
    """Raised when a capability exists but the active licence does not cover it.

    Carries the required tier so callers can render a precise upgrade prompt
    rather than a generic "not available".
    """

    def __init__(self, name: str, required: Tier, active: Tier) -> None:
        self.capability = name
        self.required = required
        self.active = active
        super().__init__(
            f"{name!r} requires the {required.label} tier; "
            f"the active licence is {active.label}"
        )


@dataclass(frozen=True, slots=True)
class Capability:
    """A registered nesting strategy and the tier it belongs to."""

    name: str
    tier: Tier
    factory: Callable[[], "NestingStrategy"]
    summary: str = ""
    #: Short marketing line shown when a free user asks for a gated strategy.
    upgrade_hint: str = ""


_STRATEGIES: dict[str, Capability] = {}
_DISCOVERED = False


def register_strategy(
    name: str,
    factory: Callable[[], "NestingStrategy"],
    *,
    tier: Tier = Tier.FREE,
    summary: str = "",
    upgrade_hint: str = "",
    replace: bool = False,
) -> None:
    """Register a nesting strategy under ``name``.

    Args:
        name: Lookup key, e.g. ``guillotine`` or ``nfp``.
        factory: Zero-argument callable returning a strategy instance. A factory
            rather than an instance so that strategies holding per-run state
            cannot leak it between jobs.
        tier: Minimum tier required to use this strategy.
        summary: One-line description for ``grainline strategies``.
        upgrade_hint: Sales copy shown when a lower tier requests this.
        replace: Permit overwriting an existing registration. Off by default so
            that a name collision between two modules is a loud failure rather
            than a silent behaviour change.

    Raises:
        ValueError: The name is already registered and ``replace`` is false.
    """
    if not name:
        raise ValueError("strategy name must be a non-empty string")
    if name in _STRATEGIES and not replace:
        raise ValueError(
            f"strategy {name!r} is already registered by "
            f"{_STRATEGIES[name].tier.label}; pass replace=True to override"
        )
    _STRATEGIES[name] = Capability(
        name=name,
        tier=tier,
        factory=factory,
        summary=summary,
        upgrade_hint=upgrade_hint,
    )


#: Package holding the always-present free strategy. Imported by
#: :func:`discover` before anything else, because the registry is the component
#: responsible for knowing what exists, and the free strategy always exists.
CORE_STRATEGY_PACKAGE = "grainline.core.nesting"


def discover(force: bool = False) -> list[str]:
    """Import the strategy modules so they can self-register.

    The free strategy is imported first and unconditionally. Skipping it is a
    subtle and user-visible bug: a caller that touches the registry *before*
    anything else has imported :mod:`grainline.core.nesting` sees an empty
    registry. ``grainline nest`` happens to work because ``nest()`` lives in
    that package, but ``grainline strategies`` queries the registry directly
    and would render an empty table.

    Commercial packages are then probed. Their absence is not an error - that
    absence *is* the free build.

    Args:
        force: Re-run discovery even if it has already been performed.

    Returns:
        The package names that were successfully imported.
    """
    global _DISCOVERED
    if _DISCOVERED and not force:
        return []

    loaded: list[str] = []

    # Imported at call time rather than module scope: core.nesting imports back
    # into this module to register itself, and a module-level import here would
    # be circular.
    try:
        importlib.import_module(CORE_STRATEGY_PACKAGE)
        loaded.append(CORE_STRATEGY_PACKAGE)
    except ImportError:  # pragma: no cover - the core package is always present
        pass

    for package in COMMERCIAL_PACKAGES:
        try:
            importlib.import_module(package)
        except ImportError:
            # Expected in the free build. Not a warning, not an error.
            continue
        loaded.append(package)

    _DISCOVERED = True
    return loaded


def available_strategies() -> dict[str, Capability]:
    """Every registered strategy, regardless of tier."""
    discover()
    return dict(_STRATEGIES)


def strategies_for_tier(tier: Tier) -> dict[str, Capability]:
    """Strategies usable at ``tier`` or below."""
    return {
        name: cap for name, cap in available_strategies().items() if cap.tier <= tier
    }


def get_strategy(name: str, *, tier: Tier = Tier.FREE) -> "NestingStrategy":
    """Instantiate a registered strategy, enforcing the tier gate.

    Args:
        name: Strategy key.
        tier: The tier the active licence grants.

    Raises:
        CapabilityError: No strategy is registered under ``name``.
        TierRequiredError: The strategy exists but sits above ``tier``.
    """
    registry = available_strategies()
    capability = registry.get(name)

    if capability is None:
        known = ", ".join(sorted(registry)) or "none"
        raise CapabilityError(
            f"unknown nesting strategy {name!r}; registered strategies: {known}"
        )

    if capability.tier > tier:
        raise TierRequiredError(name, capability.tier, tier)

    return capability.factory()


def reset() -> None:
    """Clear the registry. Test-support only.

    Production code must never call this: the registry is process-global by
    design, and clearing it mid-run would strand a partially configured job.
    """
    global _DISCOVERED
    _STRATEGIES.clear()
    _DISCOVERED = False

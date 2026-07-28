"""Nesting strategy contract, the Free-tier guillotine packer, and the entry point.

Importing this package registers the free strategy. Commercial strategies are
registered by their own packages when installed; nothing here knows about them.
"""

from __future__ import annotations

from ..licensing.tier import Tier
from ..model.job import Job
from ..model.result import NestResult
from .base import (
    NestingError,
    NestingStrategy,
    Orientation,
    OrientationCache,
    iter_sheets,
    sort_instances,
)
from .guillotine import FreeRect, GuillotineNester
from .guillotine import register as _register_guillotine

__all__ = [
    "FreeRect",
    "GuillotineNester",
    "NestingError",
    "NestingStrategy",
    "Orientation",
    "OrientationCache",
    "iter_sheets",
    "nest",
    "sort_instances",
]

_register_guillotine()


def nest(job: Job, *, tier: Tier = Tier.FREE) -> NestResult:
    """Nest ``job`` using the strategy named in its config.

    This is the single entry point the CLI, web UI and REST API all call. Tier
    resolution happens in the registry, so no caller ever branches on licence
    state — they ask for a strategy by name and either get it or get a
    :class:`~grainline.core.registry.TierRequiredError` carrying an exact
    upgrade prompt.

    Args:
        job: The parts, stock and machine settings.
        tier: The tier the active licence grants.

    Raises:
        NestingError: The job is structurally invalid.
        CapabilityError: The requested strategy is not registered.
        TierRequiredError: The strategy sits above ``tier``.
    """
    from ..registry import get_strategy

    problems = job.validate()
    fatal = [p for p in problems if "has no parts" in p or "has no stock" in p]
    if fatal:
        raise NestingError("; ".join(fatal))

    strategy = get_strategy(job.config.strategy, tier=tier)
    result = strategy.nest(job)
    # Non-fatal validation findings (a part too big for any sheet, a duplicate
    # id) belong in the report, not in an exception: the rest of the job still
    # nests and the shop still wants the sheet.
    result.notes.extend(p for p in problems if p not in fatal)
    return result

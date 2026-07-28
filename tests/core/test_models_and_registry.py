"""Tests for the domain model's validation rules and the capability registry."""

from __future__ import annotations

import pytest

from grainline.core.geometry.primitives import Shape
from grainline.core.licensing.tier import Tier
from grainline.core.model.job import FREE_ROTATIONS, Job, NestConfig
from grainline.core.model.part import GrainConstraint, Part
from grainline.core.model.stock import Stock
from grainline.core.registry import (
    CapabilityError,
    TierRequiredError,
    available_strategies,
    get_strategy,
    register_strategy,
    strategies_for_tier,
)

pytestmark = pytest.mark.free


# --- Part ------------------------------------------------------------------


def test_part_rejects_zero_quantity():
    with pytest.raises(ValueError, match="quantity must be >= 1"):
        Part(id="p", shape=Shape.rectangle(10.0, 10.0), quantity=0)


def test_part_rejects_empty_id():
    with pytest.raises(ValueError, match="non-empty"):
        Part(id="", shape=Shape.rectangle(10.0, 10.0))


def test_part_instances_are_uniquely_keyed():
    part = Part(id="bracket", shape=Shape.rectangle(10.0, 10.0), quantity=3)
    keys = [i.key for i in part.instances()]
    assert keys == ["bracket#0", "bracket#1", "bracket#2"]


def test_total_area_scales_with_quantity():
    part = Part(id="p", shape=Shape.rectangle(10.0, 20.0), quantity=5)
    assert part.total_area == pytest.approx(1000.0)


@pytest.mark.parametrize(
    "grain,expected",
    [
        (GrainConstraint.FREE, (0.0, 90.0, 180.0, 270.0)),
        (GrainConstraint.FIXED, (0.0,)),
        (GrainConstraint.BIDIRECTIONAL, (0.0, 180.0)),
    ],
)
def test_grain_filters_rotations(grain, expected):
    part = Part(id="p", shape=Shape.rectangle(10.0, 10.0), grain=grain)
    assert part.permitted_angles(FREE_ROTATIONS) == expected


def test_bidirectional_grain_never_returns_empty():
    """A grain filter that removes every candidate must fall back, not crash."""
    part = Part(id="p", shape=Shape.rectangle(10.0, 10.0),
                grain=GrainConstraint.BIDIRECTIONAL)
    assert part.permitted_angles((90.0, 270.0)) == (0.0,)


# --- Stock -----------------------------------------------------------------


def test_stock_rejects_nonpositive_dimensions():
    with pytest.raises(ValueError, match="must be positive"):
        Stock(id="s", width=0.0, height=100.0)


def test_stock_rejects_trim_that_consumes_the_sheet():
    with pytest.raises(ValueError, match="consumes the entire"):
        Stock(id="s", width=100.0, height=100.0, trim_margin=60.0)


def test_usable_area_excludes_trim_on_both_edges():
    stock = Stock(id="s", width=1000.0, height=500.0, trim_margin=25.0)
    assert stock.usable_width == pytest.approx(950.0)
    assert stock.usable_height == pytest.approx(450.0)
    # Yield is still measured against the full sheet the shop paid for.
    assert stock.area == pytest.approx(500_000.0)


def test_material_matching_treats_blank_as_wildcard():
    generic = Stock(id="g", width=10.0, height=10.0)
    alu = Stock(id="a", width=10.0, height=10.0, material="Aluminium")
    assert generic.accepts("anything")
    assert alu.accepts("aluminium")  # case-insensitive
    assert not alu.accepts("acrylic")
    assert alu.accepts("")


def test_cost_per_mm2():
    stock = Stock(id="s", width=1000.0, height=1000.0, cost=250.0)
    assert stock.cost_per_mm2 == pytest.approx(250.0 / 1_000_000.0)


# --- NestConfig ------------------------------------------------------------


def test_config_rejects_negative_kerf():
    with pytest.raises(ValueError, match="kerf cannot be negative"):
        NestConfig(kerf=-1.0)


def test_config_rejects_unknown_sort_key():
    with pytest.raises(ValueError, match="unknown sort_key"):
        NestConfig(sort_key="vibes")


def test_config_requires_at_least_one_rotation():
    with pytest.raises(ValueError, match="at least one rotation"):
        NestConfig(rotations=())


def test_effective_rotations_deduplicate():
    config = NestConfig(rotations=(0.0, 90.0, 360.0, 90.0))
    assert config.effective_rotations == (0.0, 90.0)


def test_effective_rotations_collapse_when_disabled():
    assert NestConfig(allow_rotation=False).effective_rotations == (0.0,)


# --- Job validation --------------------------------------------------------


def test_validate_reports_every_problem_at_once():
    """Fixing three problems one error at a time is a support ticket."""
    job = Job(
        parts=[
            Part(id="dup", shape=Shape.rectangle(10.0, 10.0)),
            Part(id="dup", shape=Shape.rectangle(10.0, 10.0)),
            Part(id="huge", shape=Shape.rectangle(9000.0, 9000.0)),
        ],
        stock=[Stock(id="s", width=1000.0, height=1000.0)],
    )
    problems = job.validate()
    assert any("duplicate part id" in p for p in problems)
    assert any("does not fit" in p for p in problems)
    assert len(problems) >= 2


def test_validate_flags_missing_material_stock():
    job = Job(
        parts=[Part(id="p", shape=Shape.rectangle(10.0, 10.0), material="titanium")],
        stock=[Stock(id="s", width=100.0, height=100.0, material="acrylic")],
    )
    problems = job.validate()
    assert any("no matching stock" in p for p in problems)


def test_validate_accepts_a_sound_job():
    job = Job(
        parts=[Part(id="p", shape=Shape.rectangle(100.0, 100.0), quantity=2)],
        stock=[Stock(id="s", width=1000.0, height=1000.0)],
    )
    assert job.validate() == []


def test_job_aggregates():
    job = Job(
        parts=[
            Part(id="a", shape=Shape.rectangle(10.0, 10.0), quantity=3),
            Part(id="b", shape=Shape.rectangle(20.0, 20.0), quantity=2),
        ],
        stock=[Stock(id="s", width=1000.0, height=1000.0)],
    )
    assert job.total_instances == 5
    assert job.total_part_area == pytest.approx(3 * 100.0 + 2 * 400.0)


# --- Tier ------------------------------------------------------------------


def test_tier_ordering():
    assert Tier.FREE < Tier.PREMIUM < Tier.PRO


@pytest.mark.parametrize(
    "value,expected",
    [
        ("pro", Tier.PRO), ("PREMIUM", Tier.PREMIUM), ("free", Tier.FREE),
        (2, Tier.PRO), (None, Tier.FREE),
        ("enterprise", Tier.FREE),  # unknown must fail closed
        (99, Tier.FREE),
        (True, Tier.FREE),  # bool must not be read as Tier.PREMIUM
    ],
)
def test_tier_parse_fails_closed(value, expected):
    assert Tier.parse(value) is expected


# --- Registry --------------------------------------------------------------


def test_free_guillotine_is_registered():
    assert "guillotine" in available_strategies()
    strategy = get_strategy("guillotine", tier=Tier.FREE)
    assert strategy.name == "guillotine"


def test_registry_is_populated_in_a_cold_process():
    """The free strategy must be discoverable without importing core.nesting first.

    This runs in a **subprocess** on purpose. Asserting it in-process proves
    nothing: by the time this file executes, other tests have already imported
    ``grainline.core.nesting``, which registers the strategy as a side effect.
    The original version of this test passed for exactly that reason while
    ``grainline strategies`` rendered an empty table in a clean process.
    """
    import subprocess
    import sys

    script = (
        "from grainline.core.registry import available_strategies;"
        "names = sorted(available_strategies());"
        "assert 'guillotine' in names, names;"
        "print('OK', names)"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True
    )
    assert completed.returncode == 0, (
        f"cold-start registry lookup failed:\n"
        f"{completed.stdout}\n{completed.stderr}"
    )
    assert "guillotine" in completed.stdout


def test_cli_strategies_command_works_from_cold():
    """``grainline strategies`` must not render an empty table on a fresh run."""
    import subprocess
    import sys

    completed = subprocess.run(
        [sys.executable, "-m", "grainline.cli.main", "strategies"],
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert "guillotine" in completed.stdout


def test_unknown_strategy_lists_what_is_available():
    with pytest.raises(CapabilityError, match="registered strategies"):
        get_strategy("teleport", tier=Tier.PRO)


def test_tier_gate_blocks_and_names_the_requirement():
    from grainline.core.nesting.guillotine import GuillotineNester

    register_strategy(
        "test-premium-only",
        GuillotineNester,
        tier=Tier.PREMIUM,
        summary="test fixture",
        replace=True,
    )
    with pytest.raises(TierRequiredError) as exc:
        get_strategy("test-premium-only", tier=Tier.FREE)

    assert exc.value.required is Tier.PREMIUM
    assert exc.value.active is Tier.FREE
    assert "Premium" in str(exc.value)

    # The same call succeeds once the tier is sufficient.
    assert get_strategy("test-premium-only", tier=Tier.PRO) is not None


def test_strategies_for_tier_filters():
    from grainline.core.nesting.guillotine import GuillotineNester

    register_strategy(
        "test-pro-only", GuillotineNester, tier=Tier.PRO, replace=True
    )
    free = strategies_for_tier(Tier.FREE)
    pro = strategies_for_tier(Tier.PRO)
    assert "test-pro-only" not in free
    assert "test-pro-only" in pro
    assert "guillotine" in free


def test_duplicate_registration_is_loud():
    from grainline.core.nesting.guillotine import GuillotineNester

    register_strategy("test-collide", GuillotineNester, replace=True)
    with pytest.raises(ValueError, match="already registered"):
        register_strategy("test-collide", GuillotineNester)


def test_registry_factory_returns_fresh_instances():
    """Strategies must not share state between jobs."""
    a = get_strategy("guillotine", tier=Tier.FREE)
    b = get_strategy("guillotine", tier=Tier.FREE)
    assert a is not b


def test_core_does_not_import_commercial_packages():
    """The architectural invariant that makes the open-core split real."""
    import ast
    from pathlib import Path

    import grainline.core

    core_root = Path(grainline.core.__file__).parent
    offenders: list[str] = []

    for py_file in core_root.rglob("*.py"):
        tree = ast.parse(py_file.read_text(encoding="utf-8"), str(py_file))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            for name in names:
                if "premium" in name or ".pro" in name or name.endswith("pro"):
                    offenders.append(f"{py_file.name}:{node.lineno} -> {name}")

    assert offenders == [], (
        "grainline.core must never import a commercial module; the registry is "
        f"the only permitted channel. Offenders: {offenders}"
    )

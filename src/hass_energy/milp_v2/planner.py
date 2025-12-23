from __future__ import annotations

from hass_energy.lib.source_resolver.resolver import ValueResolver
from hass_energy.milp_v2.compiler import MilpCompiler
from hass_energy.milp_v2.executor import MilpExecutor
from hass_energy.milp_v2.types import PlanResult
from hass_energy.models.config import EmsConfig
from hass_energy.models.loads import LoadConfig
from hass_energy.models.plant import PlantConfig


class MilpPlanner:
    """Orchestrates compile-then-solve for MILP v2."""

    def __init__(
        self,
        *,
        compiler: MilpCompiler,
        executor: MilpExecutor,
    ) -> None:
        self._compiler = compiler
        self._executor = executor

    def generate_plan(
        self,
        *,
        ems: EmsConfig,
        plant: PlantConfig,
        loads: list[LoadConfig],
        value_resolver: ValueResolver,
    ) -> PlanResult:
        compiled = self._compiler.compile(
            ems=ems,
            plant=plant,
            loads=loads,
            value_resolver=value_resolver,
        )
        return self._executor.solve(compiled)

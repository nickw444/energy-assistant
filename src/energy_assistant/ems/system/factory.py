from __future__ import annotations

from collections import deque
from typing import Any, TypeVar

from energy_assistant.ems.components.base_load import BaseLoadComponent
from energy_assistant.ems.components.battery import BatteryComponent
from energy_assistant.ems.components.ev import EvComponent
from energy_assistant.ems.components.grid import GridComponent
from energy_assistant.ems.components.inverter import InverterComponent
from energy_assistant.ems.components.pv import PvComponent
from energy_assistant.ems.components.switchboard import SwitchboardComponent
from energy_assistant.ems.planning.pricing import PriceSeriesBuilder
from energy_assistant.ems.planning.time_windows import TimeWindowMatcher
from energy_assistant.ems.system.component import EmsComponent
from energy_assistant.ems.system.system import EmsSystem
from energy_assistant.models.config import AppConfig
from energy_assistant.models.plant import (
    BatteryComponentConfig,
    GridComponentConfig,
    InverterComponentConfig,
    LoadComponentConfig,
    PlantComponentConfig,
    PvComponentConfig,
    SwitchboardComponentConfig,
)


class EmsSystemFactory:
    """Builds persistent EMS component definitions from plant config."""

    def __init__(
        self,
        *,
        time_window_matcher: TimeWindowMatcher,
        price_series_builder: PriceSeriesBuilder,
    ) -> None:
        self._time_window_matcher = time_window_matcher
        self._price_series_builder = price_series_builder

    @classmethod
    def create(cls) -> EmsSystemFactory:
        return cls(
            time_window_matcher=TimeWindowMatcher(),
            price_series_builder=PriceSeriesBuilder(),
        )

    def build(self, app_config: AppConfig) -> EmsSystem:
        connection_by_component: dict[str, str | None] = {}
        dependents_by_connection: dict[str, list[str]] = {}
        remaining_connection_count: dict[str, int] = {}
        grid_cfgs: dict[str, GridComponentConfig] = {}

        for component_id, component in app_config.plant.items():
            connection_target_id = self.connection_target_id(component)
            connection_by_component[component_id] = connection_target_id
            remaining_connection_count[component_id] = 0 if connection_target_id is None else 1
            if connection_target_id is not None:
                dependents_by_connection.setdefault(connection_target_id, []).append(component_id)
            if isinstance(component, GridComponentConfig):
                grid_cfgs[component_id] = component

        grid_configs_by_switchboard = self.group_grid_configs_by_switchboard(grid_cfgs)
        ready = deque(
            component_id
            for component_id in app_config.plant
            if remaining_connection_count[component_id] == 0
        )
        components: dict[str, EmsComponent[Any, Any]] = {}

        while ready:
            component_id = ready.popleft()
            component_cfg = app_config.plant[component_id]
            component = self._construct_component(
                component_id=component_id,
                component_cfg=component_cfg,
                components=components,
                connection_by_component=connection_by_component,
                grid_configs_by_switchboard=grid_configs_by_switchboard,
                price_series_builder=self._price_series_builder,
                time_window_matcher=self._time_window_matcher,
            )
            components[component_id] = component
            for dependent_id in dependents_by_connection.get(component_id, []):
                remaining_connection_count[dependent_id] -= 1
                if remaining_connection_count[dependent_id] == 0:
                    ready.append(dependent_id)

        if len(components) != len(app_config.plant):
            unresolved = [key for key in app_config.plant if key not in components]
            raise ValueError(f"unresolved component connections: {unresolved}")

        ordered_components = tuple(components[key] for key in app_config.plant if key in components)
        return EmsSystem(components=components, ordered_components=ordered_components)

    @staticmethod
    def connection_target_id(component: PlantComponentConfig) -> str | None:
        if isinstance(component, SwitchboardComponentConfig):
            return None
        return component.connection

    @classmethod
    def _construct_component(
        cls,
        *,
        component_id: str,
        component_cfg: PlantComponentConfig,
        components: dict[str, EmsComponent[Any, Any]],
        connection_by_component: dict[str, str | None],
        grid_configs_by_switchboard: dict[str, list[GridComponentConfig]],
        price_series_builder: PriceSeriesBuilder,
        time_window_matcher: TimeWindowMatcher,
    ) -> EmsComponent[Any, Any]:
        if isinstance(component_cfg, SwitchboardComponentConfig):
            return SwitchboardComponent(component_id=component_id)
        if isinstance(component_cfg, GridComponentConfig):
            return GridComponent(
                component_id=component_id,
                switchboard=_resolve_component(
                    components,
                    expected_type=SwitchboardComponent,
                    expected_label="switchboard",
                    component_key=component_id,
                    target_key=component_cfg.connection,
                ),
                grid=component_cfg,
                time_window_matcher=time_window_matcher,
                price_series_builder=price_series_builder,
            )
        if isinstance(component_cfg, LoadComponentConfig):
            return BaseLoadComponent(
                component_id=component_id,
                switchboard=_resolve_component(
                    components,
                    expected_type=SwitchboardComponent,
                    expected_label="switchboard",
                    component_key=component_id,
                    target_key=component_cfg.connection,
                ),
                load=component_cfg,
            )
        if isinstance(component_cfg, InverterComponentConfig):
            return InverterComponent(
                component_id=component_id,
                switchboard=_resolve_component(
                    components,
                    expected_type=SwitchboardComponent,
                    expected_label="switchboard",
                    component_key=component_id,
                    target_key=component_cfg.connection,
                ),
                inverter=component_cfg,
            )
        if isinstance(component_cfg, PvComponentConfig):
            return PvComponent(
                component_id=component_id,
                inverter=_resolve_component(
                    components,
                    expected_type=InverterComponent,
                    expected_label="inverter",
                    component_key=component_id,
                    target_key=component_cfg.connection,
                ),
                pv=component_cfg,
            )
        if isinstance(component_cfg, BatteryComponentConfig):
            inverter = _resolve_component(
                components,
                expected_type=InverterComponent,
                expected_label="inverter",
                component_key=component_id,
                target_key=component_cfg.connection,
            )
            inverter_connection_target_id = connection_by_component[component_cfg.connection]
            if inverter_connection_target_id is None:  # pragma: no cover - defensive
                raise ValueError(
                    f"component {component_id} references inverter {component_cfg.connection!r} with no switchboard parent"
                )
            return BatteryComponent(
                component_id=component_id,
                inverter=inverter,
                battery=component_cfg,
                grid_max_export_kw=cls.grid_max_export_kw_from_configs(
                    grid_configs_by_switchboard.get(inverter_connection_target_id, [])
                ),
            )
        return EvComponent(
            component_id=component_id,
            switchboard=_resolve_component(
                components,
                expected_type=SwitchboardComponent,
                expected_label="switchboard",
                component_key=component_id,
                target_key=component_cfg.connection,
            ),
            load=component_cfg,
            grid_export_bias_pct=cls.grid_export_bias_pct_from_configs(
                grid_configs_by_switchboard.get(component_cfg.connection, []),
                price_series_builder=price_series_builder,
            ),
            time_window_matcher=time_window_matcher,
        )


    @staticmethod
    def grid_max_export_kw_from_configs(grids: list[GridComponentConfig]) -> float:
        if not grids:
            return 0.0
        return max(grid.constraints.max_export_kw for grid in grids)

    @staticmethod
    def grid_export_bias_pct_from_configs(
        grids: list[GridComponentConfig],
        *,
        price_series_builder: PriceSeriesBuilder,
    ) -> float:
        if not grids:
            return 0.0
        first_grid = grids[0]
        return price_series_builder.binding_bias_pct(
            binding=first_grid.price_export,
            direction="export",
        )

    @staticmethod
    def group_grid_configs_by_switchboard(
        grid_cfgs: dict[str, GridComponentConfig],
    ) -> dict[str, list[GridComponentConfig]]:
        grouped: dict[str, list[GridComponentConfig]] = {}
        for grid in grid_cfgs.values():
            grouped.setdefault(grid.connection, []).append(grid)
        return grouped


TResolvedComponent = TypeVar("TResolvedComponent", bound=EmsComponent[Any, Any])


def _resolve_component(
    components: dict[str, EmsComponent[Any, Any]],
    *,
    expected_type: type[TResolvedComponent],
    expected_label: str,
    component_key: str,
    target_key: str,
) -> TResolvedComponent:
    try:
        target = components[target_key]
    except KeyError as exc:  # pragma: no cover - defensive
        raise ValueError(
            f"component {component_key} references missing {expected_label} {target_key!r}"
        ) from exc
    if not isinstance(target, expected_type):  # pragma: no cover - defensive
        raise ValueError(
            f"component {component_key} expected {expected_label} {target_key!r} but found {type(target).__name__}"
        )
    return target

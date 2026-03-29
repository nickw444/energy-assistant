from __future__ import annotations

from dataclasses import dataclass

from energy_assistant.ems.inputs.models import AppliedInputRegistry
from energy_assistant.ems.models import LoadComponentPlan
from energy_assistant.ems.parameters import SeriesParameter
from energy_assistant.ems.planning.horizon import Horizon
from energy_assistant.ems.series import interval_series_points
from energy_assistant.ems.topology.connection import Connection
from energy_assistant.ems.topology.graph import GraphElement
from energy_assistant.ems.topology.nodes import Node
from energy_assistant.ems.topology.policies import DirectionalLimit, FixedFlow
from energy_assistant.models.inputs import InputValueKind
from energy_assistant.models.plant import LoadComponentConfig


@dataclass(frozen=True, slots=True)
class BaseLoadSolveState:
    connection: Connection
    base_load_kw: list[float]


class BaseLoadComponent:
    """Fixed baseline plant load (kW) on the AC bus."""

    def __init__(
        self,
        *,
        bus_id: str,
        component_id: str,
        load: LoadComponentConfig,
    ) -> None:
        self.id = str(component_id)
        self.bus_id = str(bus_id)
        self.node_id = self.id
        self.connection_id = f"{self.id}_link"
        self.name = str(load.name)
        self._power_input_key = load.power.key

        self._base_load_kw = SeriesParameter[float](f"{self.id}_kw")

    def update_inputs(
        self,
        *,
        horizon: Horizon,
        inputs: AppliedInputRegistry,
    ) -> None:
        series = inputs.forecast(self._power_input_key, kind=InputValueKind.POWER)
        if len(series) != horizon.num_intervals:
            raise ValueError("Base load series length does not match horizon")
        self._base_load_kw.set(series)

    def graph_elements(
        self,
        *,
        horizon: Horizon,
    ) -> tuple[list[GraphElement], BaseLoadSolveState]:
        base_load_kw = self._base_load_kw.get()

        node = Node(
            horizon=horizon,
            id=self.node_id,
            name=self.name,
            node_role="consumer",
        )
        connection = Connection(
            horizon=horizon,
            id=self.connection_id,
            a_node_id=self.bus_id,
            b_node_id=self.node_id,
            policies={
                "directional_limit": DirectionalLimit(
                    max_a_to_b_kw=None,
                    max_b_to_a_kw=0.0,
                ),
                "fixed_flow": FixedFlow(
                    direction="a_to_b",
                    values_kw=base_load_kw,
                    name=self.id,
                ),
            },
        )
        solve_state = BaseLoadSolveState(connection=connection, base_load_kw=base_load_kw)
        return [node, connection], solve_state

    def build_plan(
        self,
        snapshot_horizon: Horizon,
        *,
        solve_state: BaseLoadSolveState,
    ) -> LoadComponentPlan:
        return LoadComponentPlan(
            power_kw=interval_series_points(snapshot_horizon, solve_state.base_load_kw),
        )

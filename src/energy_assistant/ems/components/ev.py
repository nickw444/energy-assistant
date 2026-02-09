from __future__ import annotations

from collections.abc import Iterator

import pulp

from energy_assistant.ems.milp.context import ConstraintSpec, ObjectiveTerm, value_of
from energy_assistant.ems.models import EvTimestepPlan
from energy_assistant.ems.system.system import ModelSnapshot
from energy_assistant.ems.topology.connection import ConnectionTemplate
from energy_assistant.ems.topology.graph import EnergyGraphModel, EnergyGraphTemplate
from energy_assistant.ems.topology.link_components import DirectionalLimit, GateSeries
from energy_assistant.ems.topology.nodes import StorageNodeTemplate
from energy_assistant.models.loads import ControlledEvLoad, SocIncentive

_EV_SWITCH_ON_THRESHOLD_KW = 0.1


class EvComponent:
    def __init__(
        self,
        *,
        graph: EnergyGraphTemplate,
        switchboard_bus_id: str,
        load: ControlledEvLoad,
        gate_series_key: str,
        connected_bool_key: str,
        realtime_power_kw_key: str,
        initial_soc_kwh_key: str,
        grid_price_bias_pct: float,
    ) -> None:
        self.id = load.id
        self.name = load.name
        self.capacity_kwh = float(load.energy_kwh)
        self.min_power_kw = float(load.min_power_kw)
        self.max_power_kw = float(load.max_power_kw)
        self.switch_penalty = float(load.switch_penalty)
        self.soc_incentives = list(load.soc_incentives)

        self._gate_series_key = str(gate_series_key)
        self._connected_bool_key = str(connected_bool_key)
        self._realtime_power_kw_key = str(realtime_power_kw_key)
        self._initial_soc_kwh_key = str(initial_soc_kwh_key)
        self._grid_price_bias = float(grid_price_bias_pct) / 100.0

        self.node_id = self.id
        self.connection_id = f"ev_{self.id}_link"

        graph.add_storage(
            StorageNodeTemplate(
                id=self.node_id,
                name=self.name,
                capacity_kwh=self.capacity_kwh,
                soc_min_kwh=0.0,
                soc_max_kwh=self.capacity_kwh,
                storage_efficiency=1.0,
                initial_soc_kwh_key=self._initial_soc_kwh_key,
                mode="charge_only",
            )
        )
        graph.add_connection(
            ConnectionTemplate(
                id=self.connection_id,
                a_node_id=str(switchboard_bus_id),
                b_node_id=self.node_id,
                link_components=[
                    DirectionalLimit(max_a_to_b_kw=self.max_power_kw, max_b_to_a_kw=0.0),
                    GateSeries(
                        direction="a_to_b",
                        gate_key=self._gate_series_key,
                        max_kw=self.max_power_kw,
                        name=f"ev_gate_{self.id}",
                    ),
                ],
            )
        )

        needs_charge_on = self.min_power_kw > 0 or self.switch_penalty > 0
        if needs_charge_on:
            graph.add_fragment(
                EvChargeControlTemplate(
                    ev_id=self.id,
                    connection_id=self.connection_id,
                    gate_key=self._gate_series_key,
                    connected_bool_key=self._connected_bool_key,
                    realtime_power_kw_key=self._realtime_power_kw_key,
                    min_power_kw=self.min_power_kw,
                    max_power_kw=self.max_power_kw,
                    switch_penalty=self.switch_penalty,
                )
            )

        if self.soc_incentives:
            graph.add_fragment(
                EvSocIncentivesTemplate(
                    ev_id=self.id,
                    storage_node_id=self.node_id,
                    initial_soc_kwh_key=self._initial_soc_kwh_key,
                    capacity_kwh=self.capacity_kwh,
                    incentives=self.soc_incentives,
                    grid_price_bias=self._grid_price_bias,
                )
            )

    def iter_timestep_plan(self, snapshot: ModelSnapshot) -> Iterator[EvTimestepPlan]:
        conn = snapshot.graph.connections[self.connection_id]
        node = snapshot.graph.storage_nodes[self.node_id]
        connected = bool(snapshot.ctx.inputs.bool(self._connected_bool_key))

        for t in snapshot.ctx.horizon.T:
            charge_kw = value_of(conn.P_a_to_b.get(t))
            soc_kwh = value_of(node.E_by_i.get(t))
            soc_pct = (soc_kwh / float(self.capacity_kwh)) * 100.0 if self.capacity_kwh else None
            yield EvTimestepPlan(
                name=str(self.name),
                charge_kw=charge_kw,
                soc_kwh=soc_kwh,
                soc_pct=soc_pct,
                connected=connected,
            )


class EvChargeControlTemplate:
    def __init__(
        self,
        *,
        ev_id: str,
        connection_id: str,
        gate_key: str,
        connected_bool_key: str,
        realtime_power_kw_key: str,
        min_power_kw: float,
        max_power_kw: float,
        switch_penalty: float,
    ) -> None:
        self.ev_id = str(ev_id)
        self.connection_id = str(connection_id)
        self.gate_key = str(gate_key)
        self.connected_bool_key = str(connected_bool_key)
        self.realtime_power_kw_key = str(realtime_power_kw_key)
        self.min_power_kw = float(min_power_kw)
        self.max_power_kw = float(max_power_kw)
        self.switch_penalty = float(switch_penalty)

    def bind(self, graph: EnergyGraphModel) -> EvChargeControlModel:
        return EvChargeControlModel(graph=graph, template=self)


class EvChargeControlModel:
    def __init__(self, *, graph: EnergyGraphModel, template: EvChargeControlTemplate) -> None:
        ctx = graph.ctx
        conn = graph.connections[template.connection_id]
        gate = ctx.inputs.float_series(template.gate_key)
        connected = bool(ctx.inputs.bool(template.connected_bool_key))
        realtime_power_kw = float(ctx.inputs.float(template.realtime_power_kw_key))

        P_charge = conn.P_a_to_b

        self.charge_on: dict[int, pulp.LpVariable] = pulp.LpVariable.dicts(
            f"Ev_{template.ev_id}_charge_on",
            ctx.horizon.T,
            lowBound=0,
            upBound=1,
            cat="Binary",
        )
        self.switch: dict[int, pulp.LpVariable] = {}

        self._constraints: list[ConstraintSpec] = []
        min_power = float(template.min_power_kw)
        if min_power <= 0 and template.switch_penalty > 0:
            min_power = _EV_SWITCH_ON_THRESHOLD_KW

        # Charge-on gating and min/max logic.
        for t in ctx.horizon.T:
            self._constraints.append(
                ConstraintSpec(
                    f"ev_charge_on_gate_{template.ev_id}_t{t}",
                    self.charge_on[t] <= float(gate[t]),
                )
            )
            if min_power > 0:
                self._constraints.append(
                    ConstraintSpec(
                        f"ev_charge_min_{template.ev_id}_t{t}",
                        P_charge[t] >= min_power * self.charge_on[t],
                    )
                )
            self._constraints.append(
                ConstraintSpec(
                    f"ev_charge_max_{template.ev_id}_t{t}",
                    P_charge[t] <= float(template.max_power_kw) * self.charge_on[t],
                )
            )

        self._objective_terms: list[ObjectiveTerm] = []

        # Switch penalty (absolute on/off transitions), including t0 seeding from realtime state.
        if template.switch_penalty > 0:
            self.switch = pulp.LpVariable.dicts(
                f"Ev_{template.ev_id}_switch",
                list(ctx.horizon.T),
                lowBound=0,
                upBound=1,
            )
            threshold_kw = (
                float(template.min_power_kw)
                if template.min_power_kw > 0
                else _EV_SWITCH_ON_THRESHOLD_KW
            )
            initial_on = 1.0 if connected and realtime_power_kw >= threshold_kw else 0.0
            if 0 in ctx.horizon.T:
                self._constraints.append(
                    ConstraintSpec(
                        f"ev_switch_up_{template.ev_id}_t0",
                        self.switch[0] >= self.charge_on[0] - initial_on,
                    )
                )
                self._constraints.append(
                    ConstraintSpec(
                        f"ev_switch_down_{template.ev_id}_t0",
                        self.switch[0] >= initial_on - self.charge_on[0],
                    )
                )
            for t in ctx.horizon.T:
                if t == 0:
                    continue
                self._constraints.append(
                    ConstraintSpec(
                        f"ev_switch_up_{template.ev_id}_t{t}",
                        self.switch[t] >= self.charge_on[t] - self.charge_on[t - 1],
                    )
                )
                self._constraints.append(
                    ConstraintSpec(
                        f"ev_switch_down_{template.ev_id}_t{t}",
                        self.switch[t] >= self.charge_on[t - 1] - self.charge_on[t],
                    )
                )
            expr = template.switch_penalty * pulp.lpSum(self.switch.values())
            self._objective_terms.append(ObjectiveTerm(expr, name=f"ev_switch:{template.ev_id}"))

    @property
    def constraints(self) -> list[ConstraintSpec]:
        return list(self._constraints)

    @property
    def objective_terms(self) -> list[ObjectiveTerm]:
        return list(self._objective_terms)


class EvSocIncentivesTemplate:
    def __init__(
        self,
        *,
        ev_id: str,
        storage_node_id: str,
        initial_soc_kwh_key: str,
        capacity_kwh: float,
        incentives: list[SocIncentive],
        grid_price_bias: float,
    ) -> None:
        self.ev_id = str(ev_id)
        self.storage_node_id = str(storage_node_id)
        self.initial_soc_kwh_key = str(initial_soc_kwh_key)
        self.capacity_kwh = float(capacity_kwh)
        self.incentives = list(incentives)
        self.grid_price_bias = float(grid_price_bias)

    def bind(self, graph: EnergyGraphModel) -> EvSocIncentivesModel:
        return EvSocIncentivesModel(graph=graph, template=self)


class EvSocIncentivesModel:
    def __init__(self, *, graph: EnergyGraphModel, template: EvSocIncentivesTemplate) -> None:
        ctx = graph.ctx
        node = graph.storage_nodes[template.storage_node_id]

        incentives = sorted(
            template.incentives,
            key=lambda item: float(item.target_soc_pct),
        )
        if not incentives:
            self._constraints = []
            self._objective_terms = []
            return

        initial_soc_kwh = float(ctx.inputs.float(template.initial_soc_kwh_key))
        capacity_kwh = float(template.capacity_kwh)
        terminal_soc = node.E_by_i[ctx.horizon.num_intervals]

        segments: list[tuple[pulp.LpVariable, float]] = []
        prev_target_kwh = 0.0
        for idx, incentive in enumerate(incentives):
            target_pct = float(incentive.target_soc_pct)
            incentive_value = float(incentive.incentive)
            target_kwh = capacity_kwh * target_pct / 100.0
            if target_kwh < prev_target_kwh:
                raise ValueError("EV incentive targets must be non-decreasing")
            available = max(0.0, target_kwh - max(prev_target_kwh, initial_soc_kwh))
            if available > 0:
                seg = pulp.LpVariable(
                    f"E_ev_{template.ev_id}_incentive_{idx}_kwh",
                    lowBound=0,
                    upBound=available,
                )
                segments.append((seg, incentive_value))
            prev_target_kwh = target_kwh

        final_available = max(0.0, capacity_kwh - max(prev_target_kwh, initial_soc_kwh))
        if final_available > 0:
            seg = pulp.LpVariable(
                f"E_ev_{template.ev_id}_incentive_final_kwh",
                lowBound=0,
                upBound=final_available,
            )
            segments.append((seg, 0.0))

        self._constraints: list[ConstraintSpec] = [
            ConstraintSpec(
                f"ev_incentive_total_{template.ev_id}",
                pulp.lpSum(seg for seg, _ in segments) == terminal_soc - initial_soc_kwh,
            )
        ]

        def _apply_export_bias(value: float) -> float:
            bias = float(template.grid_price_bias)
            if bias == 0:
                return value
            if value >= 0:
                return value * (1.0 - bias)
            return value * (1.0 + bias)

        objective_expr = pulp.lpSum(
            -_apply_export_bias(float(incentive)) * seg for seg, incentive in segments
        )
        self._objective_terms: list[ObjectiveTerm] = []
        if segments:
            self._objective_terms.append(
                ObjectiveTerm(
                    objective_expr,
                    name=f"ev_incentives:{template.ev_id}",
                )
            )

    @property
    def constraints(self) -> list[ConstraintSpec]:
        return list(self._constraints)

    @property
    def objective_terms(self) -> list[ObjectiveTerm]:
        return list(self._objective_terms)

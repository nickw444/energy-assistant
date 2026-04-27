from __future__ import annotations

from dataclasses import dataclass

from energy_assistant.ems.components.battery import BatteryComponent
from energy_assistant.ems.topology.ids import NodeId
from energy_assistant.models.plant import BatteryComponentConfig, InputReference, SocValueConfig


@dataclass(frozen=True, slots=True)
class _InverterStub:
    id: str = "inv"
    dc_bus_id: NodeId = NodeId("inv_dc")
    switchboard: object = object()


def _battery_component(*, soc_value: SocValueConfig) -> BatteryComponent:
    return BatteryComponent(
        component_id="battery",
        inverter=_InverterStub(),  # pyright: ignore[reportArgumentType]
        battery=BatteryComponentConfig(
            type="battery",
            connection="inv",
            name="Battery",
            capacity_kwh=13.5,
            storage_efficiency_pct=95.0,
            min_soc_pct=10.0,
            max_soc_pct=100.0,
            reserve_soc_pct=20.0,
            soc_value=soc_value,
            state_of_charge_pct=InputReference(source="battery_soc"),
            realtime_power=InputReference(source="battery_power"),
        ),
        grid_max_export_kw=5.0,
    )


def test_terminal_soc_value_uses_forecast_percentile() -> None:
    component = _battery_component(
        soc_value=SocValueConfig(mode="forecast_percentile", percentile=75.0)
    )
    value = component._terminal_soc_value_per_kwh([0.10, 0.30, 0.70, 1.10])  # pyright: ignore[reportPrivateUsage]
    assert value == 0.8


def test_terminal_soc_value_fixed_mode() -> None:
    component = _battery_component(soc_value=SocValueConfig(mode="fixed", value_per_kwh=0.42))
    value = component._terminal_soc_value_per_kwh([0.10, 0.30])  # pyright: ignore[reportPrivateUsage]
    assert value == 0.42


def test_terminal_soc_value_none_mode() -> None:
    component = _battery_component(soc_value=SocValueConfig(mode="none"))
    value = component._terminal_soc_value_per_kwh([0.10, 0.30])  # pyright: ignore[reportPrivateUsage]
    assert value is None

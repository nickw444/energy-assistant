"""Plant wiring is resolved and validated by :class:`EmsSystemFactory`."""

from __future__ import annotations

import pytest

from energy_assistant.ems.components.grid.price_bindings import PriceBindingApplicator
from energy_assistant.ems.components.lib.time_windows import TimeWindowMatcher
from energy_assistant.ems.system.factory import EmsSystemFactory
from energy_assistant.models.config import AppConfig
from energy_assistant.models.plant import (
    BatteryComponentConfig,
    GridComponentConfig,
    GridConstraintsConfig,
    InputReference,
    InverterComponentConfig,
    PriceBiasFilterConfig,
    PriceBindingConfig,
)

_MINIMAL_SERVER = {"host": "127.0.0.1", "port": 6070, "data_dir": "./data"}
_MINIMAL_HA = {"base_url": "https://hass.example.com", "token": "test-token"}

_GRID_PRICE_IMPORT = {
    "type": "forecast",
    "forecast": {
        "type": "home_assistant",
        "platform": "amber_express",
        "entity": "sensor.price_import",
    },
    "realtime": {
        "type": "home_assistant",
        "entity": "sensor.price_import",
    },
}
_GRID_PRICE_EXPORT = {
    "type": "forecast",
    "forecast": {
        "type": "home_assistant",
        "platform": "amber_express",
        "entity": "sensor.price_export",
    },
    "realtime": {
        "type": "home_assistant",
        "entity": "sensor.price_export",
    },
}


def test_factory_rejects_incompatible_connection_target_type() -> None:
    app_config = AppConfig.model_validate(
        {
            "server": _MINIMAL_SERVER,
            "homeassistant": _MINIMAL_HA,
            "inputs": {
                "batt_soc": {
                    "type": "scalar",
                    "value_kind": "percentage",
                    "source": {"type": "home_assistant", "entity": "sensor.soc"},
                },
                "batt_rt": {
                    "type": "scalar",
                    "value_kind": "power",
                    "source": {"type": "home_assistant", "entity": "sensor.p"},
                },
            },
            "plant": {
                "sb": {"type": "switchboard"},
                "inv": {
                    "type": "inverter",
                    "connection": "sb",
                    "name": "Inv",
                    "peak_power_kw": 5.0,
                },
                "batt": {
                    "type": "battery",
                    "connection": "sb",
                    "name": "Batt",
                    "capacity_kwh": 10.0,
                    "storage_efficiency_pct": 90.0,
                    "min_soc_pct": 0.0,
                    "max_soc_pct": 100.0,
                    "reserve_soc_pct": 10.0,
                    "state_of_charge_pct": 45.0,
                    "realtime_power": {"source": "batt_rt"},
                },
            },
        }
    )

    with pytest.raises(ValueError, match="component batt expected inverter 'sb'"):
        EmsSystemFactory.create().build(app_config)


def test_factory_rejects_missing_connection_target() -> None:
    app_config = AppConfig.model_validate(
        {
            "server": _MINIMAL_SERVER,
            "homeassistant": _MINIMAL_HA,
            "inputs": {
                "grid_price_import": _GRID_PRICE_IMPORT,
                "grid_price_export": _GRID_PRICE_EXPORT,
            },
            "plant": {
                "sb": {"type": "switchboard"},
                "grid": {
                    "type": "grid",
                    "connection": "not_registered",
                    "constraints": {"max_import_kw": 10.0, "max_export_kw": 10.0},
                    "price_import": {"source": "grid_price_import"},
                    "price_export": {"source": "grid_price_export"},
                },
            },
        }
    )

    with pytest.raises(ValueError, match=r"unresolved component connections: \['grid'\]"):
        EmsSystemFactory.create().build(app_config)


def test_factory_resolves_switchboard_references() -> None:
    """Factory resolves validated ``connection`` keys to object references (happy path)."""
    app_config = AppConfig.model_validate(
        {
            "server": _MINIMAL_SERVER,
            "homeassistant": _MINIMAL_HA,
            "inputs": {
                "grid_price_import": _GRID_PRICE_IMPORT,
                "grid_price_export": _GRID_PRICE_EXPORT,
            },
            "plant": {
                "sb": {"type": "switchboard"},
                "grid": {
                    "type": "grid",
                    "connection": "sb",
                    "constraints": {"max_import_kw": 10.0, "max_export_kw": 10.0},
                    "price_import": {"source": "grid_price_import"},
                    "price_export": {"source": "grid_price_export"},
                },
            },
        }
    )
    system = EmsSystemFactory.create().build(app_config)
    assert system.components["grid"].switchboard is system.components["sb"]


def _grid_config(
    *,
    connection: str,
    max_export_kw: float,
    export_bias_pct: float = 0.0,
) -> GridComponentConfig:
    return GridComponentConfig(
        type="grid",
        connection=connection,
        constraints=GridConstraintsConfig(max_import_kw=10.0, max_export_kw=max_export_kw),
        price_import=PriceBindingConfig(source=InputReference(source="grid_import")),
        price_export=PriceBindingConfig(
            source=InputReference(source="grid_export"),
            filters=[PriceBiasFilterConfig(type="bias", bias_pct=export_bias_pct)],
        ),
    )


def test_factory_groups_grid_config_by_switchboard_for_max_export() -> None:
    grouped = EmsSystemFactory.group_grid_configs_by_switchboard(
        {
            "grid_a": _grid_config(connection="sb_a", max_export_kw=4.0),
            "grid_b": _grid_config(connection="sb_a", max_export_kw=11.0),
            "grid_c": _grid_config(connection="sb_b", max_export_kw=7.0),
        }
    )

    assert EmsSystemFactory.grid_max_export_kw_from_configs(grouped["sb_a"]) == 11.0
    assert EmsSystemFactory.grid_max_export_kw_from_configs(grouped["sb_b"]) == 7.0
    assert EmsSystemFactory.grid_max_export_kw_from_configs(grouped.get("sb_missing", [])) == 0.0


def test_factory_uses_first_switchboard_grid_for_export_bias_pct() -> None:
    grouped = EmsSystemFactory.group_grid_configs_by_switchboard(
        {
            # Keep insertion order explicit: first config should define effective export bias.
            "grid_first": _grid_config(
                connection="sb",
                max_export_kw=5.0,
                export_bias_pct=15.0,
            ),
            "grid_second": _grid_config(
                connection="sb",
                max_export_kw=12.0,
                export_bias_pct=3.0,
            ),
        }
    )
    applicator = PriceBindingApplicator()

    expected = applicator.binding_bias_pct(
        binding=grouped["sb"][0].price_export,
        direction="export",
    )
    assert EmsSystemFactory.grid_export_bias_pct_from_configs(
        grouped["sb"],
        price_binding_applicator=applicator,
    ) == expected
    assert (
        EmsSystemFactory.grid_export_bias_pct_from_configs(
            grouped.get("sb_missing", []),
            price_binding_applicator=applicator,
        )
        == 0.0
    )


def test_factory_builds_dependencies_when_plant_order_is_reverse_topology() -> None:
    app_config = AppConfig.model_validate(
        {
            "server": _MINIMAL_SERVER,
            "homeassistant": _MINIMAL_HA,
            "inputs": {
                "grid_price_import": _GRID_PRICE_IMPORT,
                "grid_price_export": _GRID_PRICE_EXPORT,
                "batt_soc": {
                    "type": "scalar",
                    "value_kind": "percentage",
                    "source": {"type": "home_assistant", "entity": "sensor.battery_soc"},
                },
                "batt_rt": {
                    "type": "scalar",
                    "value_kind": "power",
                    "source": {"type": "home_assistant", "entity": "sensor.battery_power"},
                },
            },
            "plant": {
                # Intentionally reverse dependency direction: leaves before roots.
                "battery": {
                    "type": "battery",
                    "connection": "inverter",
                    "name": "Battery",
                    "capacity_kwh": 12.0,
                    "storage_efficiency_pct": 90.0,
                    "min_soc_pct": 5.0,
                    "max_soc_pct": 95.0,
                    "reserve_soc_pct": 20.0,
                    "state_of_charge_pct": {"source": "batt_soc"},
                    "realtime_power": {"source": "batt_rt"},
                },
                "inverter": {
                    "type": "inverter",
                    "connection": "switchboard",
                    "name": "Inverter",
                    "peak_power_kw": 8.0,
                },
                "grid": {
                    "type": "grid",
                    "connection": "switchboard",
                    "constraints": {"max_import_kw": 10.0, "max_export_kw": 7.0},
                    "price_import": {"source": "grid_price_import"},
                    "price_export": {"source": "grid_price_export"},
                },
                "switchboard": {"type": "switchboard"},
            },
        }
    )

    system = EmsSystemFactory.create().build(app_config)

    assert system.components["battery"].inverter is system.components["inverter"]
    assert system.components["inverter"].switchboard is system.components["switchboard"]
    assert system.components["grid"].switchboard is system.components["switchboard"]
    # Assembly order remains deterministic and follows plant declaration order.
    assert tuple(component.id for component in system.ordered_components) == tuple(
        app_config.plant.keys()
    )


def test_factory_raises_when_dependencies_cannot_be_resolved() -> None:
    app_config = AppConfig.model_validate(
        {
            "server": _MINIMAL_SERVER,
            "homeassistant": _MINIMAL_HA,
            "inputs": {
                "batt_soc": {
                    "type": "scalar",
                    "value_kind": "percentage",
                    "source": {"type": "home_assistant", "entity": "sensor.battery_soc"},
                },
                "batt_rt": {
                    "type": "scalar",
                    "value_kind": "power",
                    "source": {"type": "home_assistant", "entity": "sensor.battery_power"},
                },
            },
            "plant": {
                "switchboard": {"type": "switchboard"},
                "inverter": {
                    "type": "inverter",
                    "connection": "switchboard",
                    "name": "Inverter",
                    "peak_power_kw": 8.0,
                },
                "battery": {
                    "type": "battery",
                    "connection": "inverter",
                    "name": "Battery",
                    "capacity_kwh": 12.0,
                    "storage_efficiency_pct": 90.0,
                    "min_soc_pct": 5.0,
                    "max_soc_pct": 95.0,
                    "reserve_soc_pct": 20.0,
                    "state_of_charge_pct": {"source": "batt_soc"},
                    "realtime_power": {"source": "batt_rt"},
                },
            },
        }
    )
    # Break invariants after validation to exercise defensive dependency resolution.
    inverter_cfg = app_config.plant["inverter"]
    battery_cfg = app_config.plant["battery"]
    assert isinstance(inverter_cfg, InverterComponentConfig)
    assert isinstance(battery_cfg, BatteryComponentConfig)
    inverter_cfg.connection = "battery"
    battery_cfg.connection = "inverter"

    with pytest.raises(
        ValueError,
        match=r"unresolved component connections: \['inverter', 'battery'\]",
    ):
        EmsSystemFactory(
            time_window_matcher=TimeWindowMatcher(),
            price_binding_applicator=PriceBindingApplicator(),
        ).build(app_config)

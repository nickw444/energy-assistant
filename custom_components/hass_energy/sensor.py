"""HASS Energy sensors (proof of concept)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import logging
from typing import Any

import aiohttp
import async_timeout
import voluptuous as vol

from homeassistant.components.sensor import PLATFORM_SCHEMA, SensorDeviceClass, SensorEntity
from homeassistant.const import CONF_NAME, CONF_SCAN_INTERVAL
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
    UpdateFailed,
)

from .const import (
    CONF_BASE_URL,
    CONF_ICON,
    CONF_PATH,
    CONF_SENSORS,
    CONF_TIMEOUT,
    CONF_UNIT,
    DEFAULT_BASE_URL,
    DEFAULT_TIMEOUT,
)

_LOGGER = logging.getLogger(__name__)

DEFAULT_SCAN_INTERVAL = timedelta(seconds=30)

SENSOR_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_NAME): str,
        vol.Required(CONF_PATH): str,
        vol.Optional(CONF_UNIT): str,
        vol.Optional(CONF_ICON): str,
    }
)

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(
    {
        vol.Optional(CONF_BASE_URL, default=DEFAULT_BASE_URL): str,
        vol.Optional(CONF_TIMEOUT, default=DEFAULT_TIMEOUT): int,
        vol.Optional(CONF_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL): timedelta,
        vol.Optional(CONF_SENSORS, default=[]): [SENSOR_SCHEMA],
    }
)


@dataclass(slots=True)
class PlanPayload:
    raw: dict[str, Any]
    plan: dict[str, Any]


@dataclass(slots=True)
class SensorSpec:
    name: str
    path: str
    unit: str | None = None
    icon: str | None = None


class HassEnergyClient:
    def __init__(self, session: aiohttp.ClientSession, base_url: str, timeout: int) -> None:
        self._session = session
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    async def fetch_latest(self) -> PlanPayload | None:
        url = f"{self._base_url}/plan/latest"
        try:
            async with async_timeout.timeout(self._timeout):
                async with self._session.get(url) as resp:
                    if resp.status == 404:
                        return None
                    resp.raise_for_status()
                    data = await resp.json()
        except aiohttp.ClientError as exc:
            raise UpdateFailed(f"Failed to fetch EMS plan: {exc}") from exc

        plan = {}
        if isinstance(data, dict):
            result = data.get("result")
            if isinstance(result, dict):
                plan = result.get("plan") if isinstance(result.get("plan"), dict) else {}
        return PlanPayload(raw=data if isinstance(data, dict) else {}, plan=plan)


class HassEnergyCoordinator(DataUpdateCoordinator[PlanPayload | None]):
    def __init__(self, hass: HomeAssistant, client: HassEnergyClient, interval: timedelta) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name="hass_energy_plan",
            update_interval=interval,
        )
        self._client = client

    async def _async_update_data(self) -> PlanPayload | None:
        return await self._client.fetch_latest()


class HassEnergyPlanSensor(CoordinatorEntity[PlanPayload | None], SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator: HassEnergyCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = "hass_energy_plan"
        self._attr_name = "HASS Energy Plan"

    @property
    def native_value(self) -> str | None:
        payload = self.coordinator.data
        if not payload or not payload.plan:
            return None
        status = payload.plan.get("status")
        return str(status) if status is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        payload = self.coordinator.data
        if not payload or not payload.plan:
            return {}
        return {
            "plan": payload.plan,
        }


class HassEnergyPlanUpdatedSensor(CoordinatorEntity[PlanPayload | None], SensorEntity):
    _attr_has_entity_name = True
    _attr_unique_id = "hass_energy_plan_generated_at"
    _attr_name = "EMS Plan Updated"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:clock-outline"

    def __init__(self, coordinator: HassEnergyCoordinator) -> None:
        super().__init__(coordinator)

    @property
    def native_value(self) -> datetime | None:
        payload = self.coordinator.data
        if not payload:
            return None
        return _plan_generated_at(payload)


class HassEnergyPlanValueSensor(CoordinatorEntity[PlanPayload | None], SensorEntity):
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: HassEnergyCoordinator,
        *,
        name: str,
        path: str,
        unit: str | None,
        icon: str | None,
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"hass_energy_{name.lower().replace(' ', '_')}"
        self._attr_name = name
        self._path = path
        if unit:
            self._attr_native_unit_of_measurement = unit
        if icon:
            self._attr_icon = icon

    @property
    def native_value(self) -> Any:
        payload = self.coordinator.data
        if not payload or not payload.plan:
            return None
        return _resolve_path(payload.plan, self._path)


async def async_setup_platform(
    hass: HomeAssistant,
    config: dict,
    async_add_entities,
    discovery_info=None,
) -> None:
    session = async_get_clientsession(hass)
    base_url = config[CONF_BASE_URL]
    timeout = config[CONF_TIMEOUT]
    interval = config[CONF_SCAN_INTERVAL]

    client = HassEnergyClient(session, base_url, timeout)
    coordinator = HassEnergyCoordinator(hass, client, interval)
    await coordinator.async_config_entry_first_refresh()

    entities: list[SensorEntity] = [
        HassEnergyPlanSensor(coordinator),
        HassEnergyPlanUpdatedSensor(coordinator),
    ]
    entities.extend(_build_mpc_entities(coordinator))
    for sensor_cfg in config.get(CONF_SENSORS, []):
        entities.append(
            HassEnergyPlanValueSensor(
                coordinator,
                name=sensor_cfg[CONF_NAME],
                path=sensor_cfg[CONF_PATH],
                unit=sensor_cfg.get(CONF_UNIT),
                icon=sensor_cfg.get(CONF_ICON),
            )
        )

    async_add_entities(entities)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities,
) -> None:
    session = async_get_clientsession(hass)
    base_url = entry.data.get(CONF_BASE_URL, DEFAULT_BASE_URL)
    timeout = entry.data.get(CONF_TIMEOUT, DEFAULT_TIMEOUT)

    client = HassEnergyClient(session, base_url, timeout)
    coordinator = HassEnergyCoordinator(hass, client, DEFAULT_SCAN_INTERVAL)
    await coordinator.async_config_entry_first_refresh()

    entities: list[SensorEntity] = [
        HassEnergyPlanSensor(coordinator),
        HassEnergyPlanUpdatedSensor(coordinator),
    ]
    entities.extend(_build_mpc_entities(coordinator))
    async_add_entities(entities)


def _build_mpc_entities(
    coordinator: HassEnergyCoordinator,
) -> list[SensorEntity]:
    payload = coordinator.data
    if not payload or not payload.plan:
        return []
    specs = _build_mpc_specs(payload.plan)
    return [
        HassEnergyPlanValueSensor(
            coordinator,
            name=spec.name,
            path=spec.path,
            unit=spec.unit,
            icon=spec.icon,
        )
        for spec in specs
    ]


def _build_mpc_specs(plan: dict[str, Any]) -> list[SensorSpec]:
    slot0 = _get_slot0(plan)
    if not slot0:
        return []

    specs: list[SensorSpec] = [
        SensorSpec(
            name="MPC Grid Import",
            path="slots[0].grid_import_kw",
            unit="kW",
            icon="mdi:transmission-tower",
        ),
        SensorSpec(
            name="MPC Grid Export",
            path="slots[0].grid_export_kw",
            unit="kW",
            icon="mdi:transmission-tower",
        ),
        SensorSpec(
            name="MPC Grid Net",
            path="slots[0].grid_kw",
            unit="kW",
            icon="mdi:transmission-tower",
        ),
        SensorSpec(
            name="MPC PV",
            path="slots[0].pv_kw",
            unit="kW",
            icon="mdi:solar-power",
        ),
    ]

    _extend_dict_specs(
        specs,
        slot0,
        dict_key="ev_charge_kw",
        name_prefix="MPC EV Charge",
        path_prefix="slots[0].ev_charge_kw",
        unit="kW",
        icon="mdi:ev-station",
    )
    _extend_dict_specs(
        specs,
        slot0,
        dict_key="inverter_ac_net_kw",
        name_prefix="MPC Inverter Net",
        path_prefix="slots[0].inverter_ac_net_kw",
        unit="kW",
        icon="mdi:current-ac",
    )
    _extend_dict_specs(
        specs,
        slot0,
        dict_key="curtail_inverters",
        name_prefix="MPC Curtail",
        path_prefix="slots[0].curtail_inverters",
        unit=None,
        icon="mdi:solar-power-variant",
    )
    _extend_dict_specs(
        specs,
        slot0,
        dict_key="battery_charge_kw",
        name_prefix="MPC Battery Charge",
        path_prefix="slots[0].battery_charge_kw",
        unit="kW",
        icon="mdi:battery-charging",
    )
    _extend_dict_specs(
        specs,
        slot0,
        dict_key="battery_discharge_kw",
        name_prefix="MPC Battery Discharge",
        path_prefix="slots[0].battery_discharge_kw",
        unit="kW",
        icon="mdi:battery-minus",
    )
    return specs


def _extend_dict_specs(
    specs: list[SensorSpec],
    slot0: dict[str, Any],
    *,
    dict_key: str,
    name_prefix: str,
    path_prefix: str,
    unit: str | None,
    icon: str | None,
) -> None:
    values = slot0.get(dict_key)
    if not isinstance(values, dict):
        return
    for key in sorted(values.keys(), key=lambda item: str(item)):
        specs.append(
            SensorSpec(
                name=f"{name_prefix} {key}",
                path=f"{path_prefix}.{key}",
                unit=unit,
                icon=icon,
            )
        )


def _resolve_path(data: dict[str, Any], path: str) -> Any:
    current: Any = data
    if not path:
        return None
    parts = path.split(".")
    for part in parts:
        if current is None:
            return None
        key, index = _split_index(part)
        if key:
            if isinstance(current, dict):
                current = current.get(key)
            else:
                return None
        if index is not None:
            if isinstance(current, list) and -len(current) <= index < len(current):
                current = current[index]
            else:
                return None
    if isinstance(current, (int, float)):
        return _round_kw(current)
    return current


def _split_index(part: str) -> tuple[str | None, int | None]:
    if "[" not in part:
        return part, None
    if not part.endswith("]"):
        return part, None
    key, raw_index = part[:-1].split("[", 1)
    try:
        index = int(raw_index)
    except ValueError:
        return part, None
    return key or None, index


def _round_kw(value: float) -> float:
    return round(float(value), 3)


def _plan_generated_at(payload: PlanPayload) -> datetime | None:
    if payload.plan:
        raw_value = payload.plan.get("generated_at")
        ts = _parse_epoch_seconds(raw_value)
        if ts is not None:
            return datetime.fromtimestamp(ts, tz=timezone.utc)
    if payload.raw:
        result = payload.raw.get("result")
        if isinstance(result, dict):
            ts = _parse_epoch_seconds(result.get("generated_at"))
            if ts is not None:
                return datetime.fromtimestamp(ts, tz=timezone.utc)
    return None


def _parse_epoch_seconds(value: Any) -> float | None:
    try:
        ts = float(value)
    except (TypeError, ValueError):
        return None
    if ts <= 0:
        return None
    return ts


def _get_slot0(plan: dict[str, Any]) -> dict[str, Any] | None:
    slots = plan.get("slots")
    if not isinstance(slots, list) or not slots:
        return None
    slot0 = slots[0]
    if not isinstance(slot0, dict):
        return None
    return slot0

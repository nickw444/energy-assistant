"""Device registry helpers shared across entity platforms."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.util import slugify

from .const import DOMAIN


def root_device_identifier(base_url: str) -> str:
    return f"server:{base_url}"


def root_device_info(base_url: str) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, root_device_identifier(base_url))},
        name="Plant",
    )


def inverter_device_info(base_url: str, inverter_id: str) -> DeviceInfo:
    root_id = root_device_identifier(base_url)
    return DeviceInfo(
        identifiers={(DOMAIN, f"{root_id}:inverter:{inverter_id}")},
        name=f"Inverter {inverter_id}",
        via_device=(DOMAIN, root_id),
    )


def pv_device_info(base_url: str, pv_id: str) -> DeviceInfo:
    root_id = root_device_identifier(base_url)
    return DeviceInfo(
        identifiers={(DOMAIN, f"{root_id}:pv:{pv_id}")},
        name=f"PV {pv_id}",
        via_device=(DOMAIN, root_id),
    )


def battery_device_info(base_url: str, battery_id: str) -> DeviceInfo:
    root_id = root_device_identifier(base_url)
    return DeviceInfo(
        identifiers={(DOMAIN, f"{root_id}:battery:{battery_id}")},
        name=f"Battery {battery_id}",
        via_device=(DOMAIN, root_id),
    )


def ev_device_info(base_url: str, ev_id: str) -> DeviceInfo:
    root_id = root_device_identifier(base_url)
    return DeviceInfo(
        identifiers={(DOMAIN, f"{root_id}:ev:{ev_id}")},
        name=f"EV {ev_id}",
        via_device=(DOMAIN, root_id),
    )


def entity_unique_id(base_url: str, *parts: str) -> str:
    return ":".join([root_device_identifier(base_url), *parts])


def suggested_object_id(*parts: str) -> str:
    return f"energy_assistant_{slugify('_'.join(parts))}"

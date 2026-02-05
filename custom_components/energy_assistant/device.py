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


def load_device_info(base_url: str, load_id: str) -> DeviceInfo:
    root_id = root_device_identifier(base_url)
    return DeviceInfo(
        identifiers={(DOMAIN, f"{root_id}:load:{load_id}")},
        name=f"Load {load_id}",
        via_device=(DOMAIN, root_id),
    )


def entity_unique_id(base_url: str, *parts: str) -> str:
    return ":".join([root_device_identifier(base_url), *parts])


def suggested_object_id(*parts: str) -> str:
    return f"energy_assistant_{slugify('_'.join(parts))}"

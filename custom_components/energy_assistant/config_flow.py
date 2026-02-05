"""Config flow for Energy Assistant integration."""

from __future__ import annotations

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult

from .const import CONF_BASE_URL, DEFAULT_BASE_URL, DOMAIN


class EnergyAssistantConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Energy Assistant."""

    VERSION = 1

    async def async_step_user(
        self,
        user_input: dict[str, str] | None = None,
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            base_url = user_input[CONF_BASE_URL].rstrip("/")
            await self.async_set_unique_id(base_url)
            self._abort_if_unique_id_configured()
            return self.async_create_entry(title=base_url, data={CONF_BASE_URL: base_url})

        schema = vol.Schema(
            {
                vol.Required(CONF_BASE_URL, default=DEFAULT_BASE_URL): str,
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

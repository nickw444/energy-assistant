from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from hass_energy.config import EnergySystemConfig


@dataclass
class CompiledModel:
    """Container for compiled model pieces."""

    constraints: list[str]
    metadata: dict[str, Any]


class ModelCompiler:
    """Compiles high-level energy configuration into MILP constraints."""

    def compile(self, config: EnergySystemConfig) -> CompiledModel:
        # Placeholder compilation: capture basic config data as metadata.
        return CompiledModel(
            constraints=[],
            metadata={
                "forecast_window_hours": config.forecast_window_hours,
                "poll_interval_seconds": config.poll_interval_seconds,
                "home_assistant_configured": bool(config.home_assistant.base_url),
            },
        )

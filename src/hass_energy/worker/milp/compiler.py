from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from hass_energy.models.config import EmsConfig


@dataclass
class CompiledModel:
    """Container for compiled model pieces."""

    constraints: list[str]
    metadata: dict[str, Any]


class ModelCompiler:
    """Compiles high-level energy configuration into MILP constraints."""

    def compile(self, config: EmsConfig) -> CompiledModel:
        # Placeholder compilation: capture basic config data as metadata.
        return CompiledModel(
            constraints=[],
            metadata={
                "interval_duration": config.interval_duration,
                "num_intervals": config.num_intervals,
            },
        )

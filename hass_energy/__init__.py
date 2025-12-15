"""Core package for the hass-energy CLI."""

from .config import Config, HomeAssistantConfig, MapperConfig, OptimizerConfig, load_config
from .ha_client import HomeAssistantWebSocketClient
from .mapper import HassEnergyMapper, load_mapper
from .optimizer import HassEnergyOptimizer, load_optimizer

__all__ = [
    "__version__",
    "Config",
    "HomeAssistantConfig",
    "MapperConfig",
    "OptimizerConfig",
    "HomeAssistantWebSocketClient",
    "HassEnergyMapper",
    "HassEnergyOptimizer",
    "load_config",
    "load_mapper",
    "load_optimizer",
]
__version__ = "0.1.0"

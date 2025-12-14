"""Core package for the hass-energy CLI."""

from .config import Config, HomeAssistantConfig, load_config
from .ha_client import HomeAssistantWebSocketClient

__all__ = [
    "__version__",
    "Config",
    "HomeAssistantConfig",
    "HomeAssistantWebSocketClient",
    "load_config",
]
__version__ = "0.1.0"

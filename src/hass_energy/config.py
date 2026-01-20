from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, cast

import yaml
from pydantic import ValidationError

from hass_energy.models.config import AppConfig

logger = logging.getLogger(__name__)


_DEFAULT_CONFIG = Path("config.yaml")
_DEV_CONFIG_FALLBACK = Path("config.dev.yaml")


def load_app_config(config_path: Path | None) -> AppConfig:
    if config_path is None:
        logger.info("No config path provided; using defaults")
        if _DEFAULT_CONFIG.exists():
            config_path = _DEFAULT_CONFIG
        elif _DEV_CONFIG_FALLBACK.exists():
            config_path = _DEV_CONFIG_FALLBACK
        else:
            config_path = _DEFAULT_CONFIG

    if not config_path.exists():
        raise ValueError(f"Config file {config_path} not found")

    try:
        loaded: Any = yaml.safe_load(config_path.read_text()) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"Config file is not valid YAML: {config_path}") from exc

    if not isinstance(loaded, dict):
        raise ValueError("Top-level config must be a mapping")

    try:
        config = AppConfig.model_validate(cast(dict[str, Any], loaded))
    except ValidationError as exc:
        raise ValueError(f"Invalid configuration in {config_path}: {exc}") from exc

    _ensure_data_dir(config.server.data_dir)
    return config


def _ensure_data_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)

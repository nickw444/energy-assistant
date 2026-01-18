from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, cast

import yaml
from pydantic import ValidationError

from hass_energy.models.config import AppConfig

logger = logging.getLogger(__name__)


def load_app_config(config_path: Path | None) -> AppConfig:
    if config_path is None:
        logger.info("No config path provided; using defaults")
        config_path = Path("config.yaml")

    devPath = Path("config.dev.yaml")
    if not config_path.exists() and devPath.exists():
        logger.info("Using config.dev.yaml")
        config_path = devPath

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

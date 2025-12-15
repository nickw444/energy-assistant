from __future__ import annotations

import importlib
import importlib.util
import inspect
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any, Protocol, cast, runtime_checkable

from .config import OptimizerConfig

DEFAULT_OPTIMIZER_ATTRS = ("get_optimizer", "optimizer", "Optimizer")


@runtime_checkable
class HassEnergyOptimizer(Protocol):
    """Protocol for decision layers built on top of mapped Home Assistant data."""

    def required_entities(self) -> list[str]:
        """Return Home Assistant entity_ids for knobs/settings needed by the optimizer."""
        ...

    def decide(self, mapped: dict[str, Any], entities: dict[str, Any]) -> dict[str, Any]:
        """Return a decision payload produced from mapped data and live knob states."""
        ...


@dataclass
class _OptimizerSpec:
    module: str
    attribute: str | None


def load_optimizer(optimizer_config: OptimizerConfig) -> HassEnergyOptimizer:
    """Load an optimizer implementation from an importable module on PYTHONPATH."""
    spec = _parse_spec(optimizer_config)
    module = _import_module(spec.module)
    optimizer = _resolve_optimizer(module, spec.attribute)
    _validate_optimizer(optimizer)
    return optimizer


def _parse_spec(optimizer_config: OptimizerConfig) -> _OptimizerSpec:
    module_spec = optimizer_config.module
    attribute = optimizer_config.attribute
    if ":" in module_spec and attribute is None:
        module_spec, attribute = module_spec.split(":", 1)
    return _OptimizerSpec(module=module_spec, attribute=attribute)


def _import_module(module_ref: str) -> ModuleType:
    path_candidate = Path(module_ref)
    if path_candidate.exists():
        if path_candidate.is_dir():
            raise FileNotFoundError(
                f"Optimizer path is a directory, expected file: {path_candidate}"
            )
        return _import_module_from_file(path_candidate.resolve())

    try:
        return importlib.import_module(module_ref)
    except ModuleNotFoundError as exc:
        raise FileNotFoundError(
            f"Optimizer module '{module_ref}' could not be imported; ensure it is on "
            "PYTHONPATH or provide a valid file path"
        ) from exc


def _import_module_from_file(path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(path.stem, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Unable to load optimizer module from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    loader = spec.loader
    assert loader is not None
    loader.exec_module(module)  # type: ignore[arg-type]
    return module


def _resolve_optimizer(module: ModuleType, attribute: str | None) -> HassEnergyOptimizer:
    candidates = [attribute] if attribute else list(DEFAULT_OPTIMIZER_ATTRS)
    for attr in candidates:
        if not attr:
            continue
        if not hasattr(module, attr):
            continue
        candidate = getattr(module, attr)
        materialized = _materialize(candidate)
        if materialized:
            return materialized
    if attribute:
        raise ValueError(f"Optimizer attribute '{attribute}' not found or invalid in {module}")
    raise ValueError(
        f"Could not find an optimizer in {module}; "
        f"tried attributes {', '.join(DEFAULT_OPTIMIZER_ATTRS)}"
    )


def _materialize(obj: Any) -> HassEnergyOptimizer | None:
    if inspect.isclass(obj):
        try:
            instance = obj()
        except TypeError:
            return None
        return cast(HassEnergyOptimizer, instance) if _is_optimizer(instance) else None
    if _is_optimizer(obj):
        return cast(HassEnergyOptimizer, obj)
    if callable(obj):
        try:
            instance = obj()
        except TypeError:
            return None
        return cast(HassEnergyOptimizer, instance) if _is_optimizer(instance) else None
    return None


def _is_optimizer(obj: Any) -> bool:
    return (
        hasattr(obj, "required_entities")
        and callable(obj.required_entities)
        and hasattr(obj, "decide")
        and callable(obj.decide)
    )


def _validate_optimizer(optimizer: HassEnergyOptimizer) -> None:
    required_entities = optimizer.required_entities()
    if not isinstance(required_entities, list) or not all(
        isinstance(item, str) for item in required_entities
    ):
        raise ValueError("Optimizer.required_entities() must return a list of strings")
    try:
        optimizer.decide({}, {})
    except Exception:
        # Input validation may reject empty dicts; this is a light sanity check only.
        pass

from __future__ import annotations

import importlib
import importlib.util
import inspect
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any, Protocol, cast, runtime_checkable

from .config import MapperConfig

DEFAULT_MAPPER_ATTRS = ("get_mapper", "mapper", "Mapper")


@runtime_checkable
class HassEnergyMapper(Protocol):
    """Protocol for Home Assistant state mappers."""

    def required_entities(self) -> list[str]:
        """Return the entity_ids required to build the mapped output."""
        ...

    def map(self, states: dict[str, Any]) -> dict[str, Any]:
        """Produce a mapped/flattened representation of the provided states."""
        ...


@dataclass
class _MapperSpec:
    module: str
    attribute: str | None


def load_mapper(mapper_config: MapperConfig) -> HassEnergyMapper:
    """Load a mapper implementation from an importable module on PYTHONPATH."""
    spec = _parse_spec(mapper_config)
    module = _import_module(spec.module)
    mapper = _resolve_mapper(module, spec.attribute)
    _validate_mapper(mapper)
    return mapper


def _parse_spec(mapper_config: MapperConfig) -> _MapperSpec:
    module_spec = mapper_config.module
    attribute = mapper_config.attribute
    if ":" in module_spec and attribute is None:
        module_spec, attribute = module_spec.split(":", 1)
    return _MapperSpec(module=module_spec, attribute=attribute)


def _import_module(module_ref: str) -> ModuleType:
    path_candidate = Path(module_ref)
    if path_candidate.exists():
        if path_candidate.is_dir():
            raise FileNotFoundError(f"Mapper path is a directory, expected file: {path_candidate}")
        return _import_module_from_file(path_candidate.resolve())

    try:
        return importlib.import_module(module_ref)
    except ModuleNotFoundError as exc:
        raise FileNotFoundError(
            f"Mapper module '{module_ref}' could not be imported; ensure it is on PYTHONPATH "
            "or provide a valid file path"
        ) from exc


def _import_module_from_file(path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(path.stem, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Unable to load mapper module from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    loader = spec.loader
    assert loader is not None
    loader.exec_module(module)  # type: ignore[arg-type]
    return module


def _resolve_mapper(module: ModuleType, attribute: str | None) -> HassEnergyMapper:
    candidates = [attribute] if attribute else list(DEFAULT_MAPPER_ATTRS)
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
        raise ValueError(f"Mapper attribute '{attribute}' not found or invalid in {module}")
    raise ValueError(
        f"Could not find a mapper in {module}; tried attributes {', '.join(DEFAULT_MAPPER_ATTRS)}"
    )


def _materialize(obj: Any) -> HassEnergyMapper | None:
    if inspect.isclass(obj):
        try:
            instance = obj()
        except TypeError:
            return None
        return cast(HassEnergyMapper, instance) if _is_mapper(instance) else None
    if _is_mapper(obj):
        return cast(HassEnergyMapper, obj)
    if callable(obj):
        try:
            instance = obj()
        except TypeError:
            return None
        return cast(HassEnergyMapper, instance) if _is_mapper(instance) else None
    return None


def _is_mapper(obj: Any) -> bool:
    return hasattr(obj, "required_entities") and callable(obj.required_entities) and hasattr(
        obj, "map"
    ) and callable(obj.map)


def _validate_mapper(mapper: HassEnergyMapper) -> None:
    # Validate required_entities() returns a list of strings.
    required = mapper.required_entities()
    if not isinstance(required, list) or not all(isinstance(item, str) for item in required):
        raise ValueError("Mapper.required_entities() must return a list of strings")
    # Ensure map is callable (already checked) and does not raise when called with empty dict.
    try:
        mapper.map({})
    except Exception:
        # Don't fail hard; mapping may require entities. Keep a sanity call for signature shape.
        pass


def get_state(states: dict[str, Any], entity_id: str) -> Any:
    """Return the state value for an entity_id."""
    state = states.get(entity_id) or {}
    return state.get("state")


def get_attr(states: dict[str, Any], entity_id: str, key: str) -> Any:
    """Return a specific attribute value for an entity_id."""
    state = states.get(entity_id) or {}
    attrs = state.get("attributes") or {}
    return attrs.get(key)


def to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def to_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None

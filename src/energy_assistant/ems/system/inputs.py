from __future__ import annotations

from typing import Any

from energy_assistant.ems.horizon import Horizon


class EmsInputs:
    """Per-run resolved/aligned inputs consumed by the Layer 0 MILP fragments.

    This is intentionally key-based to keep LinkComponents generic and reusable. Keys must be
    unique within a run.
    """

    def __init__(self, *, horizon: Horizon) -> None:
        self._num_intervals = int(horizon.num_intervals)
        self._float_series: dict[str, list[float]] = {}
        self._bool_series: dict[str, list[bool]] = {}
        self._floats: dict[str, float] = {}
        self._bools: dict[str, bool] = {}
        self._objects: dict[str, Any] = {}

    @property
    def num_intervals(self) -> int:
        return self._num_intervals

    def set_float_series(self, key: str, values: list[float]) -> None:
        if len(values) != self._num_intervals:
            raise ValueError(
                f"Float series {key!r} length {len(values)} != num_intervals={self._num_intervals}"
            )
        self._float_series[str(key)] = list(values)

    def float_series(self, key: str) -> list[float]:
        return self._float_series[str(key)]

    def set_bool_series(self, key: str, values: list[bool]) -> None:
        if len(values) != self._num_intervals:
            raise ValueError(
                f"Bool series {key!r} length {len(values)} != num_intervals={self._num_intervals}"
            )
        self._bool_series[str(key)] = list(values)

    def bool_series(self, key: str) -> list[bool]:
        return self._bool_series[str(key)]

    def set_float(self, key: str, value: float) -> None:
        self._floats[str(key)] = float(value)

    def float(self, key: str) -> float:
        return float(self._floats[str(key)])

    def set_bool(self, key: str, value: bool) -> None:
        self._bools[str(key)] = bool(value)

    def bool(self, key: str) -> bool:
        return bool(self._bools[str(key)])

    def set_object(self, key: str, value: Any) -> None:
        self._objects[str(key)] = value

    def object(self, key: str) -> Any:
        return self._objects[str(key)]


from __future__ import annotations

from energy_assistant.inputs.registry import ResolvedScalarInput
from energy_assistant.models.inputs import InputValueKind


class AppliedForecastInput:
    def __init__(self, *, key: str, kind: InputValueKind, series: list[float]) -> None:
        self.key = str(key)
        self.kind = kind
        self.series = [float(value) for value in series]


class AppliedInputRegistry:
    def __init__(
        self,
        *,
        scalars: dict[str, ResolvedScalarInput] | None = None,
        forecasts: dict[str, AppliedForecastInput] | None = None,
    ) -> None:
        self._scalars = dict(scalars or {})
        self._forecasts = dict(forecasts or {})

    def scalar(self, key: str, *, kind: InputValueKind | None = None) -> float | bool:
        resolved = self._scalars.get(key)
        if resolved is None:
            raise ValueError(f"Missing scalar input: {key}")
        if kind is not None and resolved.kind is not kind:
            raise ValueError(
                f"Scalar input {key} has kind {resolved.kind.value}; expected {kind.value}"
            )
        return resolved.value

    def scalar_float(self, key: str, *, kind: InputValueKind) -> float:
        value = self.scalar(key, kind=kind)
        if isinstance(value, bool):
            raise ValueError(f"Scalar input {key} is boolean, not numeric")
        return float(value)

    def scalar_bool(self, key: str) -> bool:
        value = self.scalar(key, kind=InputValueKind.BOOLEAN)
        return bool(value)

    def forecast(self, key: str, *, kind: InputValueKind | None = None) -> list[float]:
        resolved = self._forecasts.get(key)
        if resolved is None:
            raise ValueError(f"Missing forecast input: {key}")
        if kind is not None and resolved.kind is not kind:
            raise ValueError(
                f"Forecast input {key} has kind {resolved.kind.value}; expected {kind.value}"
            )
        return list(resolved.series)

from __future__ import annotations

from typing import cast

from energy_assistant.models.inputs import InputValueKind


class ResolvedScalarInput:
    def __init__(self, *, key: str, kind: InputValueKind, value: float | bool) -> None:
        self.key = str(key)
        self.kind = kind
        self.value = value

    def to_payload(self) -> dict[str, object]:
        return {
            "type": "scalar",
            "kind": self.kind.value,
            "value": self.value,
        }


class ResolvedForecastInput:
    def __init__(self, *, key: str, kind: InputValueKind, series: list[float]) -> None:
        self.key = str(key)
        self.kind = kind
        self.series = [float(value) for value in series]

    def to_payload(self) -> dict[str, object]:
        return {
            "type": "forecast",
            "kind": self.kind.value,
            "series": list(self.series),
        }


class ResolvedInputRegistry:
    def __init__(
        self,
        *,
        scalars: dict[str, ResolvedScalarInput] | None = None,
        forecasts: dict[str, ResolvedForecastInput] | None = None,
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

    def to_payload(self) -> dict[str, dict[str, object]]:
        payload: dict[str, dict[str, object]] = {}
        for key, value in sorted(self._scalars.items()):
            payload[key] = value.to_payload()
        for key, value in sorted(self._forecasts.items()):
            payload[key] = value.to_payload()
        return payload

    @classmethod
    def from_payload(cls, payload: dict[str, object]) -> ResolvedInputRegistry:
        scalars: dict[str, ResolvedScalarInput] = {}
        forecasts: dict[str, ResolvedForecastInput] = {}
        for key, raw_value in payload.items():
            if not isinstance(raw_value, dict):
                raise ValueError("Resolved input payload entries must be objects")
            value = cast(dict[str, object], raw_value)
            raw_type = value.get("type")
            raw_kind = value.get("kind")
            if not isinstance(raw_type, str) or not isinstance(raw_kind, str):
                raise ValueError("Resolved input payload entries require string type/kind fields")
            try:
                kind = InputValueKind(raw_kind)
            except ValueError as exc:
                raise ValueError(f"Unsupported resolved input kind: {raw_kind}") from exc
            if raw_type == "scalar":
                scalar_value = value.get("value")
                if not isinstance(scalar_value, (int, float, bool)):
                    raise ValueError("Resolved scalar input value must be a number or boolean")
                scalars[key] = ResolvedScalarInput(
                    key=key,
                    kind=kind,
                    value=cast(float | bool, scalar_value),
                )
                continue
            if raw_type == "forecast":
                raw_series = value.get("series")
                if not isinstance(raw_series, list):
                    raise ValueError("Resolved forecast input series must be a list")
                series_items = cast(list[object], raw_series)
                forecasts[key] = ResolvedForecastInput(
                    key=key,
                    kind=kind,
                    series=[float(cast(int | float, item)) for item in series_items],
                )
                continue
            raise ValueError(f"Unsupported resolved input type: {raw_type}")
        return cls(scalars=scalars, forecasts=forecasts)

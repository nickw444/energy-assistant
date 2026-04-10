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
    def __init__(
        self,
        *,
        key: str,
        kind: InputValueKind,
        points: dict[str, float],
        interval_minutes: int,
        realtime_value: float | None = None,
        extension_points: dict[str, float] | None = None,
        extension_interval_minutes: int | None = None,
    ) -> None:
        self.key = str(key)
        self.kind = kind
        self.points = {str(ts): float(value) for ts, value in sorted(points.items())}
        self.interval_minutes = int(interval_minutes)
        self.realtime_value = None if realtime_value is None else float(realtime_value)
        self.extension_points = (
            None
            if extension_points is None
            else {str(ts): float(value) for ts, value in sorted(extension_points.items())}
        )
        self.extension_interval_minutes = (
            None if extension_interval_minutes is None else int(extension_interval_minutes)
        )

    def to_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "type": "forecast",
            "kind": self.kind.value,
            "points": dict(self.points),
            "interval_minutes": self.interval_minutes,
        }
        if self.realtime_value is not None:
            payload["realtime_value"] = self.realtime_value
        if self.extension_points is not None:
            payload["extension_points"] = dict(self.extension_points)
        if self.extension_interval_minutes is not None:
            payload["extension_interval_minutes"] = self.extension_interval_minutes
        return payload


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

    def forecast(
        self,
        key: str,
        *,
        kind: InputValueKind | None = None,
    ) -> ResolvedForecastInput:
        resolved = self._forecasts.get(key)
        if resolved is None:
            raise ValueError(f"Missing forecast input: {key}")
        if kind is not None and resolved.kind is not kind:
            raise ValueError(
                f"Forecast input {key} has kind {resolved.kind.value}; expected {kind.value}"
            )
        return resolved

    def scalars(self) -> dict[str, ResolvedScalarInput]:
        return dict(self._scalars)

    def forecasts(self) -> dict[str, ResolvedForecastInput]:
        return dict(self._forecasts)

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
                forecasts[key] = _resolved_forecast_from_payload(key=key, kind=kind, payload=value)
                continue
            raise ValueError(f"Unsupported resolved input type: {raw_type}")
        return cls(scalars=scalars, forecasts=forecasts)


def _resolved_forecast_from_payload(
    *,
    key: str,
    kind: InputValueKind,
    payload: dict[str, object],
) -> ResolvedForecastInput:
    raw_points = payload.get("points")
    if not isinstance(raw_points, dict):
        raise ValueError("Resolved forecast input points must be an object")
    raw_interval_minutes = payload.get("interval_minutes")
    if not isinstance(raw_interval_minutes, int):
        raise ValueError("Resolved forecast input interval_minutes must be an integer")
    realtime_value = payload.get("realtime_value")
    if realtime_value is not None and not isinstance(realtime_value, (int, float)):
        raise ValueError("Resolved forecast realtime_value must be numeric")
    raw_extension_points = payload.get("extension_points")
    extension_points: dict[str, float] | None = None
    if raw_extension_points is not None:
        if not isinstance(raw_extension_points, dict):
            raise ValueError("Resolved forecast extension_points must be an object")
        extension_points = {
            str(ts): float(cast(int | float, item))
            for ts, item in cast(dict[str, object], raw_extension_points).items()
        }
    raw_extension_interval = payload.get("extension_interval_minutes")
    if raw_extension_interval is not None and not isinstance(raw_extension_interval, int):
        raise ValueError(
            "Resolved forecast extension_interval_minutes must be an integer"
        )
    return ResolvedForecastInput(
        key=key,
        kind=kind,
        points={
            str(ts): float(cast(int | float, item))
            for ts, item in cast(dict[str, object], raw_points).items()
        },
        interval_minutes=raw_interval_minutes,
        realtime_value=None if realtime_value is None else float(realtime_value),
        extension_points=extension_points,
        extension_interval_minutes=raw_extension_interval,
    )

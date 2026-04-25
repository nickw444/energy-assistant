from __future__ import annotations

from datetime import datetime, timedelta

from energy_assistant.ems.inputs.alignment import (
    PowerForecastAligner,
    PriceForecastAligner,
    validate_forecast_coverage,
)
from energy_assistant.ems.inputs.models import (
    AppliedForecastInput,
    AppliedInputRegistry,
)
from energy_assistant.ems.planning.horizon import Horizon
from energy_assistant.inputs.registry import (
    ResolvedForecastInput,
    ResolvedInputRegistry,
    ResolvedScalarInput,
)
from energy_assistant.lib.source_resolver.models import (
    PowerForecastInterval,
    PriceForecastInterval,
)
from energy_assistant.models.inputs import InputConfig, InputValueKind, input_value_kind


class EmsInputApplicator:
    def __init__(
        self,
        *,
        input_configs: dict[str, InputConfig],
        power_aligner: PowerForecastAligner,
        price_aligner: PriceForecastAligner,
    ) -> None:
        self._input_configs = dict(input_configs)
        self._power_aligner = power_aligner
        self._price_aligner = price_aligner

    def apply_to_horizon(
        self,
        *,
        horizon: Horizon,
        inputs: ResolvedInputRegistry,
    ) -> AppliedInputRegistry:
        scalars: dict[str, ResolvedScalarInput] = inputs.scalars()
        raw_forecasts = inputs.forecasts()
        forecasts: dict[str, AppliedForecastInput] = {}

        for key, input_config in self._input_configs.items():
            if key not in raw_forecasts:
                continue
            kind = input_value_kind(input_config)
            raw_forecast = raw_forecasts[key]
            if raw_forecast.kind is not kind:
                raise ValueError(
                    "Forecast input "
                    f"{key} has kind {raw_forecast.kind.value}; expected {kind.value}"
                )
            series = self._apply_forecast(
                label=f"Input {key}",
                horizon=horizon,
                raw_forecast=raw_forecast,
                kind=kind,
            )
            forecasts[key] = AppliedForecastInput(key=key, kind=kind, series=series)

        return AppliedInputRegistry(scalars=scalars, forecasts=forecasts)

    def _apply_forecast(
        self,
        *,
        label: str,
        horizon: Horizon,
        raw_forecast: ResolvedForecastInput,
        kind: InputValueKind,
    ) -> list[float]:
        if kind is InputValueKind.PRICE:
            return self._apply_price_forecast(
                label=label,
                horizon=horizon,
                raw_forecast=raw_forecast,
            )
        return self._apply_power_forecast(
            label=label,
            horizon=horizon,
            raw_forecast=raw_forecast,
        )

    def _apply_power_forecast(
        self,
        *,
        label: str,
        horizon: Horizon,
        raw_forecast: ResolvedForecastInput,
    ) -> list[float]:
        intervals = _power_forecast_intervals_from_points(
            points=raw_forecast.points,
            fallback_interval_minutes=raw_forecast.interval_minutes,
        )
        validate_forecast_coverage(
            label=label,
            horizon=horizon,
            intervals=intervals,
            allow_first_slot_missing=raw_forecast.realtime_value is not None,
        )
        return [
            value
            for value in self._power_aligner.align(
                horizon,
                intervals,
                first_slot_override=raw_forecast.realtime_value,
            )
        ]

    def _apply_price_forecast(
        self,
        *,
        label: str,
        horizon: Horizon,
        raw_forecast: ResolvedForecastInput,
    ) -> list[float]:
        intervals = _price_forecast_intervals_from_points(
            points=raw_forecast.points,
            fallback_interval_minutes=raw_forecast.interval_minutes,
        )
        if raw_forecast.extension_points is not None:
            extension_intervals = _price_forecast_intervals_from_points(
                points=raw_forecast.extension_points,
                fallback_interval_minutes=(
                    raw_forecast.extension_interval_minutes
                    if raw_forecast.extension_interval_minutes is not None
                    else raw_forecast.interval_minutes
                ),
            )
            intervals = _merge_price_intervals(
                base_intervals=intervals,
                extension_intervals=extension_intervals,
            )

        validate_forecast_coverage(
            label=label,
            horizon=horizon,
            intervals=intervals,
            allow_first_slot_missing=raw_forecast.realtime_value is not None,
        )
        return [
            value
            for value in self._price_aligner.align(
                horizon,
                intervals,
                first_slot_override=raw_forecast.realtime_value,
            )
        ]

def _price_forecast_intervals_from_points(
    *,
    points: dict[str, float],
    fallback_interval_minutes: int,
) -> list[PriceForecastInterval]:
    if fallback_interval_minutes <= 0:
        raise ValueError("forecast interval_minutes must be positive")
    ordered = _sorted_points(points)
    if not ordered:
        return []

    fallback = timedelta(minutes=fallback_interval_minutes)
    price_intervals: list[PriceForecastInterval] = []
    for idx, (start, value) in enumerate(ordered):
        end = ordered[idx + 1][0] if idx + 1 < len(ordered) else start + fallback
        if end <= start:
            raise ValueError("forecast points must be strictly increasing")
        price_intervals.append(PriceForecastInterval(start=start, end=end, value=value))
    return price_intervals


def _power_forecast_intervals_from_points(
    *,
    points: dict[str, float],
    fallback_interval_minutes: int,
) -> list[PowerForecastInterval]:
    if fallback_interval_minutes <= 0:
        raise ValueError("forecast interval_minutes must be positive")
    ordered = _sorted_points(points)
    if not ordered:
        return []

    fallback = timedelta(minutes=fallback_interval_minutes)
    power_intervals: list[PowerForecastInterval] = []
    for idx, (start, value) in enumerate(ordered):
        end = ordered[idx + 1][0] if idx + 1 < len(ordered) else start + fallback
        if end <= start:
            raise ValueError("forecast points must be strictly increasing")
        power_intervals.append(PowerForecastInterval(start=start, end=end, value=value))
    return power_intervals


def _sorted_points(points: dict[str, float]) -> list[tuple[datetime, float]]:
    ordered: list[tuple[datetime, float]] = []
    for raw_start, raw_value in points.items():
        start = datetime.fromisoformat(raw_start)
        if start.tzinfo is None:
            raise ValueError("forecast points must be timezone-aware ISO datetimes")
        ordered.append((start, raw_value))
    ordered.sort(key=lambda item: item[0])
    return ordered


def _merge_price_intervals(
    *,
    base_intervals: list[PriceForecastInterval],
    extension_intervals: list[PriceForecastInterval],
) -> list[PriceForecastInterval]:
    if not base_intervals:
        return list(extension_intervals)
    merged = list(base_intervals)
    last_end = max(interval.end for interval in base_intervals)
    for interval in extension_intervals:
        if interval.end <= last_end:
            continue
        start = interval.start if interval.start >= last_end else last_end
        if interval.end <= start:
            continue
        merged.append(
            PriceForecastInterval(
                start=start,
                end=interval.end,
                value=interval.value,
            )
        )
    return merged

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta
from typing import Protocol

from energy_assistant.inputs.registry import (
    ResolvedForecastInput,
    ResolvedInputRegistry,
    ResolvedScalarInput,
)
from energy_assistant.inputs.window import InputWindow
from energy_assistant.lib.source_resolver.hass_source import (
    HomeAssistantAmberElectricForecastSource,
    HomeAssistantAmberExpressForecastSource,
    HomeAssistantBinarySensorEntitySource,
    HomeAssistantCurrencyEntitySource,
    HomeAssistantEntitySource,
    HomeAssistantHistoricalAverageForecastSource,
    HomeAssistantHistoricalAveragePriceForecastSource,
    HomeAssistantPercentageEntitySource,
    HomeAssistantPowerKwEntitySource,
    HomeAssistantSolcastForecastSource,
)
from energy_assistant.lib.source_resolver.models import (
    PowerForecastInterval,
    PriceForecastInterval,
)
from energy_assistant.lib.source_resolver.resolver import ValueResolver
from energy_assistant.models.config import AppConfig
from energy_assistant.models.inputs import (
    ForecastInputConfig,
    ForecastSource,
    InputValueKind,
    ScalarInputConfig,
    ScalarSource,
    input_value_kind,
)
from energy_assistant.models.plant import GridComponentConfig


class EmsInputProvider(Protocol):
    def mark_for_hydration(self) -> None: ...

    def hydrate_all(self) -> None: ...

    def resolve_for_window(self, *, window: InputWindow) -> ResolvedInputRegistry: ...

    def grid_price_watch_entity_ids(self) -> set[str]: ...


class ResolverBackedInputProvider:
    def __init__(self, *, app_config: AppConfig, resolver: ValueResolver) -> None:
        self._app_config = app_config
        self._resolver = resolver

    def mark_for_hydration(self) -> None:
        for input_config in self._app_config.inputs.values():
            if isinstance(input_config, ScalarInputConfig):
                self._resolver.mark_for_hydration(
                    self._scalar_entity_source(
                        entity=input_config.source.entity,
                        value_kind=input_config.value_kind,
                    )
                )
                continue
            self._resolver.mark_for_hydration(input_config.forecast)
            if input_config.realtime is not None:
                self._resolver.mark_for_hydration(
                    self._scalar_entity_source(
                        entity=input_config.realtime.entity,
                        value_kind=input_value_kind(input_config),
                    )
                )
            if input_config.forecast_expansion is not None:
                realtime_source = input_config.realtime
                if realtime_source is None:
                    raise ValueError("forecast_expansion requires a realtime scalar source")
                self._resolver.mark_for_hydration(
                    self._build_price_extension_source(
                        realtime_entity=realtime_source.entity,
                        forecast_horizon_hours=1,
                        history_days=input_config.forecast_expansion.history_days,
                        interval_duration=input_config.forecast_expansion.interval_duration,
                    )
                )

    def hydrate_all(self) -> None:
        self._resolver.hydrate_all()

    def resolve_for_window(self, *, window: InputWindow) -> ResolvedInputRegistry:
        scalars: dict[str, ResolvedScalarInput] = {}
        forecasts: dict[str, ResolvedForecastInput] = {}
        for key, input_config in self._app_config.inputs.items():
            kind = input_value_kind(input_config)
            if isinstance(input_config, ScalarInputConfig):
                scalars[key] = ResolvedScalarInput(
                    key=key,
                    kind=kind,
                    value=self._resolve_scalar(input_config),
                )
                continue
            forecast_intervals = self._resolve_forecast_intervals(
                input_config.forecast,
                kind=kind,
            )
            forecasts[key] = ResolvedForecastInput(
                key=key,
                kind=kind,
                points=self._intervals_to_point_map(forecast_intervals),
                interval_minutes=self._forecast_fallback_interval_minutes(
                    intervals=forecast_intervals,
                    forecast=input_config.forecast,
                ),
                realtime_value=self._resolve_optional_realtime_value(
                    realtime=input_config.realtime,
                    kind=kind,
                ),
                extension_points=self._resolve_extension_point_map(
                    input_config=input_config,
                    window=window,
                    kind=kind,
                ),
                extension_interval_minutes=self._resolve_extension_interval_minutes(
                    input_config=input_config,
                ),
            )
        return ResolvedInputRegistry(scalars=scalars, forecasts=forecasts)

    def grid_price_watch_entity_ids(self) -> set[str]:
        entity_ids: set[str] = set()
        for grid in _grid_components(self._app_config):
            for binding in (grid.price_import, grid.price_export):
                input_config = self._app_config.inputs[binding.source.key]
                if not isinstance(input_config, ForecastInputConfig):
                    continue
                realtime = input_config.realtime
                if realtime is not None:
                    entity_ids.add(realtime.entity)
        return entity_ids

    def _resolve_scalar(self, input_config: ScalarInputConfig) -> float | bool:
        try:
            value = self._resolver.resolve(
                self._scalar_entity_source(
                    entity=input_config.source.entity,
                    value_kind=input_config.value_kind,
                )
            )
        except ValueError:
            if input_config.value_kind is InputValueKind.POWER:
                return 0.0
            if input_config.value_kind is InputValueKind.BOOLEAN:
                return False
            raise
        if isinstance(value, bool):
            return value
        return float(value)

    def _resolve_forecast_intervals(
        self,
        forecast: ForecastSource,
        *,
        kind: InputValueKind,
    ) -> list[PowerForecastInterval] | list[PriceForecastInterval]:
        if kind is InputValueKind.PRICE:
            if isinstance(forecast, HomeAssistantAmberElectricForecastSource):
                return self._resolver.resolve(forecast)
            if isinstance(forecast, HomeAssistantAmberExpressForecastSource):
                return self._resolver.resolve(forecast)
            raise ValueError("Price forecast kind did not resolve to a price forecast source")
        if isinstance(forecast, HomeAssistantHistoricalAverageForecastSource):
            return self._resolver.resolve(forecast)
        if isinstance(forecast, HomeAssistantSolcastForecastSource):
            return self._resolver.resolve(forecast)
        raise ValueError("Power forecast kind did not resolve to a power forecast source")

    def _resolve_optional_realtime_value(
        self,
        *,
        realtime: ScalarSource | None,
        kind: InputValueKind,
    ) -> float | None:
        if realtime is None:
            return None
        try:
            value = self._resolver.resolve(
                self._scalar_entity_source(
                    entity=realtime.entity,
                    value_kind=kind,
                )
            )
        except ValueError:
            return None
        return float(value)

    def _resolve_extension_point_map(
        self,
        *,
        input_config: ForecastInputConfig,
        window: InputWindow,
        kind: InputValueKind,
    ) -> dict[str, float] | None:
        expansion = input_config.forecast_expansion
        realtime = input_config.realtime
        if kind is not InputValueKind.PRICE or expansion is None or realtime is None:
            return None
        extension_source = self._build_price_extension_source(
            realtime_entity=realtime.entity,
            forecast_horizon_hours=self._price_extension_horizon_hours(
                window=window,
                interval_duration=expansion.interval_duration,
            ),
            history_days=expansion.history_days,
            interval_duration=expansion.interval_duration,
        )
        extension_intervals = self._resolver.resolve(extension_source)
        return self._intervals_to_point_map(extension_intervals)

    def _resolve_extension_interval_minutes(
        self,
        *,
        input_config: ForecastInputConfig,
    ) -> int | None:
        expansion = input_config.forecast_expansion
        if expansion is None:
            return None
        return int(expansion.interval_duration)

    def _build_price_extension_source(
        self,
        *,
        realtime_entity: str,
        forecast_horizon_hours: int,
        history_days: int,
        interval_duration: int,
    ) -> HomeAssistantHistoricalAveragePriceForecastSource:
        return HomeAssistantHistoricalAveragePriceForecastSource(
            type="home_assistant",
            platform="historical_average_price",
            entity=realtime_entity,
            history_days=history_days,
            interval_duration=interval_duration,
            forecast_horizon_hours=max(1, int(forecast_horizon_hours)),
        )

    def _price_extension_horizon_hours(
        self,
        *,
        window: InputWindow,
        interval_duration: int,
    ) -> int:
        forecast_start = _floor_to_interval_boundary(window.now, interval_duration)
        required_minutes = int((window.end - forecast_start).total_seconds() / 60.0)
        hours, remainder = divmod(required_minutes, 60)
        return max(1, hours + (1 if remainder else 0))

    def _intervals_to_point_map(
        self,
        intervals: list[PowerForecastInterval] | list[PriceForecastInterval],
    ) -> dict[str, float]:
        ordered = sorted(intervals, key=lambda interval: interval.start)
        _validate_point_reconstruction(ordered)
        return {interval.start.isoformat(): float(interval.value) for interval in ordered}

    def _forecast_fallback_interval_minutes(
        self,
        *,
        intervals: list[PowerForecastInterval] | list[PriceForecastInterval],
        forecast: ForecastSource,
    ) -> int:
        ordered = sorted(intervals, key=lambda interval: interval.start)
        if not ordered:
            return _default_forecast_interval_minutes(forecast)
        last_duration = ordered[-1].end - ordered[-1].start
        minutes = int(round(last_duration.total_seconds() / 60.0))
        if minutes <= 0:
            raise ValueError("forecast intervals must have positive duration")
        return minutes

    def _scalar_entity_source(
        self,
        *,
        entity: str,
        value_kind: InputValueKind,
    ) -> HomeAssistantEntitySource[float] | HomeAssistantEntitySource[bool]:
        if value_kind is InputValueKind.BOOLEAN:
            return HomeAssistantBinarySensorEntitySource(type="home_assistant", entity=entity)
        if value_kind is InputValueKind.PERCENTAGE:
            return HomeAssistantPercentageEntitySource(type="home_assistant", entity=entity)
        if value_kind is InputValueKind.PRICE:
            return HomeAssistantCurrencyEntitySource(type="home_assistant", entity=entity)
        return HomeAssistantPowerKwEntitySource(type="home_assistant", entity=entity)


class FixtureResolvedInputProvider:
    def __init__(
        self,
        *,
        registry: ResolvedInputRegistry,
        price_watch_entity_ids: Iterable[str] | None = None,
    ) -> None:
        self._registry = registry
        self._price_watch_entity_ids = set(price_watch_entity_ids or [])

    def mark_for_hydration(self) -> None:
        return

    def hydrate_all(self) -> None:
        return

    def resolve_for_window(self, *, window: InputWindow) -> ResolvedInputRegistry:
        _ = window
        return self._registry

    def grid_price_watch_entity_ids(self) -> set[str]:
        return set(self._price_watch_entity_ids)


def _grid_components(app_config: AppConfig) -> list[GridComponentConfig]:
    return [
        component
        for component in app_config.plant.values()
        if isinstance(component, GridComponentConfig)
    ]


def _validate_point_reconstruction(
    intervals: tuple[PowerForecastInterval | PriceForecastInterval, ...]
    | list[PowerForecastInterval | PriceForecastInterval],
) -> None:
    if not intervals:
        return
    for interval in intervals:
        if interval.end <= interval.start:
            raise ValueError("forecast intervals must have positive duration")
    for prev, curr in zip(intervals, intervals[1:], strict=False):
        gap = curr.start - prev.end
        if abs(gap.total_seconds()) > timedelta(seconds=1).total_seconds():
            raise ValueError(
                "forecast intervals must be contiguous to be represented as raw point maps"
            )


def _default_forecast_interval_minutes(forecast: ForecastSource) -> int:
    if isinstance(
        forecast,
        (
            HomeAssistantHistoricalAverageForecastSource,
            HomeAssistantHistoricalAveragePriceForecastSource,
        ),
    ):
        return int(forecast.interval_duration)
    if isinstance(forecast, HomeAssistantSolcastForecastSource):
        return 30
    if isinstance(forecast, HomeAssistantAmberElectricForecastSource):
        return 30
    return 5


def _floor_to_interval_boundary(now: datetime, interval_minutes: int) -> datetime:
    minutes = (now.minute // interval_minutes) * interval_minutes
    return now.replace(minute=minutes, second=0, microsecond=0)

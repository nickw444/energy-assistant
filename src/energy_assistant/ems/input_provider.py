from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Protocol, cast

from energy_assistant.ems.forecast_alignment import (
    PowerForecastAligner,
    PriceForecastAligner,
    validate_forecast_coverage,
)
from energy_assistant.ems.horizon import Horizon, floor_to_interval_boundary
from energy_assistant.ems.input_registry import (
    ResolvedForecastInput,
    ResolvedInputRegistry,
    ResolvedScalarInput,
)
from energy_assistant.lib.source_resolver.hass_source import (
    HomeAssistantBinarySensorEntitySource,
    HomeAssistantCurrencyEntitySource,
    HomeAssistantEntitySource,
    HomeAssistantHistoricalAveragePriceForecastSource,
    HomeAssistantPercentageEntitySource,
    HomeAssistantPowerKwEntitySource,
)
from energy_assistant.lib.source_resolver.models import (
    PowerForecastInterval,
    PriceForecastInterval,
)
from energy_assistant.lib.source_resolver.resolver import ValueResolver
from energy_assistant.models.config import AppConfig
from energy_assistant.models.inputs import (
    ForecastExpansionConfig,
    ForecastInputConfig,
    ForecastSource,
    InputValueKind,
    ScalarInputConfig,
    input_value_kind,
)
from energy_assistant.models.plant import GridComponentConfig


class EmsInputProvider(Protocol):
    def mark_for_hydration(self) -> None: ...

    def hydrate_all(self) -> None: ...

    def resolve_for_horizon(self, *, horizon: Horizon) -> ResolvedInputRegistry: ...

    def grid_price_watch_entity_ids(self) -> set[str]: ...


class ResolverBackedInputProvider:
    def __init__(self, *, app_config: AppConfig, resolver: ValueResolver) -> None:
        self._app_config = app_config
        self._resolver = resolver
        self._power_aligner = PowerForecastAligner()
        self._price_aligner = PriceForecastAligner()

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

    def resolve_for_horizon(self, *, horizon: Horizon) -> ResolvedInputRegistry:
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
            forecasts[key] = ResolvedForecastInput(
                key=key,
                kind=kind,
                series=self._resolve_forecast(
                    key=key,
                    input_config=input_config,
                    horizon=horizon,
                    kind=kind,
                ),
            )
        return ResolvedInputRegistry(scalars=scalars, forecasts=forecasts)

    def grid_price_watch_entity_ids(self) -> set[str]:
        grid = _grid_component(self._app_config)
        entity_ids: set[str] = set()
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

    def _resolve_forecast(
        self,
        *,
        key: str,
        input_config: ForecastInputConfig,
        horizon: Horizon,
        kind: InputValueKind,
    ) -> list[float]:
        intervals = self._resolve_forecast_intervals(
            forecast=input_config.forecast,
            realtime=input_config.realtime,
            horizon=horizon,
            expansion=input_config.forecast_expansion,
            kind=kind,
        )
        validate_forecast_coverage(
            label=f"Input {key}",
            horizon=horizon,
            intervals=intervals,
            allow_first_slot_missing=input_config.realtime is not None,
        )
        first_slot_override: float | None = None
        if input_config.realtime is not None:
            try:
                realtime_value = self._resolver.resolve(
                    self._scalar_entity_source(
                        entity=input_config.realtime.entity,
                        value_kind=kind,
                    )
                )
            except ValueError:
                realtime_value = None
            if realtime_value is not None:
                first_slot_override = float(realtime_value)
        if kind is InputValueKind.PRICE:
            return [
                float(value)
                for value in self._price_aligner.align(
                    horizon,
                    cast(list[PriceForecastInterval], intervals),
                    first_slot_override=first_slot_override,
                )
            ]
        return [
            float(value)
            for value in self._power_aligner.align(
                horizon,
                cast(list[PowerForecastInterval], intervals),
                first_slot_override=first_slot_override,
            )
        ]

    def _resolve_forecast_intervals(
        self,
        *,
        forecast: ForecastSource,
        realtime: HomeAssistantCurrencyEntitySource | object,
        horizon: Horizon,
        expansion: ForecastExpansionConfig | None,
        kind: InputValueKind,
    ) -> list[PowerForecastInterval] | list[PriceForecastInterval]:
        intervals = cast(
            list[PowerForecastInterval] | list[PriceForecastInterval],
            cast(Any, self._resolver).resolve(forecast),
        )
        if kind is not InputValueKind.PRICE or expansion is None:
            return intervals
        extension_source = self._build_price_extension_source(
            realtime_entity=cast(HomeAssistantCurrencyEntitySource, realtime).entity,
            forecast_horizon_hours=self._price_extension_horizon_hours(
                horizon=horizon,
                interval_duration=expansion.interval_duration,
            ),
            history_days=expansion.history_days,
            interval_duration=expansion.interval_duration,
        )
        extension_intervals = self._resolver.resolve(extension_source)
        return _merge_price_intervals(
            base_intervals=cast(list[PriceForecastInterval], intervals),
            extension_intervals=extension_intervals,
        )

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

    def _price_extension_horizon_hours(self, *, horizon: Horizon, interval_duration: int) -> int:
        forecast_start = floor_to_interval_boundary(horizon.now, interval_duration)
        horizon_end = horizon.slots[-1].end
        required_minutes = int((horizon_end - forecast_start).total_seconds() / 60.0)
        hours, remainder = divmod(required_minutes, 60)
        return max(1, hours + (1 if remainder else 0))

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

    def resolve_for_horizon(self, *, horizon: Horizon) -> ResolvedInputRegistry:
        expected_length = horizon.num_intervals
        for key, value in self._registry.to_payload().items():
            if value.get("type") != "forecast":
                continue
            raw_series = value.get("series")
            if not isinstance(raw_series, list):
                raise ValueError(
                    f"Resolved fixture forecast {key} length does not match horizon intervals"
                )
            series = cast(list[object], raw_series)
            if len(series) != expected_length:
                raise ValueError(
                    f"Resolved fixture forecast {key} length does not match horizon intervals"
                )
        return self._registry

    def grid_price_watch_entity_ids(self) -> set[str]:
        return set(self._price_watch_entity_ids)


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
                value=float(interval.value),
            )
        )
    return merged


def _grid_component(app_config: AppConfig) -> GridComponentConfig:
    for component in app_config.plant.values():
        if isinstance(component, GridComponentConfig):
            return component
    raise ValueError("plant must define exactly one grid component")

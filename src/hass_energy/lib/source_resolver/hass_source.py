from __future__ import annotations

import datetime
from typing import Annotated, Literal, TypeVar, cast

from pydantic import ConfigDict, Field, model_validator

from hass_energy.lib.source_resolver.hass_provider import (
    HomeAssistantHistoryPayload,
    HomeAssistantStateDict,
)
from hass_energy.lib.source_resolver.models import PowerForecastInterval, PriceForecastInterval
from hass_energy.lib.source_resolver.sources import EntitySource

T = TypeVar("T")

_AMBER_INTERVAL_TOLERANCE_SECONDS = 2.0


def required_float(value: object) -> float:
    if value is None:
        raise ValueError("Value is required and cannot be None")
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float, str)):
        return float(value)
    raise TypeError(f"Unsupported value type: {type(value)!r}")


def required_bool(value: object) -> bool:
    if value is None:
        raise ValueError("Value is required and cannot be None")
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"on", "true", "1", "yes"}:
            return True
        if normalized in {"off", "false", "0", "no"}:
            return False
    return bool(value)


def _normalize_power_kw(value: float, unit: str | None) -> float:
    if not unit:
        return value
    normalized = unit.strip().lower()
    if normalized == "w":
        return value / 1000.0
    if normalized == "kw":
        return value
    if normalized == "mw":
        return value * 1000.0
    return value


def _parse_timestamp(value: object) -> datetime.datetime | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        return datetime.datetime.fromisoformat(text)
    except ValueError:
        return None


def _amber_price_value(
    item: dict[str, object],
    mode: Literal[
        "spot",
        "advanced",
        "blend_min",
        "blend_max",
        "blend_mean",
    ]
    | None,
) -> float | None:
    spot_value: float | None = None
    advanced_value: float | None = None
    if "per_kwh" in item:
        spot_value = required_float(item.get("per_kwh"))
    if "advanced_price_predicted" in item:
        raw_advanced = item.get("advanced_price_predicted")
        if raw_advanced is not None:
            advanced_value = required_float(raw_advanced)

    if mode is None:
        return spot_value

    if mode == "spot":
        if spot_value is None:
            raise ValueError("Spot price is required for Amber Electric spot mode")
        return spot_value
    if mode == "advanced":
        return advanced_value if advanced_value is not None else spot_value
    if spot_value is None:
        return advanced_value
    if advanced_value is None:
        return spot_value
    if mode == "blend_min":
        return min(spot_value, advanced_value)
    if mode == "blend_max":
        return max(spot_value, advanced_value)
    return (spot_value + advanced_value) / 2.0


class HomeAssistantEntitySource(EntitySource[HomeAssistantStateDict, T]):
    type: Literal["home_assistant"]
    entity: str = Field(min_length=1)

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class HomeAssistantMultiEntitySource(EntitySource[list[HomeAssistantStateDict], T]):
    type: Literal["home_assistant"]
    entities: list[Annotated[str, Field(min_length=1)]] = Field(min_length=1)

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class HomeAssistantHistoryEntitySource(EntitySource[HomeAssistantHistoryPayload, T]):
    type: Literal["home_assistant"]
    entity: str = Field(min_length=1)
    history_days: int = Field(ge=1)

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class HomeAssistantPowerKwEntitySource(HomeAssistantEntitySource[float]):
    # Take raw state and normalize to power in kW
    def mapper(self, state: HomeAssistantStateDict) -> float:
        raw = required_float(state["state"])
        unit = state["attributes"].get("unit_of_measurement")
        return _normalize_power_kw(raw, unit if isinstance(unit, str) else None)


class HomeAssistantBinarySensorEntitySource(HomeAssistantEntitySource[bool]):
    # Take raw state and normalize to boolean
    def mapper(self, state: HomeAssistantStateDict) -> bool:
        return required_bool(state["state"])


class HomeAssistantPercentageEntitySource(HomeAssistantEntitySource[float]):
    # Take raw state and normalize to percentage (0-100)
    def mapper(self, state: HomeAssistantStateDict) -> float:
        return required_float(state["state"])


class HomeAssistantCurrencyEntitySource(HomeAssistantEntitySource[float]):
    # Take raw state and normalize to currency value
    def mapper(self, state: HomeAssistantStateDict) -> float:
        return required_float(state["state"])


class HomeAssistantAmberElectricForecastSource(
    HomeAssistantEntitySource[list[PriceForecastInterval]]
):
    type: Literal["home_assistant"]
    platform: Literal["amberelectric"]
    entity: str = Field(min_length=1)
    price_forecast_mode: Literal[
        "spot",
        "advanced",
        "blend_min",
        "blend_max",
        "blend_mean",
    ] | None = None

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    def mapper(self, state: HomeAssistantStateDict) -> list[PriceForecastInterval]:
        attributes = state["attributes"]
        forecasts_raw = attributes.get("forecasts")
        if not isinstance(forecasts_raw, list):
            return []
        forecasts_raw = cast(list[object], forecasts_raw)

        intervals: list[PriceForecastInterval] = []
        forecasts: list[dict[str, object]] = []
        for item in forecasts_raw:
            if isinstance(item, dict):
                forecasts.append(cast(dict[str, object], item))
        for item in forecasts:
            start = _parse_timestamp(item.get("start_time") or item.get("nem_date"))
            end = _parse_timestamp(item.get("end_time"))
            
            duration = item.get("duration")
            has_duration = isinstance(duration, (int, float))
            if start is None:
                continue
            if end is None and not has_duration:
                continue

            if has_duration:
                # check for an intolderence issue
                expected_end = start + datetime.timedelta(minutes=float(duration))
                if end is None:
                    end = expected_end
                else:
                    # Amber intervals can be offset by a second; snap to duration end when drift is notable.
                    diff_seconds = abs((end - expected_end).total_seconds())
                    if diff_seconds >= _AMBER_INTERVAL_TOLERANCE_SECONDS:
                        end = expected_end
            if end is None:
                continue


            value = _amber_price_value(
                item,
                self.price_forecast_mode,
            )
            if value is None:
                continue

            intervals.append(PriceForecastInterval(start=start, end=end, value=value))

        return intervals


class HomeAssistantSolcastForecastSource(
    HomeAssistantMultiEntitySource[list[PowerForecastInterval]]
):
    platform: Literal["solcast"]

    type: Literal["home_assistant"]
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    def mapper(self, state: list[HomeAssistantStateDict]) -> list[PowerForecastInterval]:
        forecasts: list[PowerForecastInterval] = []
        for state_item in state:
            detailed_forecast = cast(
                list[dict[str, object]],
                state_item["attributes"]["detailedForecast"],
            )
            for solcast_interval in detailed_forecast:
                forecast_interval = self._solcast_to_forecast_interval(solcast_interval)
                forecasts.append(forecast_interval)

        return forecasts

    def _solcast_to_forecast_interval(
        self,
        solcast_interval: dict[str, object],
    ) -> PowerForecastInterval:
        start_dt = datetime.datetime.fromisoformat(str(solcast_interval["period_start"]))
        end_dt = start_dt + datetime.timedelta(minutes=30)
        value_kw = required_float(cast(object, solcast_interval.get("pv_estimate")))
        return PowerForecastInterval(
            start=start_dt,
            end=end_dt,
            value=value_kw,
        )


class HomeAssistantHistoricalAverageForecastSource(
    HomeAssistantHistoryEntitySource[list[PowerForecastInterval]]
):
    """Build a rolling average forecast from historical state values.

    Uses the last `history_days` of state history to compute a time-of-day
    average for each `interval_duration` bucket. The output repeats that
    daily profile for `forecast_horizon_hours`, starting at the top of the
    current hour and aligned to the bucket interval size. The history state
    unit is provided by `unit`.
    """

    platform: Literal["historical_average"]
    unit: str = Field(min_length=1)
    interval_duration: int = Field(default=5, ge=1, le=60)
    forecast_horizon_hours: int = Field(default=24, ge=1)
    realtime_window_minutes: int | None = Field(default=None, ge=1)

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    @model_validator(mode="after")
    def _validate_interval_duration(self) -> HomeAssistantHistoricalAverageForecastSource:
        if 60 % self.interval_duration != 0:
            raise ValueError("interval_duration must evenly divide 60 minutes")
        normalized = self.unit.strip().lower()
        if normalized not in {"w", "kw", "mw"}:
            raise ValueError("unit must be one of: W, kW, MW")
        return self

    def mapper(
        self,
        state: HomeAssistantHistoryPayload,
    ) -> list[PowerForecastInterval]:
        history = state.history
        current_state = state.current_state
        entries: list[tuple[datetime.datetime, float]] = []
        unit = self.unit
        for item in history:
            timestamp = _parse_timestamp(item.get("last_updated") or item.get("last_changed"))
            if timestamp is None:
                continue
            try:
                value = required_float(item.get("state"))
            except (TypeError, ValueError):
                continue
            entries.append((timestamp, _normalize_power_kw(value, unit)))

        if not entries:
            return []

        entries.sort(key=lambda item: item[0])
        tz = entries[0][0].tzinfo or datetime.UTC
        normalized: list[tuple[datetime.datetime, float]] = []
        for ts, value in entries:
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=tz)
            else:
                ts = ts.astimezone(tz)
            normalized.append((ts, value))
        entries = normalized

        now = datetime.datetime.now(tz=tz)
        if now <= entries[-1][0]:
            now = entries[-1][0] + datetime.timedelta(minutes=self.interval_duration)

        interval_minutes = self.interval_duration
        buckets_per_day = (24 * 60) // interval_minutes
        bucket_sums = [0.0] * buckets_per_day
        bucket_seconds = [0.0] * buckets_per_day

        for idx, (start, value) in enumerate(entries):
            end = entries[idx + 1][0] if idx + 1 < len(entries) else now
            if end <= start:
                continue
            current = start
            while current < end:
                interval_start_minute = (current.minute // interval_minutes) * interval_minutes
                interval_start = current.replace(
                    minute=interval_start_minute,
                    second=0,
                    microsecond=0,
                )
                interval_end = interval_start + datetime.timedelta(minutes=interval_minutes)
                overlap_end = interval_end if interval_end < end else end
                seconds = (overlap_end - current).total_seconds()
                bucket = (interval_start.hour * 60 + interval_start.minute) // interval_minutes
                bucket_sums[bucket] += value * seconds
                bucket_seconds[bucket] += seconds
                current = overlap_end

        averages = [
            (bucket_sums[i] / bucket_seconds[i]) if bucket_seconds[i] > 0 else 0.0
            for i in range(buckets_per_day)
        ]

        start_time = now.replace(minute=0, second=0, microsecond=0)
        horizon_minutes = self.forecast_horizon_hours * 60
        num_intervals = horizon_minutes // interval_minutes
        intervals: list[PowerForecastInterval] = []
        for offset in range(num_intervals):
            interval_start = start_time + datetime.timedelta(minutes=offset * interval_minutes)
            interval_end = interval_start + datetime.timedelta(minutes=interval_minutes)
            bucket = (interval_start.hour * 60 + interval_start.minute) // interval_minutes
            intervals.append(
                PowerForecastInterval(
                    start=interval_start,
                    end=interval_end,
                    value=averages[bucket],
                )
            )
        self._apply_realtime_smoothing(intervals, now, current_state)
        return intervals

    def _apply_realtime_smoothing(
        self,
        intervals: list[PowerForecastInterval],
        now: datetime.datetime,
        current_state: HomeAssistantStateDict,
    ) -> None:
        if not intervals or self.realtime_window_minutes is None:
            return
        try:
            raw_value = required_float(current_state.get("state"))
        except (TypeError, ValueError):
            return
        attributes = current_state.get("attributes", {})
        unit = attributes.get("unit_of_measurement")
        unit_value = unit if isinstance(unit, str) else self.unit
        realtime_kw = _normalize_power_kw(raw_value, unit_value)
        window = datetime.timedelta(minutes=self.realtime_window_minutes)
        if window.total_seconds() <= 0:
            return
        window_end = now + window
        for interval in intervals:
            if interval.end <= now:
                continue
            if interval.start >= window_end:
                break
            progress = (interval.start - now).total_seconds() / window.total_seconds()
            progress = max(0.0, min(1.0, progress))
            interpolated = realtime_kw + (interval.value - realtime_kw) * progress
            if interpolated > interval.value:
                interval.value = interpolated

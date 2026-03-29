from __future__ import annotations

import json
from pathlib import Path
from typing import cast

from energy_assistant.ems.fixture_inputs import save_resolved_inputs_fixture
from energy_assistant.ems.input_registry import ResolvedForecastInput, ResolvedInputRegistry
from energy_assistant.models.inputs import InputValueKind


def test_resolved_input_registry_round_trips_raw_forecast_payload() -> None:
    registry = ResolvedInputRegistry(
        forecasts={
            "grid_price_import": ResolvedForecastInput(
                key="grid_price_import",
                kind=InputValueKind.PRICE,
                points={
                    "2025-01-01T00:00:00+00:00": 0.10,
                    "2025-01-01T00:30:00+00:00": 0.20,
                },
                interval_minutes=30,
                realtime_value=0.15,
                extension_points={
                    "2025-01-01T01:00:00+00:00": 0.25,
                },
                extension_interval_minutes=30,
            )
        }
    )

    payload = registry.to_payload()
    restored = ResolvedInputRegistry.from_payload(cast(dict[str, object], payload))
    restored_forecast = restored.forecast("grid_price_import", kind=InputValueKind.PRICE)

    assert restored_forecast.points == {
        "2025-01-01T00:00:00+00:00": 0.10,
        "2025-01-01T00:30:00+00:00": 0.20,
    }
    assert restored_forecast.interval_minutes == 30
    assert restored_forecast.realtime_value == 0.15
    assert restored_forecast.extension_points == {
        "2025-01-01T01:00:00+00:00": 0.25,
    }
    assert restored_forecast.extension_interval_minutes == 30


def test_save_resolved_inputs_fixture_rounds_numeric_storage(tmp_path: Path) -> None:
    fixture_path = tmp_path / "ems_fixture.json"
    registry = ResolvedInputRegistry(
        scalars={},
        forecasts={
            "base_load_power": ResolvedForecastInput(
                key="base_load_power",
                kind=InputValueKind.POWER,
                points={
                    "2025-01-01T00:00:00+00:00": 1.1407434374638563,
                    "2025-01-01T00:30:00+00:00": 0.3333333333333333,
                },
                interval_minutes=30,
                realtime_value=1.987654321,
                extension_points={
                    "2025-01-01T01:00:00+00:00": 2.123456789,
                },
                extension_interval_minutes=30,
            )
        },
    )

    save_resolved_inputs_fixture(
        path=fixture_path,
        captured_at="2025-01-01T00:00:00+00:00",
        inputs=registry,
    )

    payload = json.loads(fixture_path.read_text())
    forecast = payload["inputs"]["base_load_power"]

    assert forecast["points"]["2025-01-01T00:00:00+00:00"] == 1.1407434374638563
    assert forecast["points"]["2025-01-01T00:30:00+00:00"] == 0.3333333333333333
    assert forecast["realtime_value"] == 1.987654321
    assert forecast["extension_points"]["2025-01-01T01:00:00+00:00"] == 2.123456789


def test_save_resolved_inputs_fixture_rounds_non_power_forecasts_more_aggressively(
    tmp_path: Path,
) -> None:
    fixture_path = tmp_path / "ems_fixture.json"
    registry = ResolvedInputRegistry(
        forecasts={
            "grid_price_import": ResolvedForecastInput(
                key="grid_price_import",
                kind=InputValueKind.PRICE,
                points={
                    "2025-01-01T00:00:00+00:00": 0.123456789,
                },
                interval_minutes=30,
                realtime_value=0.987654321,
                extension_points={
                    "2025-01-01T00:30:00+00:00": 0.111111111,
                },
                extension_interval_minutes=30,
            )
        }
    )

    save_resolved_inputs_fixture(
        path=fixture_path,
        captured_at="2025-01-01T00:00:00+00:00",
        inputs=registry,
    )

    payload = json.loads(fixture_path.read_text())
    forecast = payload["inputs"]["grid_price_import"]

    assert forecast["points"]["2025-01-01T00:00:00+00:00"] == 0.123457
    assert forecast["realtime_value"] == 0.987654
    assert forecast["extension_points"]["2025-01-01T00:30:00+00:00"] == 0.111111

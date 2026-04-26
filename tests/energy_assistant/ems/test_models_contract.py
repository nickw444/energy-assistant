from __future__ import annotations

from datetime import UTC, datetime

from pydantic import TypeAdapter

from energy_assistant.ems.models import (
    BaseLoadComponentPlan,
    ComponentPlan,
    EmsPlanOutput,
    EmsPlanTimings,
)
from energy_assistant.ems.series_types import EmsSeriesPoint


def test_component_plan_discriminator_parses_grid_type() -> None:
    payload = {
        "type": "grid",
        "price_import_raw": [{"time": "2026-01-01T00:00:00Z", "value": 1.0}],
        "price_export_raw": [{"time": "2026-01-01T00:00:00Z", "value": 0.5}],
        "price_import_effective": [{"time": "2026-01-01T00:00:00Z", "value": 1.0}],
        "price_export_effective": [{"time": "2026-01-01T00:00:00Z", "value": 0.5}],
        "import_allowed": [{"time": "2026-01-01T00:00:00Z", "value": True}],
        "import_kw": [{"time": "2026-01-01T00:00:00Z", "value": 0.0}],
        "export_kw": [{"time": "2026-01-01T00:00:00Z", "value": 0.0}],
        "net_kw": [{"time": "2026-01-01T00:00:00Z", "value": 0.0}],
    }
    adapter: TypeAdapter[ComponentPlan] = TypeAdapter(ComponentPlan)
    model: ComponentPlan = adapter.validate_python(payload)
    assert model.type == "grid"


def test_ems_plan_output_rounds_objective_value_on_json_dump() -> None:
    point = EmsSeriesPoint(time=datetime(2026, 1, 1, tzinfo=UTC), value=1.0)
    plan = EmsPlanOutput(
        generated_at=datetime(2026, 1, 1, tzinfo=UTC),
        status="Optimal",
        objective_value=1.23456,
        timings=EmsPlanTimings(build_seconds=0.1, solve_seconds=0.2, total_seconds=0.3),
        components={
            "load": BaseLoadComponentPlan(power_kw=[point]),
        },
    )

    payload = plan.model_dump(mode="json")
    assert payload["objective_value"] == 1.235

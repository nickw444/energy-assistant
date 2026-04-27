from __future__ import annotations

from pathlib import Path

from energy_assistant.ems.fixtures.harness import (
    EmsFixturePaths,
    resolve_ems_fixture_paths,
)
from energy_assistant.ems.models import EmsPlanOutput
from energy_assistant.plotting import write_plan_svg

FIXTURE_BASE = Path("tests/fixtures/ems")


def _is_complete_bundle(paths: EmsFixturePaths) -> bool:
    return bool(
        paths.fixture_path.exists()
        and paths.config_path.exists()
        and paths.plan_path.exists()
    )


def _discover_scenarios(base_dir: Path) -> list[tuple[str, str | None]]:
    if not base_dir.exists():
        return []
    scenarios: list[tuple[str, str | None]] = []
    for fixture_child in base_dir.iterdir():
        if not fixture_child.is_dir():
            continue
        fixture_name = fixture_child.name
        paths = resolve_ems_fixture_paths(base_dir, fixture_name, None)
        if _is_complete_bundle(paths):
            scenarios.append((fixture_name, None))
        for scenario_child in fixture_child.iterdir():
            if not scenario_child.is_dir():
                continue
            scenario_paths = resolve_ems_fixture_paths(base_dir, fixture_name, scenario_child.name)
            if _is_complete_bundle(scenario_paths):
                scenarios.append((fixture_name, scenario_child.name))
    return sorted(scenarios, key=lambda item: (item[0], item[1] or ""))


def _refresh_scenario(paths: EmsFixturePaths) -> None:
    plan = EmsPlanOutput.model_validate_json(paths.plan_path.read_text())
    write_plan_svg(plan, paths.plot_path)


def main() -> int:
    scenarios = _discover_scenarios(FIXTURE_BASE)
    if not scenarios:
        print(f"No EMS fixture scenarios found under {FIXTURE_BASE}.")
        return 0

    refreshed: list[str] = []
    for fixture, scenario in scenarios:
        paths = resolve_ems_fixture_paths(FIXTURE_BASE, fixture, scenario)
        if not _is_complete_bundle(paths):
            continue

        missing_plot = not paths.plot_path.exists()
        legacy_plot = paths.scenario_dir / "output.jpeg"

        if not (missing_plot or legacy_plot.exists()):
            continue

        reasons = []
        if missing_plot:
            reasons.append("missing plot")
        if legacy_plot.exists():
            reasons.append("legacy JPEG present")

        label = fixture if scenario is None else f"{fixture}/{scenario}"
        print(f"Refreshing {label} ({', '.join(reasons)}).")
        _refresh_scenario(paths)
        if legacy_plot.exists():
            legacy_plot.unlink()
        refreshed.append(label)

    if refreshed:
        print(f"Refreshed {len(refreshed)} scenario(s): {', '.join(refreshed)}.")
    else:
        print("All EMS fixture plots are up to date.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

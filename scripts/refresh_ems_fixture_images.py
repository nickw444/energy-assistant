from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from energy_assistant.ems.fixtures.harness import (
    EmsFixturePaths,
    compute_plan_hash,
    resolve_ems_fixture_paths,
)

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


def _expected_hash(paths: EmsFixturePaths) -> str:
    payload = json.loads(paths.plan_path.read_text())
    return compute_plan_hash(payload)


def _refresh_scenario(fixture: str, scenario: str | None, *, force_image: bool) -> None:
    cmd = [
        sys.executable,
        "-m",
        "energy_assistant.cli",
        "ems",
        "refresh-baseline",
        "--fixture",
        fixture,
    ]
    if scenario is not None:
        cmd.extend(["--name", scenario])
    if force_image:
        cmd.append("--force-image")
    subprocess.run(cmd, check=True)


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
        missing_hash = not paths.hash_path.exists()
        expected_hash = _expected_hash(paths)
        stored_hash = paths.hash_path.read_text().strip() if paths.hash_path.exists() else None
        hash_mismatch = stored_hash is not None and stored_hash != expected_hash

        if not (missing_plot or missing_hash or hash_mismatch):
            continue

        reasons = []
        if missing_plot:
            reasons.append("missing image")
        if missing_hash:
            reasons.append("missing hash")
        if hash_mismatch:
            reasons.append("hash mismatch")

        label = fixture if scenario is None else f"{fixture}/{scenario}"
        print(f"Refreshing {label} ({', '.join(reasons)}).")
        _refresh_scenario(fixture, scenario, force_image=missing_plot)
        refreshed.append(label)

    if refreshed:
        print(f"Refreshed {len(refreshed)} scenario(s): {', '.join(refreshed)}.")
    else:
        print("All EMS fixture images are up to date.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

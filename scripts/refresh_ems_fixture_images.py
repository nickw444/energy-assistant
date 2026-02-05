from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from energy_assistant.ems.fixture_harness import (
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


def _discover_scenarios(base_dir: Path) -> list[str]:
    if not base_dir.exists():
        return []
    scenarios: list[str] = []
    for child in base_dir.iterdir():
        if not child.is_dir():
            continue
        paths = resolve_ems_fixture_paths(base_dir, child.name)
        if _is_complete_bundle(paths):
            scenarios.append(child.name)
    return sorted(scenarios)


def _expected_hash(paths: EmsFixturePaths) -> str:
    payload = json.loads(paths.plan_path.read_text())
    return compute_plan_hash(payload)


def _refresh_scenario(name: str, *, force_image: bool) -> None:
    cmd = [
        sys.executable,
        "-m",
        "energy_assistant.cli",
        "ems",
        "refresh-baseline",
        "--name",
        name,
    ]
    if force_image:
        cmd.append("--force-image")
    subprocess.run(cmd, check=True)


def main() -> int:
    scenarios = _discover_scenarios(FIXTURE_BASE)
    if not scenarios:
        print(f"No EMS fixture scenarios found under {FIXTURE_BASE}.")
        return 0

    refreshed: list[str] = []
    for scenario in scenarios:
        paths = resolve_ems_fixture_paths(FIXTURE_BASE, scenario)
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

        print(f"Refreshing {scenario} ({', '.join(reasons)}).")
        _refresh_scenario(scenario, force_image=missing_plot)
        refreshed.append(scenario)

    if refreshed:
        print(f"Refreshed {len(refreshed)} scenario(s): {', '.join(refreshed)}.")
    else:
        print("All EMS fixture images are up to date.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from hass_energy.config import load_app_config
from hass_energy.lib import HomeAssistantClient
from hass_energy.worker.milp import MilpPlanner
from hass_energy.worker.milp.ha_dump import map_states_to_realtime


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run MILP planner standalone.")
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config.yaml"),
        help="Path to YAML config",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    args = build_parser().parse_args(argv)

    app_config = load_app_config(args.config)
    ha_client = HomeAssistantClient()
    states_payload = ha_client.fetch_realtime_state(app_config.energy)
    realtime_state = map_states_to_realtime(
        states_payload,
        forecast_window_hours=app_config.energy.forecast_window_hours,
    )

    planner = MilpPlanner()
    plan = planner.generate_plan(app_config.energy, realtime_state=realtime_state, history=[])
    print(json.dumps(plan, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

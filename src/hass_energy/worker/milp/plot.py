from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import matplotlib.dates as mdates
import matplotlib.pyplot as plt


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Visualize MILP plan time-series outputs from a JSON file.",
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("plan.json"),
        help="Path to the planner output JSON (use '-' for stdin)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional path to save the plot (PNG). If omitted, shows an interactive window.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    args = build_parser().parse_args(argv)

    data = _load_json(args.input)
    slots_raw = data.get("slots")
    if not isinstance(slots_raw, list):
        raise ValueError("Expected 'slots' list in planner output")
    slots = cast(list[Any], slots_raw)

    times: list[datetime] = []
    grid_kw: list[float] = []
    pv_kw: list[float] = []
    load_kw: list[float] = []
    import_price: list[float] = []
    export_price: list[float] = []
    slot_costs: list[float] = []
    battery_series: dict[str, dict[str, list[float]]] = {}
    ev_series: dict[str, list[float]] = {}
    deferrable_series: dict[str, list[int]] = {}

    local_tz = datetime.now().astimezone().tzinfo
    if local_tz is None:
        local_tz = UTC

    for slot in slots:
        if not isinstance(slot, dict):
            continue
        slot_dict = cast(dict[str, Any], slot)
        start_raw = slot_dict.get("start")
        if not isinstance(start_raw, str):
            continue
        start_dt = datetime.fromisoformat(start_raw)
        if start_dt.tzinfo is None:
            start_dt = start_dt.replace(tzinfo=local_tz)
        else:
            start_dt = start_dt.astimezone(local_tz)
        times.append(start_dt)
        grid_kw.append(float(slot_dict.get("grid_kw", 0.0)))
        pv_kw.append(float(slot_dict.get("pv_kw", 0.0)))
        load_kw.append(float(slot_dict.get("load_kw", 0.0)))
        import_price.append(float(slot_dict.get("import_price", 0.0)))
        export_price.append(float(slot_dict.get("export_price", 0.0)))
        slot_costs.append(float(slot_dict.get("slot_cost", 0.0)))

        battery = slot_dict.get("battery")
        if isinstance(battery, dict):
            battery_dict = cast(dict[str, Any], battery)
            for name, payload in battery_dict.items():
                if not isinstance(payload, dict):
                    continue
                payload_dict = cast(dict[str, Any], payload)
                series = battery_series.setdefault(
                    name,
                    {"power_kw": [], "soc_kwh": []},
                )
                series["power_kw"].append(float(payload_dict.get("power_kw", 0.0)))
                series["soc_kwh"].append(float(payload_dict.get("soc_kwh", 0.0)))

        ev = slot_dict.get("ev")
        if isinstance(ev, dict):
            ev_dict = cast(dict[str, Any], ev)
            for name, payload in ev_dict.items():
                if not isinstance(payload, dict):
                    continue
                payload_dict = cast(dict[str, Any], payload)
                ev_series.setdefault(name, []).append(
                    float(payload_dict.get("charge_kw", 0.0))
                )

        deferrable = slot_dict.get("deferrable")
        if isinstance(deferrable, dict):
            deferrable_dict = cast(dict[str, Any], deferrable)
            for name, payload in deferrable_dict.items():
                if not isinstance(payload, dict):
                    continue
                payload_dict = cast(dict[str, Any], payload)
                deferrable_series.setdefault(name, []).append(
                    1 if payload_dict.get("on") else 0
                )

    if not times:
        raise ValueError("No valid slots found to plot")

    plt_any = cast(Any, plt)
    fig, axes = cast(tuple[Any, Any], plt_any.subplots(5, 1, sharex=True, figsize=(12, 13)))
    axes_list = cast(list[Any], axes)

    ax_inputs = axes_list[0]
    ax_inputs.plot(times, load_kw, label="input_load_kw", color="tab:blue")
    ax_inputs.plot(times, pv_kw, label="input_pv_kw", color="tab:green")
    ax_inputs.set_ylabel("Inputs (kW)")
    ax_inputs.legend(loc="upper right", ncol=2, fontsize=8)
    ax_inputs.grid(True, alpha=0.3)

    ax_power = axes_list[1]
    ax_power.plot(times, grid_kw, label="output_grid_kw", color="tab:orange")
    for name, series in battery_series.items():
        ax_power.plot(
            times,
            series["power_kw"],
            label=f"{name}_battery_kw",
            linestyle="-.",
        )
    for name, series in ev_series.items():
        ax_power.plot(times, series, label=f"{name}_ev_kw", linestyle=":")
    ax_power.set_ylabel("Outputs (kW)")
    ax_power.legend(loc="upper right", ncol=2, fontsize=8)
    ax_power.grid(True, alpha=0.3)

    ax_price_inputs = axes_list[2]
    ax_price_inputs.plot(times, import_price, label="input_import_price", color="tab:purple")
    ax_price_inputs.plot(times, export_price, label="input_export_price", color="tab:brown")
    ax_price_inputs.set_ylabel("Inputs ($/kWh)")
    ax_price_inputs.legend(loc="upper right", ncol=2, fontsize=8)
    ax_price_inputs.grid(True, alpha=0.3)

    ax_soc = axes_list[3]
    for name, series in battery_series.items():
        ax_soc.plot(times, series["soc_kwh"], label=f"{name}_soc_kwh")
    for name, series in deferrable_series.items():
        ax_soc.step(times, series, label=f"{name}_on", where="post")
    ax_soc.set_ylabel("SOC (kWh) / On")
    ax_soc.legend(loc="upper right", ncol=2, fontsize=8)
    ax_soc.grid(True, alpha=0.3)

    ax_cost = axes_list[4]
    ax_cost.plot(times, slot_costs, label="slot_cost", color="tab:gray")
    ax_cost.set_ylabel("Cost (AUD)")
    ax_cost.legend(loc="upper right", ncol=2, fontsize=8)
    ax_cost.grid(True, alpha=0.3)

    ax_soc.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M", tz=local_tz))
    fig.autofmt_xdate()
    fig.suptitle("MILP Plan Time-Series")
    fig.tight_layout()

    if args.output:
        fig.savefig(args.output, dpi=150)
    else:
        plt_any.show()
    return 0


def _load_json(path: Path) -> dict[str, Any]:
    if str(path) == "-":
        data = json.loads(sys.stdin.read())
    else:
        if not path.exists():
            raise FileNotFoundError(f"Input JSON not found: {path}")
        data = json.loads(path.read_text())
    if not isinstance(data, dict):
        raise ValueError("Expected JSON object at top level")
    return cast(dict[str, Any], data)


if __name__ == "__main__":
    raise SystemExit(main())

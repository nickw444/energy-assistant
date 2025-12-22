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
    pv_curtail_kw: list[float] = []
    load_kw: list[float] = []
    import_price: list[float] = []
    export_price: list[float] = []
    slot_costs: list[float] = []
    cumulative_costs: list[float] = []
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
        pv_curtail_kw.append(float(slot_dict.get("pv_curtail_kw", 0.0)))
        load_kw.append(float(slot_dict.get("load_kw", 0.0)))
        import_price.append(float(slot_dict.get("import_price", 0.0)))
        export_price.append(float(slot_dict.get("export_price", 0.0)))
        slot_costs.append(float(slot_dict.get("slot_cost", 0.0)))
        cumulative_costs.append(slot_costs[-1] + (cumulative_costs[-1] if cumulative_costs else 0.0))

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
    fig, axes = cast(tuple[Any, Any], plt_any.subplots(6, 1, sharex=True, figsize=(12, 15)))
    axes_list = cast(list[Any], axes)

    ax_inputs = axes_list[0]
    ax_inputs.plot(times, load_kw, label="input_load_kw", color="tab:blue")
    ax_inputs.plot(times, pv_kw, label="input_pv_kw", color="tab:green")
    ax_inputs.plot(
        times,
        pv_curtail_kw,
        label="output_pv_curtail_kw",
        color="tab:red",
        linestyle="--",
    )
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

    ax_cumulative = axes_list[5]
    ax_cumulative.plot(times, cumulative_costs, label="cumulative_cost", color="tab:olive")
    ax_cumulative.set_ylabel("Cumulative Cost (AUD)")
    ax_cumulative.legend(loc="upper right", ncol=2, fontsize=8)
    ax_cumulative.grid(True, alpha=0.3)

    ax_cumulative.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M", tz=local_tz))
    fig.autofmt_xdate()
    fig.suptitle("MILP Plan Time-Series")
    fig.tight_layout()

    if args.output:
        fig.savefig(args.output, dpi=150)
    else:
        _enable_hover(fig, axes_list, local_tz)
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


def _enable_hover(fig: Any, axes: list[Any], tz: Any) -> None:
    annotations: dict[Any, Any] = {}
    line_meta: dict[Any, list[tuple[Any, str, list[float], list[float]]]] = {}

    for ax in axes:
        annot = ax.annotate(
            "",
            xy=(0, 0),
            xytext=(10, 10),
            textcoords="offset points",
            bbox={"boxstyle": "round", "fc": "white", "alpha": 0.85},
            arrowprops={"arrowstyle": "->", "alpha": 0.4},
        )
        annot.set_visible(False)
        annotations[ax] = annot

        lines: list[tuple[Any, str, list[float], list[float]]] = []
        for line in ax.get_lines():
            label = line.get_label()
            if not label or label.startswith("_"):
                continue
            x_raw = list(line.get_xdata())
            y_raw = list(line.get_ydata())
            x_nums = [float(x) for x in mdates.date2num(x_raw)]
            y_vals = [float(y) for y in y_raw]
            lines.append((line, label, x_nums, y_vals))
        line_meta[ax] = lines

    def _format(label: str, x_num: float, y_val: float) -> str:
        dt = mdates.num2date(x_num, tz=tz)
        return f"{label}\n{dt:%Y-%m-%d %H:%M}\n{y_val:.3f}"

    def on_move(event: Any) -> None:
        if event.inaxes is None or event.x is None or event.y is None:
            changed = False
            for annot in annotations.values():
                if annot.get_visible():
                    annot.set_visible(False)
                    changed = True
            if changed:
                fig.canvas.draw_idle()
            return

        ax = event.inaxes
        lines = line_meta.get(ax, [])
        if not lines or event.xdata is None or event.ydata is None:
            return

        best: tuple[Any, str, float, float] | None = None
        best_dist = float("inf")
        for _line, label, x_nums, y_vals in lines:
            for x_num, y_val in zip(x_nums, y_vals, strict=False):
                disp_x, disp_y = ax.transData.transform((x_num, y_val))
                dx = disp_x - event.x
                dy = disp_y - event.y
                dist = dx * dx + dy * dy
                if dist < best_dist:
                    best_dist = dist
                    best = (_line, label, x_num, y_val)

        annot = annotations[ax]
        if best is None or best_dist > 100:
            if annot.get_visible():
                annot.set_visible(False)
                fig.canvas.draw_idle()
            return

        _, label, x_num, y_val = best
        annot.xy = (x_num, y_val)
        annot.set_text(_format(label, x_num, y_val))
        annot.set_visible(True)
        fig.canvas.draw_idle()

    fig.canvas.mpl_connect("motion_notify_event", on_move)


if __name__ == "__main__":
    raise SystemExit(main())

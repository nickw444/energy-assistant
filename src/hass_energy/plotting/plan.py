from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime, tzinfo
from pathlib import Path
from typing import Any


def plot_plan(
    plan: object,
    *,
    title: str = "Energy Plan",
    output: Path | None = None,
) -> None:
    try:
        import matplotlib.pyplot as plt
    except ImportError as exc:
        raise ImportError("matplotlib is required to plot plans") from exc

    slots = _extract_slots(plan)
    if not slots:
        raise ValueError("Plan has no slots to plot.")

    local_tz = datetime.now().astimezone().tzinfo or UTC
    times: list[datetime] = []
    for slot in slots:
        slot_time = _parse_time(slot.get("start"), local_tz=local_tz)
        times.append(slot_time or datetime.now(tz=local_tz))

    grid_import = _series(slots, "grid_import_kw", "grid_import", "import_kw", "import")
    grid_export = _series(slots, "grid_export_kw", "grid_export", "export_kw", "export")
    grid_net = _series(slots, "grid_kw", "grid_net_kw", "net_grid_kw")
    if not _has_any(grid_net) and (_has_any(grid_import) or _has_any(grid_export)):
        grid_net = [imp - exp for imp, exp in zip(grid_import, grid_export, strict=False)]

    pv_kw = _series(slots, "pv_kw")
    pv_available_kw = _series(slots, "pv_available_kw")
    pv_inverters = _series_map(slots, "pv_inverters")
    pv_inverters_available = _series_map(slots, "pv_inverters_available")
    inverter_ac_net = _series_map(slots, "inverter_ac_net_kw")
    batt_charge = _series_map(slots, "battery_charge_kw")
    batt_discharge = _series_map(slots, "battery_discharge_kw")
    batt_net: dict[str, list[float]] = {}
    for name, discharge_series in batt_discharge.items():
        charge_series = batt_charge.get(name)
        if charge_series is None:
            continue
        batt_net[name] = [
            discharge - charge
            for discharge, charge in zip(discharge_series, charge_series, strict=False)
        ]
    for name, charge_series in batt_charge.items():
        if name in batt_net:
            continue
        discharge_series = batt_discharge.get(name)
        if discharge_series is None:
            continue
        batt_net[name] = [
            discharge - charge
            for discharge, charge in zip(discharge_series, charge_series, strict=False)
        ]
    batt_soc = _series_map(slots, "battery_soc_kwh")
    load_kw = _series(slots, "load_kw")
    ev_charge = _series_map(slots, "ev_charge_kw")
    ev_soc = _series_map(slots, "ev_soc_kwh")
    batt_capacity = _extract_top_level_map(plan, "battery_capacity_kwh")
    ev_capacity = _extract_top_level_map(plan, "ev_capacity_kwh")
    batt_soc_pct = _soc_percent_series(batt_soc, batt_capacity)
    ev_soc_pct = _soc_percent_series(ev_soc, ev_capacity)
    if batt_soc_pct or ev_soc_pct:
        soc_unit = "%"
        batt_soc_plot = batt_soc_pct
        ev_soc_plot = ev_soc_pct
    else:
        soc_unit = "kWh"
        batt_soc_plot = batt_soc
        ev_soc_plot = ev_soc
    price_import = _series(slots, "price_import", "price_import_kw", "price_import_kwh")
    price_export = _series(slots, "price_export", "price_export_kw", "price_export_kwh")
    segment_cost = _series(slots, "segment_cost", "slot_cost", "cost_segment")
    cumulative_cost = _series(slots, "cumulative_cost", "total_cost")
    has_price = _has_any(price_import) or _has_any(price_export)
    has_cost = _has_any(segment_cost) or _has_any(cumulative_cost)
    has_soc = any(_has_any(series) for series in batt_soc_plot.values()) or any(
        _has_any(series) for series in ev_soc_plot.values()
    )

    if not has_cost and (_has_any(price_import) or _has_any(price_export)):
        durations = [float(slot.get("duration_h", 0.0)) for slot in slots]
        segment_cost = [
            (imp * p_imp - exp * p_exp) * dt
            for imp, exp, p_imp, p_exp, dt in zip(
                grid_import,
                grid_export,
                price_import,
                price_export,
                durations,
                strict=False,
            )
        ]
        cumulative_cost = _cumulative_sum(segment_cost)
        has_cost = _has_any(segment_cost) or _has_any(cumulative_cost)

    if has_price and has_cost and has_soc:
        fig, (ax, ax_price, ax_cost, ax_soc) = plt.subplots(
            4,
            1,
            figsize=(12, 12),
            sharex=True,
            gridspec_kw={"height_ratios": [3, 1, 1, 1]},
        )
        ax_cost_right = None
    elif has_price and has_cost:
        fig, (ax, ax_price, ax_cost) = plt.subplots(
            3,
            1,
            figsize=(12, 10),
            sharex=True,
            gridspec_kw={"height_ratios": [3, 1, 1]},
        )
        ax_soc = None
        ax_cost_right = None
    elif has_price and has_soc:
        fig, (ax, ax_price, ax_soc) = plt.subplots(
            3,
            1,
            figsize=(12, 10),
            sharex=True,
            gridspec_kw={"height_ratios": [3, 1, 1]},
        )
        ax_cost = None
        ax_cost_right = None
    elif has_cost and has_soc:
        fig, (ax, ax_cost, ax_soc) = plt.subplots(
            3,
            1,
            figsize=(12, 10),
            sharex=True,
            gridspec_kw={"height_ratios": [3, 1, 1]},
        )
        ax_price = None
        ax_cost_right = None
    elif has_price:
        fig, (ax, ax_price) = plt.subplots(
            2,
            1,
            figsize=(12, 8),
            sharex=True,
            gridspec_kw={"height_ratios": [3, 1]},
        )
        ax_cost = None
        ax_soc = None
        ax_cost_right = None
    elif has_cost:
        fig, (ax, ax_cost) = plt.subplots(
            2,
            1,
            figsize=(12, 8),
            sharex=True,
            gridspec_kw={"height_ratios": [3, 1]},
        )
        ax_price = None
        ax_soc = None
        ax_cost_right = None
    elif has_soc:
        fig, (ax, ax_soc) = plt.subplots(
            2,
            1,
            figsize=(12, 8),
            sharex=True,
            gridspec_kw={"height_ratios": [3, 1]},
        )
        ax_price = None
        ax_cost = None
        ax_cost_right = None
    else:
        fig, ax = plt.subplots(figsize=(12, 6))
        ax_price = None
        ax_cost = None
        ax_soc = None
        ax_cost_right = None

    lines: list[Any] = []
    if _has_any(grid_net):
        (line,) = ax.plot(times, grid_net, label="grid_kw")
        lines.append(line)
    if _has_any(pv_kw):
        (line,) = ax.plot(times, pv_kw, label="pv_kw")
        lines.append(line)
    if _has_any(pv_available_kw) and not _series_equal(pv_available_kw, pv_kw):
        (line,) = ax.plot(times, pv_available_kw, label="pv_available_kw", linestyle=":")
        lines.append(line)
    for name, series in pv_inverters.items():
        if _has_any(series):
            (line,) = ax.plot(times, series, label=f"pv_{name}_kw", linestyle="--")
            lines.append(line)
    for name, series in pv_inverters_available.items():
        base_series = pv_inverters.get(name)
        if base_series is not None and _series_equal(series, base_series):
            continue
        if _has_any(series):
            (line,) = ax.plot(
                times,
                series,
                label=f"pv_{name}_available_kw",
                linestyle=":",
            )
            lines.append(line)
    for name, series in batt_net.items():
        if _has_any(series):
            (line,) = ax.plot(times, series, label=f"batt_{name}_net_kw", linestyle=":")
            lines.append(line)
    for name, series in inverter_ac_net.items():
        if _has_any(series):
            (line,) = ax.plot(times, series, label=f"inv_{name}_ac_net_kw", linestyle="-.")
            lines.append(line)
    for name, series in ev_charge.items():
        (line,) = ax.plot(
            times,
            series,
            label=f"ev_{name}_charge_kw",
            linestyle="--",
        )
        lines.append(line)
    if _has_any(load_kw):
        (line,) = ax.plot(times, load_kw, label="load_kw")
        lines.append(line)

    price_lines: list[Any] = []
    if ax_price is not None:
        if _has_any(price_import):
            (line,) = ax_price.plot(times, price_import, label="price_import")
            price_lines.append(line)
        if _has_any(price_export):
            (line,) = ax_price.plot(times, price_export, label="price_export")
            price_lines.append(line)
        ax_price.set_ylabel("$/kWh")
        ax_price.legend(loc="best")
        ax_price.grid(True, alpha=0.3)
        price_max = max(
            abs(min(price_import, default=0.0)),
            abs(max(price_import, default=0.0)),
            abs(min(price_export, default=0.0)),
            abs(max(price_export, default=0.0)),
        )
        if price_max > 0:
            ax_price.set_ylim(-price_max, price_max)
        ax_price.axhline(0, color="black", linewidth=1.0, alpha=0.6, zorder=0)

    segment_lines: list[Any] = []
    cumulative_lines: list[Any] = []
    if ax_cost is not None:
        ax_cost_right = ax_cost.twinx()
        if _has_any(segment_cost):
            (line,) = ax_cost.plot(times, segment_cost, label="segment_cost", linestyle="--")
            segment_lines.append(line)
        if _has_any(cumulative_cost):
            (line,) = ax_cost_right.plot(times, cumulative_cost, label="cumulative_cost")
            cumulative_lines.append(line)
        ax_cost.set_ylabel("$/segment")
        ax_cost_right.set_ylabel("$ total")
        ax_cost.grid(True, alpha=0.3)
        segment_max = max(
            abs(min(segment_cost, default=0.0)),
            abs(max(segment_cost, default=0.0)),
        )
        if segment_max > 0:
            ax_cost.set_ylim(-segment_max, segment_max)
        cumulative_max = max(
            abs(min(cumulative_cost, default=0.0)),
            abs(max(cumulative_cost, default=0.0)),
        )
        if cumulative_max > 0:
            ax_cost_right.set_ylim(-cumulative_max, cumulative_max)
        ax_cost.axhline(0, color="black", linewidth=1.0, alpha=0.6, zorder=0)
        ax_cost_right.axhline(0, color="black", linewidth=1.0, alpha=0.6, zorder=0)
        handles_left, labels_left = ax_cost.get_legend_handles_labels()
        handles_right, labels_right = ax_cost_right.get_legend_handles_labels()
        ax_cost.legend(
            handles_left + handles_right,
            labels_left + labels_right,
            loc="best",
        )

    soc_lines: list[Any] = []
    if ax_soc is not None:
        soc_min = 0.0
        soc_max = 0.0
        soc_suffix = "pct" if soc_unit == "%" else "kwh"
        for name, series in batt_soc_plot.items():
            if _has_any(series):
                (line,) = ax_soc.plot(times, series, label=f"batt_{name}_soc_{soc_suffix}")
                soc_lines.append(line)
                soc_min = min(soc_min, min(series))
                soc_max = max(soc_max, max(series))
        for name, series in ev_soc_plot.items():
            if _has_any(series):
                (line,) = ax_soc.plot(times, series, label=f"ev_{name}_soc_{soc_suffix}")
                soc_lines.append(line)
                soc_min = min(soc_min, min(series))
                soc_max = max(soc_max, max(series))
        if soc_lines and soc_max > soc_min:
            if soc_unit == "%":
                ax_soc.set_ylim(0.0, max(100.0, soc_max * 1.05))
            else:
                ax_soc.set_ylim(0.0, soc_max * 1.05)
        ax_soc.set_ylabel(soc_unit)
        ax_soc.legend(loc="best")
        ax_soc.grid(True, alpha=0.3)

    ax.set_title(title)
    ax.set_xlabel("Time")
    ax.set_ylabel("kW")
    ax.legend(loc="best")
    ax.grid(True, alpha=0.3)
    fig.tight_layout()

    if output is not None:
        fig.savefig(output)
        return
    if lines:
        _enable_hover(ax, lines, times, unit="kW")
    if ax_price is not None and price_lines:
        _enable_hover(ax_price, price_lines, times, unit="$/kWh")
    if ax_cost is not None and segment_lines:
        _enable_hover(
            ax_cost,
            segment_lines,
            times,
            unit="$/segment",
            allowed_axes={ax_cost, ax_cost_right} if ax_cost_right is not None else None,
        )
    if ax_cost_right is not None and cumulative_lines:
        _enable_hover(
            ax_cost_right,
            cumulative_lines,
            times,
            unit="$",
            allowed_axes={ax_cost, ax_cost_right},
        )
    if ax_soc is not None and soc_lines:
        _enable_hover(ax_soc, soc_lines, times, unit=soc_unit)

    _enable_line_toggle(fig)
    plt.show()


def _extract_slots(plan: object) -> list[dict[str, Any]]:
    try:
        slots = plan.slots  # type: ignore[attr-defined]
    except AttributeError:
        slots = None
    if isinstance(slots, list):
        return [slot for slot in slots if isinstance(slot, dict)]

    if isinstance(plan, dict):
        if isinstance(plan.get("slots"), list):
            return [slot for slot in plan["slots"] if isinstance(slot, dict)]
        nested = plan.get("plan")
        if isinstance(nested, dict) and isinstance(nested.get("slots"), list):
            return [slot for slot in nested["slots"] if isinstance(slot, dict)]

    return []


def _parse_time(value: object, *, local_tz: tzinfo) -> datetime | None:
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=local_tz)
        return parsed.astimezone(local_tz)
    return None


def _series(
    slots: list[dict[str, Any]],
    *keys: str,
    default: float = 0.0,
) -> list[float]:
    values: list[float] = []
    for slot in slots:
        value: float | None = None
        for key in keys:
            if key in slot:
                try:
                    value = float(slot[key])
                except (TypeError, ValueError):
                    value = None
                break
        values.append(value if value is not None else default)
    return values


def _series_map(
    slots: list[dict[str, Any]],
    key: str,
) -> dict[str, list[float]]:
    series: dict[str, list[float]] = {}
    for slot in slots:
        value = slot.get(key)
        if not isinstance(value, dict):
            continue
        for name, raw in value.items():
            try:
                parsed = float(raw)
            except (TypeError, ValueError):
                parsed = 0.0
            series.setdefault(str(name), []).append(parsed)
    for values in series.values():
        if len(values) < len(slots):
            values.extend([0.0] * (len(slots) - len(values)))
    return series


def _extract_top_level_map(plan: object, key: str) -> dict[str, float]:
    if isinstance(plan, dict):
        value = plan.get(key)
    else:
        value = getattr(plan, key, None)
    if not isinstance(value, dict):
        return {}
    parsed: dict[str, float] = {}
    for name, raw in value.items():
        try:
            parsed[str(name)] = float(raw)
        except (TypeError, ValueError):
            parsed[str(name)] = 0.0
    return parsed


def _soc_percent_series(
    soc_series: dict[str, list[float]],
    capacities: dict[str, float],
) -> dict[str, list[float]]:
    percent: dict[str, list[float]] = {}
    for name, series in soc_series.items():
        capacity = float(capacities.get(name, 0.0))
        if capacity <= 0:
            continue
        percent[name] = [(value / capacity) * 100.0 for value in series]
    return percent


def _has_any(values: Iterable[float]) -> bool:
    return any(abs(value) > 1e-9 for value in values)


def _series_equal(a: list[float], b: list[float], *, atol: float = 1e-9) -> bool:
    if len(a) != len(b):
        return False
    return all(abs(x - y) <= atol for x, y in zip(a, b, strict=False))


def _cumulative_sum(values: list[float]) -> list[float]:
    total = 0.0
    series: list[float] = []
    for value in values:
        total += value
        series.append(total)
    return series


def _enable_hover(
    ax: Any,
    lines: list[Any],
    times: list[datetime],
    *,
    unit: str,
    allowed_axes: set[Any] | None = None,
) -> None:
    try:
        import matplotlib.dates as mdates
    except ImportError:
        return

    for line in lines:
        line.set_pickradius(5)

    time_values = [mdates.date2num(value) for value in times]
    annotation = ax.annotate(
        "",
        xy=(0, 0),
        xytext=(12, 12),
        textcoords="offset points",
        bbox={"boxstyle": "round", "fc": "white", "ec": "0.7"},
        arrowprops={"arrowstyle": "->", "color": "0.5"},
    )
    annotation.set_visible(False)

    def _format_tooltip(line: Any, idx: int) -> str:
        label = line.get_label()
        time_str = times[idx].strftime("%Y-%m-%d %H:%M")
        value = float(line.get_ydata()[idx])
        return f"{label}\n{time_str}\n{value:.3f} {unit}"

    def _on_move(event: Any) -> None:
        if event.inaxes is None or (
            allowed_axes is not None and event.inaxes not in allowed_axes
        ):
            if annotation.get_visible():
                annotation.set_visible(False)
                event.canvas.draw_idle()
            return

        best: tuple[float, Any, int, float, float] | None = None
        for line in lines:
            contains, info = line.contains(event)
            if not contains:
                continue
            for idx in info.get("ind", []):
                x = time_values[idx]
                y = float(line.get_ydata()[idx])
                display_x, display_y = ax.transData.transform((x, y))
                dist = ((display_x - event.x) ** 2 + (display_y - event.y) ** 2) ** 0.5
                if best is None or dist < best[0]:
                    best = (dist, line, idx, x, y)

        if best is None:
            if annotation.get_visible():
                annotation.set_visible(False)
                event.canvas.draw_idle()
            return

        _, line, idx, x, y = best
        annotation.xy = (x, y)
        annotation.set_text(_format_tooltip(line, idx))
        annotation.set_visible(True)
        event.canvas.draw_idle()

    ax.figure.canvas.mpl_connect("motion_notify_event", _on_move)


def _enable_line_toggle(fig: Any) -> None:
    legend_map: dict[Any, Any] = {}
    text_map: dict[Any, Any] = {}
    for ax in fig.axes:
        legend = ax.get_legend()
        if legend is None:
            continue
        handles = getattr(legend, "legendHandles", None)
        if handles is None:
            handles = legend.legend_handles
        labels = [text.get_text() for text in legend.texts]
        for handle, label in zip(handles, labels, strict=False):
            for line in ax.get_lines():
                if line.get_label() == label:
                    legend_map[handle] = line
                    break
        for text in legend.texts:
            label = text.get_text()
            for line in ax.get_lines():
                if line.get_label() == label:
                    text_map[text] = line
                    break
        for item in list(legend_map.keys()) + list(text_map.keys()):
            item.set_picker(True)

    def _on_pick(event: Any) -> None:
        artist = event.artist
        line = legend_map.get(artist) or text_map.get(artist)
        if line is None:
            return
        visible = not line.get_visible()
        line.set_visible(visible)
        if artist in legend_map:
            artist.set_alpha(1.0 if visible else 0.2)
        elif artist in text_map:
            artist.set_alpha(1.0 if visible else 0.2)
        fig.canvas.draw_idle()

    fig.canvas.mpl_connect("pick_event", _on_pick)

"""Energy plan plotting utilities."""

from __future__ import annotations

import html
import math
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, tzinfo
from pathlib import Path
from typing import Any, cast

from energy_assistant.ems.models import (
    BaseLoadComponentPlan,
    BatteryComponentPlan,
    EmsPlanOutput,
    EmsSeriesPoint,
    GridComponentPlan,
    InverterComponentPlan,
    LoadControlledEvComponentPlan,
    PvComponentPlan,
)

COLORS = {
    "pv": "rgba(255, 193, 7, 1.0)",
    "pv_fill": "rgba(255, 193, 7, 0.5)",
    "curtailment": "rgba(255, 152, 0, 1.0)",
    "curtailment_line": "rgba(255, 152, 0, 0.35)",
    "load": "rgba(156, 39, 176, 1.0)",
    "load_fill": "rgba(156, 39, 176, 0.4)",
    "grid_net": "rgba(33, 150, 243, 1.0)",
    "grid_net_fill": "rgba(33, 150, 243, 0.4)",
    "batt_charge": "rgba(0, 150, 136, 1.0)",
    "batt_charge_fill": "rgba(0, 150, 136, 0.4)",
    "batt_discharge": "rgba(0, 150, 136, 1.0)",
    "batt_discharge_fill": "rgba(0, 150, 136, 0.3)",
    "batt_soc": "rgba(76, 175, 80, 1.0)",
    "ev_charge": "rgba(0, 150, 136, 1.0)",
    "ev_charge_fill": "rgba(0, 150, 136, 0.3)",
    "ev_soc": "rgba(139, 195, 74, 1.0)",
    "price_import": "rgba(63, 81, 181, 1.0)",
    "price_export": "rgba(233, 30, 99, 1.0)",
    "price_import_risk": "rgba(63, 81, 181, 0.35)",
    "price_export_risk": "rgba(233, 30, 99, 0.35)",
    "curtailment_fill": "rgba(255, 193, 7, 0.12)",
}


def _plan_display_timezone(
    *refs: datetime | None,
) -> tzinfo:
    """Axis time zone from series instants only (not plan ``generated_at``, often UTC)."""
    tzs = {t.tzinfo for t in refs if t is not None and t.tzinfo is not None}
    if len(tzs) == 1:
        return next(iter(tzs))
    host = datetime.now().astimezone().tzinfo
    return host if host is not None else UTC


def _hourly_major_step_hours(span: timedelta) -> int:
    """Pick a tick every n hours (on the wall clock), with at most ~20 major ticks."""
    span_h = max(span.total_seconds() / 3600.0, 1e-6)
    target = max(1, int(math.ceil(span_h / 20.0)))
    for step in (1, 2, 3, 4, 6, 8, 12, 24, 48, 72, 96, 120, 168, 720, 24 * 30, 24 * 90, 24 * 365):
        if step >= target:
            return step
    return 24 * 365


def _date_tick0_floor(start: datetime, local_tz: tzinfo) -> datetime:
    """First on-the-hour instant at or before ``start`` in ``local_tz``."""
    t_aware = start if start.tzinfo is not None else start.replace(tzinfo=local_tz)
    local = t_aware.astimezone(local_tz)
    floored = local.replace(minute=0, second=0, microsecond=0)
    if floored > local:
        floored -= timedelta(hours=1)
    if start.tzinfo is not None:
        return floored.astimezone(start.tzinfo)
    return floored


def _on_the_hour_ticks(
    start: datetime, end: datetime, local_tz: tzinfo, step_hours: int
) -> list[datetime]:
    """On-the-hour instants every ``step_hours`` hours from ``start`` to ``end`` (inclusive)."""
    step = timedelta(hours=step_hours)
    t = _date_tick0_floor(start, local_tz)
    while t < start:
        t += step
    out: list[datetime] = []
    end_eps = end + timedelta(microseconds=1)
    while t <= end_eps:
        out.append(t)
        t += step
    return out


def _plan_xaxis_plotly_config(start: datetime, end: datetime, local_tz: tzinfo) -> dict[str, Any]:
    """Plotly x-axis: localized labels, vertical grid on the hour every n hours."""
    step_h = _hourly_major_step_hours(end - start)
    instants = _on_the_hour_ticks(start, end, local_tz, step_h)
    tickvals = [t.timestamp() * 1000.0 for t in instants]
    ticktext: list[str] = []
    for t in instants:
        w = t.astimezone(local_tz)
        ticktext.append(w.strftime("%I:%M %p\n%d %b"))
    return {
        "title": None,
        "type": "date",
        "showgrid": True,
        "gridcolor": "rgba(128, 128, 128, 0.2)",
        "tickmode": "array",
        "tickvals": tickvals,
        "ticktext": ticktext,
        "hoverformat": "%Y-%m-%d %H:%M",
        "domain": [0.0, 0.88],
    }


@dataclass(frozen=True, slots=True)
class ScenarioPlot:
    name: str
    plan: EmsPlanOutput | None = None
    error: str | None = None


def _build_plan_figure(
    plan: EmsPlanOutput,
    *,
    include_hover: bool = True,
) -> tuple[Any, float]:
    """Build a Plotly figure for the energy plan.

    Args:
        plan: The plan output to plot.
        include_hover: Whether to include hover templates on traces.

    Returns:
        Tuple of (figure, total_cost).
    """
    try:
        import plotly.graph_objects as go  # pyright: ignore[reportUnknownVariableType]
        from plotly.subplots import make_subplots  # pyright: ignore[reportUnknownVariableType]
    except ImportError as exc:
        raise ImportError("plotly is required for plotting: uv add plotly") from exc

    grid = _single_component(plan, "grid", GridComponentPlan)
    if grid is None:
        raise ValueError("Plan is missing required 'grid' component.")
    if not grid.net_kw:
        raise ValueError("Plan has no interval series to plot.")

    interval_points = grid.net_kw
    interval_end_times = _interval_end_times(plan, interval_points)
    local_tz = _plan_display_timezone(
        *[p.time for p in interval_points],
        interval_end_times[-1] if interval_end_times else None,
    )
    times = [_normalize_time(point.time, local_tz=local_tz) for point in interval_points]
    times.append(_normalize_time(interval_end_times[-1], local_tz=local_tz))
    time_labels = times[:-1]

    grid_net = _float_series(grid.net_kw)
    load_component = _single_component(plan, "load", BaseLoadComponentPlan, optional=True)
    load_kw = _float_series(load_component.power_kw) if load_component is not None else [0.0] * len(
        interval_points
    )

    pv_components = _components_of_type(plan, PvComponentPlan)
    battery_components = _components_of_type(plan, BatteryComponentPlan)
    ev_components = _components_of_type(plan, LoadControlledEvComponentPlan)

    pv_series = {
        name: _float_series(component.actual_kw)
        for name, component in pv_components.items()
    }
    batt_charge = {
        name: _float_series(component.charge_kw) for name, component in battery_components.items()
    }
    batt_discharge = {
        name: _float_series(component.discharge_kw)
        for name, component in battery_components.items()
    }
    batt_soc_pct = {
        name: _float_series(component.soc_pct) for name, component in battery_components.items()
    }
    batt_soc_times = {
        name: _normalize_times(component.soc_pct, local_tz=local_tz)
        for name, component in battery_components.items()
    }

    ev_charge = {
        name: _float_series(component.charge_kw)
        for name, component in ev_components.items()
    }
    ev_soc_pct = {
        name: _float_series(component.soc_pct)
        for name, component in ev_components.items()
    }
    ev_soc_times = {
        name: _normalize_times(component.soc_pct, local_tz=local_tz)
        for name, component in ev_components.items()
    }

    price_import = _float_series(grid.price_import_raw)
    price_export = _float_series(grid.price_export_raw)
    price_import_risk = _float_series(grid.price_import_effective)
    price_export_risk = _float_series(grid.price_export_effective)

    curtailment_by_pv = {
        name: _float_series(component.curtail_kw)
        for name, component in pv_components.items()
    }
    total_curtailment = _aggregate_series(curtailment_by_pv)
    curtailment_flags = _aggregate_bool_series(
        {
            name: _bool_series(component.curtailment)
            for name, component in pv_components.items()
        }
    )

    has_soc = any(_has_any(series) for series in batt_soc_pct.values()) or any(
        _has_any(series) for series in ev_soc_pct.values()
    )
    has_price = (
        _has_any(price_import)
        or _has_any(price_export)
        or _has_any(price_import_risk)
        or _has_any(price_export_risk)
    )

    fig = make_subplots(rows=1, cols=1, specs=[[{"secondary_y": True}]])

    legend_group_power = "Power"
    legend_group_soc = "State of Charge"
    legend_group_price = "Price"

    total_pv = _aggregate_series(pv_series)
    total_batt_charge = _aggregate_series(batt_charge)
    total_batt_discharge = _aggregate_series(batt_discharge)
    total_ev_charge = _aggregate_series(ev_charge)

    if _has_any(total_pv):
        fig.add_trace(
            go.Scatter(
                x=time_labels,
                y=total_pv,
                name="PV Power",
                mode="lines",
                fill="tozeroy",
                fillcolor=COLORS["pv_fill"],
                line={"color": COLORS["pv"], "width": 2, "shape": "hv"},
                hovertemplate="%{y:.2f} kW<extra>PV</extra>" if include_hover else None,
                legendgroup=legend_group_power,
            ),
            secondary_y=False,
        )

    if _has_any(total_curtailment):
        fig.add_trace(
            go.Scatter(
                x=time_labels,
                y=total_curtailment,
                name="PV Curtailment",
                mode="lines",
                fill="tozeroy",
                fillcolor=COLORS["curtailment_line"],
                line={"color": COLORS["curtailment"], "width": 2, "shape": "hv"},
                hovertemplate="%{y:.2f} kW<extra>Curtailment</extra>"
                if include_hover
                else None,
                legendgroup=legend_group_power,
            ),
            secondary_y=False,
        )

    if _has_any(load_kw):
        fig.add_trace(
            go.Scatter(
                x=time_labels,
                y=load_kw,
                name="Load",
                mode="lines",
                fill="tozeroy",
                fillcolor=COLORS["load_fill"],
                line={"color": COLORS["load"], "width": 2, "shape": "hv"},
                hovertemplate="%{y:.2f} kW<extra>Load</extra>" if include_hover else None,
                legendgroup=legend_group_power,
            ),
            secondary_y=False,
        )

    fig.add_trace(
        go.Scatter(
            x=time_labels,
            y=grid_net,
            name="Grid Net",
            mode="lines",
            fill="tozeroy",
            fillcolor=COLORS["grid_net_fill"],
            line={"color": COLORS["grid_net"], "width": 2, "shape": "hv"},
            hovertemplate="%{y:.2f} kW<extra>Grid Net</extra>" if include_hover else None,
            legendgroup=legend_group_power,
        ),
        secondary_y=False,
    )

    if _has_any(total_batt_charge):
        charge_neg = [-v for v in total_batt_charge]
        fig.add_trace(
            go.Scatter(
                x=time_labels,
                y=charge_neg,
                name="Battery Charge",
                mode="lines",
                fill="tozeroy",
                fillcolor=COLORS["batt_charge_fill"],
                line={"color": COLORS["batt_charge"], "width": 2, "shape": "hv"},
                hovertemplate="%{y:.2f} kW<extra>Batt Charge</extra>" if include_hover else None,
                legendgroup=legend_group_power,
            ),
            secondary_y=False,
        )

    if _has_any(total_batt_discharge):
        fig.add_trace(
            go.Scatter(
                x=time_labels,
                y=total_batt_discharge,
                name="Battery Discharge",
                mode="lines",
                fill="tozeroy",
                fillcolor=COLORS["batt_discharge_fill"],
                line={"color": COLORS["batt_discharge"], "width": 2, "shape": "hv"},
                hovertemplate=(
                    "%{y:.2f} kW<extra>Batt Discharge</extra>" if include_hover else None
                ),
                legendgroup=legend_group_power,
            ),
            secondary_y=False,
        )

    if _has_any(total_ev_charge):
        fig.add_trace(
            go.Scatter(
                x=time_labels,
                y=total_ev_charge,
                name="EV Charge",
                mode="lines",
                fill="tozeroy",
                fillcolor=COLORS["ev_charge_fill"],
                line={"color": COLORS["ev_charge"], "width": 2, "shape": "hv"},
                hovertemplate="%{y:.2f} kW<extra>EV Charge</extra>" if include_hover else None,
                legendgroup=legend_group_power,
            ),
            secondary_y=False,
        )

    if has_soc:
        for name, series in batt_soc_pct.items():
            if _has_any(series):
                label = f"Battery SoC ({name})" if len(batt_soc_pct) > 1 else "Battery SoC"
                fig.add_trace(
                    go.Scatter(
                        x=batt_soc_times[name],
                        y=series,
                        name=label,
                        mode="lines",
                        line={
                            "color": COLORS["batt_soc"],
                            "width": 3,
                            "shape": "hv",
                            "dash": "dot",
                        },
                        hovertemplate="%{y:.1f}%<extra>Batt SoC</extra>" if include_hover else None,
                        legendgroup=legend_group_soc,
                    ),
                    secondary_y=True,
                )
        for name, series in ev_soc_pct.items():
            if _has_any(series):
                label = f"EV SoC ({name})" if len(ev_soc_pct) > 1 else "EV SoC"
                fig.add_trace(
                    go.Scatter(
                        x=ev_soc_times[name],
                        y=series,
                        name=label,
                        mode="lines",
                        line={
                            "color": COLORS["ev_soc"],
                            "width": 3,
                            "shape": "hv",
                            "dash": "dot",
                        },
                        hovertemplate="%{y:.1f}%<extra>EV SoC</extra>" if include_hover else None,
                        legendgroup=legend_group_soc,
                    ),
                    secondary_y=True,
                )

    if has_price:
        price_y_axis = "y3"
        if _has_any(price_import):
            current_price = price_import[0] if price_import else 0
            name = f"Buy Price: {current_price:.2f} $/kWh" if include_hover else "Buy Price"
            fig.add_trace(
                go.Scatter(
                    x=time_labels,
                    y=price_import,
                    name=name,
                    mode="lines",
                    line={"color": COLORS["price_import"], "width": 2, "shape": "hv"},
                    yaxis=price_y_axis,
                    hovertemplate=(
                        "%{y:.3f} $/kWh<extra>Buy Price</extra>" if include_hover else None
                    ),
                    legendgroup=legend_group_price,
                ),
            )
        if _has_any(price_import_risk):
            current_price = price_import_risk[0] if price_import_risk else 0
            name = (
                f"Buy Price (Risk Bias): {current_price:.2f} $/kWh"
                if include_hover
                else "Buy Price (Risk Bias)"
            )
            fig.add_trace(
                go.Scatter(
                    x=time_labels,
                    y=price_import_risk,
                    name=name,
                    mode="lines",
                    line={
                        "color": COLORS["price_import_risk"],
                        "width": 1.5,
                        "shape": "hv",
                        "dash": "dot",
                    },
                    yaxis=price_y_axis,
                    hovertemplate=(
                        "%{y:.3f} $/kWh<extra>Buy Price (Risk Bias)</extra>"
                        if include_hover
                        else None
                    ),
                    legendgroup=legend_group_price,
                ),
            )
        if _has_any(price_export):
            current_price = price_export[0] if price_export else 0
            name = f"Sell Price: {current_price:.2f} $/kWh" if include_hover else "Sell Price"
            fig.add_trace(
                go.Scatter(
                    x=time_labels,
                    y=price_export,
                    name=name,
                    mode="lines",
                    line={"color": COLORS["price_export"], "width": 2, "shape": "hv"},
                    yaxis=price_y_axis,
                    hovertemplate=(
                        "%{y:.3f} $/kWh<extra>Sell Price</extra>" if include_hover else None
                    ),
                    legendgroup=legend_group_price,
                ),
            )
        if _has_any(price_export_risk):
            current_price = price_export_risk[0] if price_export_risk else 0
            name = (
                f"Sell Price (Risk Bias): {current_price:.2f} $/kWh"
                if include_hover
                else "Sell Price (Risk Bias)"
            )
            fig.add_trace(
                go.Scatter(
                    x=time_labels,
                    y=price_export_risk,
                    name=name,
                    mode="lines",
                    line={
                        "color": COLORS["price_export_risk"],
                        "width": 1.5,
                        "shape": "hv",
                        "dash": "dot",
                    },
                    yaxis=price_y_axis,
                    hovertemplate=(
                        "%{y:.3f} $/kWh<extra>Sell Price (Risk Bias)</extra>"
                        if include_hover
                        else None
                    ),
                    legendgroup=legend_group_price,
                ),
            )

    total_cost = _interval_settlement_cost(
        import_kw=grid.import_kw,
        export_kw=grid.export_kw,
        price_import=grid.price_import_raw,
        price_export=grid.price_export_raw,
        end_times=interval_end_times,
    )
    total_import_kwh = _interval_energy_kwh(grid.import_kw, interval_end_times)
    total_export_kwh = _interval_energy_kwh(grid.export_kw, interval_end_times)

    price_max = max(
        max(abs(p) for p in price_import) if price_import else 0,
        max(abs(p) for p in price_export) if price_export else 0,
        max(abs(p) for p in price_import_risk) if price_import_risk else 0,
        max(abs(p) for p in price_export_risk) if price_export_risk else 0,
        0.01,
    )
    soc_values = [
        value for series in (*batt_soc_pct.values(), *ev_soc_pct.values()) for value in series
    ]
    soc_max = max(soc_values, default=0.0)
    soc_axis_max = max(soc_max, 100.0)

    power_max = max(
        max(abs(v) for v in grid_net) if grid_net else 0,
        max(abs(v) for v in load_kw) if load_kw else 0,
        max(abs(v) for v in total_pv) if total_pv else 0,
        max(abs(v) for v in total_curtailment) if total_curtailment else 0,
        max(abs(v) for v in total_batt_charge) if total_batt_charge else 0,
        max(abs(v) for v in total_batt_discharge) if total_batt_discharge else 0,
        max(abs(v) for v in total_ev_charge) if total_ev_charge else 0,
        1.0,
    )
    power_max = max(power_max * 1.1, 1.0)

    curtailment_shapes = [
        {
            "type": "rect",
            "xref": "x",
            "yref": "paper",
            "x0": times[index],
            "x1": times[index + 1],
            "y0": 0,
            "y1": 1,
            "fillcolor": COLORS["curtailment_fill"],
            "line": {"width": 0},
            "layer": "below",
        }
        for index, active in enumerate(curtailment_flags)
        if active
    ]
    soc_reference_line = None
    if has_soc:
        soc_reference_line = {
            "type": "line",
            "xref": "x",
            "yref": "y2",
            "x0": times[0],
            "x1": times[-1],
            "y0": 100,
            "y1": 100,
            "line": {"color": "rgba(76, 175, 80, 0.6)", "width": 1, "dash": "dot"},
            "layer": "below",
        }

    fig.update_layout(
        title={
            "text": (
                "EMS Plan | "
                f"Cost 💰: ${total_cost:.2f} | "
                f"Grid Export 📤: {total_export_kwh:.2f} kWh | "
                f"Grid Import 📥: {total_import_kwh:.2f} kWh"
            ),
            "x": 0.5,
            "xanchor": "center",
            "font": {"size": 16},
        },
        xaxis=_plan_xaxis_plotly_config(times[0], times[-1], local_tz),
        yaxis={
            "title": "Power (kW)",
            "showgrid": True,
            "gridcolor": "rgba(128, 128, 128, 0.2)",
            "zeroline": True,
            "zerolinecolor": "rgba(128, 128, 128, 0.5)",
            "range": [-power_max, power_max],
        },
        yaxis2={
            "title": {"text": "SoC (%)", "standoff": 10},
            "overlaying": "y",
            "side": "right",
            "anchor": "free",
            "position": 0.98,
            "showgrid": False,
            "range": [-soc_axis_max, soc_axis_max],
            "tickmode": "array",
            "tickvals": [0, 20, 40, 60, 80, 100],
            "ticksuffix": "%",
            "zeroline": True,
            "zerolinecolor": "rgba(128, 128, 128, 0.5)",
            "ticklabelposition": "outside right",
            "ticklabelstandoff": 4,
            "ticks": "outside",
        },
        yaxis3={
            "title": {"text": "Price ($)", "standoff": 12},
            "overlaying": "y",
            "side": "right",
            "position": 0.92,
            "anchor": "free",
            "showgrid": False,
            "range": [-price_max * 1.1, price_max * 1.1],
            "tickformat": ".2f",
            "ticklabelposition": "outside right",
            "ticklabelstandoff": 4,
            "ticks": "outside",
            "tickfont": {"size": 10},
        },
        legend={
            "orientation": "h",
            "yanchor": "top",
            "y": -0.12,
            "xanchor": "center",
            "x": 0.5,
            "bgcolor": "rgba(255, 255, 255, 0.8)",
            "itemclick": "toggle",
            "itemdoubleclick": "toggleothers",
        },
        hovermode="x unified",
        plot_bgcolor="white",
        paper_bgcolor="white",
        margin={"l": 60, "r": 130, "t": 50, "b": 100},
        shapes=curtailment_shapes + ([soc_reference_line] if soc_reference_line else []),
    )

    return fig, total_cost


def _apply_interactive_overrides(fig: Any) -> None:
    fig.update_traces(hoverlabel={"namelength": -1})

    fig.update_xaxes(
        rangeslider={"visible": False},
        rangeselector={
            "buttons": [
                {"count": 6, "label": "6h", "step": "hour", "stepmode": "backward"},
                {"count": 12, "label": "12h", "step": "hour", "stepmode": "backward"},
                {"count": 1, "label": "1d", "step": "day", "stepmode": "backward"},
                {"step": "all", "label": "All"},
            ],
            "yanchor": "bottom",
            "y": 1.02,
            "xanchor": "right",
            "x": 1,
        },
    )


def _legend_hover_script() -> str:
    return """
(function() {
    function ensureStyle() {
        if (document.getElementById('legend-hover-style')) {
            return;
        }
        var style = document.createElement('style');
        style.id = 'legend-hover-style';
        style.textContent = '.trace.faded { opacity: 0.15 !important; }';
        document.head.appendChild(style);
    }

    function attachLegendHover(gd) {
        if (!gd || gd.__legendHoverAttached) {
            return true;
        }
        if (!gd._fullData) {
            return false;
        }
        var legend = gd.querySelector('.legend');
        if (!legend) {
            return false;
        }
        gd.__legendHoverAttached = true;

        var legendGroups = legend.querySelectorAll('.traces');
        legendGroups.forEach(function(group) {
            group.addEventListener('mouseenter', function() {
                var textEl = group.querySelector('.legendtext');
                if (!textEl) return;
                var name = textEl.getAttribute('data-unformatted') || textEl.textContent;
                var targetUid = null;
                for (var i = 0; i < gd._fullData.length; i++) {
                    if (gd._fullData[i].name === name) {
                        targetUid = gd._fullData[i].uid;
                        break;
                    }
                }
                if (!targetUid) return;
                gd.querySelectorAll('.scatterlayer .trace, .overplot .trace').forEach(function(t) {
                    var tClass = t.className.baseVal || t.className || '';
                    if (tClass.indexOf(targetUid) === -1) {
                        t.classList.add('faded');
                    }
                });
            });
            group.addEventListener('mouseleave', function() {
                gd.querySelectorAll('.trace.faded').forEach(function(t) {
                    t.classList.remove('faded');
                });
            });
        });
        return true;
    }

    function init() {
        ensureStyle();
        var graphs = document.querySelectorAll('.plotly-graph-div');
        if (!graphs.length) {
            return true;
        }
        var allReady = true;
        graphs.forEach(function(gd) {
            if (!attachLegendHover(gd)) {
                allReady = false;
            }
        });
        return allReady;
    }

    (function retry() {
        var ready = init();
        if (!ready) {
            setTimeout(retry, 100);
        }
    })();
})();
"""


def plot_plan_html(
    plan: EmsPlanOutput,
    *,
    output: Path | None = None,
) -> str | None:
    """Generate an interactive HTML plot of the energy plan.

    Args:
        plan: The plan output to plot.
        output: If provided, write HTML to this path. Otherwise return HTML string.

    Returns:
        HTML string if output is None, otherwise None (writes to file).
    """
    fig, _ = _build_plan_figure(plan, include_hover=True)
    _apply_interactive_overrides(fig)

    html_content: str = fig.to_html(
        full_html=True, include_plotlyjs=True, post_script=_legend_hover_script()
    )

    fullscreen_css = """<style>
html, body { margin: 0; padding: 0; width: 100%; height: 100%; overflow: hidden; }
.plotly-graph-div { width: 100% !important; height: 100vh !important; }
</style>
</head>"""
    html_content = html_content.replace("</head>", fullscreen_css)

    if output is not None:
        output.write_text(html_content)
        return None
    return html_content


def plot_scenarios_html(
    scenarios: Sequence[ScenarioPlot],
    *,
    output: Path | None = None,
    title: str = "EMS Scenario Report",
    subtitle: str | None = None,
    height: int = 700,
) -> str | None:
    if not scenarios:
        raise ValueError("No scenarios provided.")

    try:
        from plotly.offline import get_plotlyjs  # pyright: ignore[reportUnknownVariableType]
    except ImportError as exc:
        raise ImportError("plotly is required for plotting: uv add plotly") from exc
    plotly_js = get_plotlyjs()

    sections: list[str] = []
    for scenario in scenarios:
        name = html.escape(scenario.name)
        if scenario.plan is None:
            error_text = html.escape(scenario.error or "Unknown error.")
            sections.append(
                "\n".join(
                    [
                        '<section class="scenario scenario-error">',
                        f"<h2>{name}</h2>",
                        "<pre>",
                        error_text,
                        "</pre>",
                        "</section>",
                    ]
                )
            )
            continue

        fig, _ = _build_plan_figure(scenario.plan, include_hover=True)
        _apply_interactive_overrides(fig)
        fig.update_layout(height=height)
        fig_html = fig.to_html(full_html=False, include_plotlyjs=False)
        sections.append(
            "\n".join(
                [
                    '<section class="scenario">',
                    f"<h2>{name}</h2>",
                    fig_html,
                    "</section>",
                ]
            )
        )

    subtitle_html = f"<p>{html.escape(subtitle)}</p>" if subtitle else ""
    html_content = "\n".join(
        [
            "<!doctype html>",
            '<html lang="en">',
            "<head>",
            '<meta charset="utf-8"/>',
            '<meta name="viewport" content="width=device-width, initial-scale=1"/>',
            f"<title>{html.escape(title)}</title>",
            "<style>",
            ":root {",
            "  color-scheme: light;",
            "  --bg: #f4f2ff;",
            "  --bg-alt: #fdf7f0;",
            "  --card: #ffffff;",
            "  --ink: #1f2933;",
            "  --muted: #52606d;",
            "  --accent: #2563eb;",
            "  --error: #ef4444;",
            "}",
            "* { box-sizing: border-box; }",
            "body {",
            "  margin: 0;",
            "  font-family: \"Sora\", \"Avenir Next\", \"Trebuchet MS\", sans-serif;",
            "  color: var(--ink);",
            "  background: radial-gradient(circle at top, var(--bg), var(--bg-alt));",
            "}",
            "header {",
            "  padding: 32px 40px 12px;",
            "}",
            "header h1 {",
            "  margin: 0 0 6px;",
            "  font-size: 28px;",
            "  letter-spacing: 0.02em;",
            "}",
            "header p {",
            "  margin: 0;",
            "  color: var(--muted);",
            "  font-size: 14px;",
            "}",
            "main {",
            "  padding: 0 40px 48px;",
            "  display: flex;",
            "  flex-direction: column;",
            "  gap: 28px;",
            "}",
            ".scenario {",
            "  background: var(--card);",
            "  border-radius: 18px;",
            "  padding: 16px 18px 8px;",
            "  box-shadow: 0 12px 28px rgba(15, 23, 42, 0.12);",
            "}",
            ".scenario h2 {",
            "  margin: 0 0 10px;",
            "  font-size: 18px;",
            "  letter-spacing: 0.02em;",
            "  text-transform: uppercase;",
            "  color: var(--accent);",
            "}",
            ".scenario-error {",
            "  border-left: 6px solid var(--error);",
            "}",
            ".scenario-error pre {",
            "  margin: 0;",
            "  padding: 12px;",
            "  border-radius: 12px;",
            "  background: #0f172a;",
            "  color: #f8fafc;",
            "  font-size: 12px;",
            "  overflow-x: auto;",
            "  white-space: pre-wrap;",
            "}",
            "@media (max-width: 860px) {",
            "  header { padding: 24px 20px 8px; }",
            "  main { padding: 0 20px 32px; }",
            "  .scenario { padding: 12px; }",
            "}",
            "</style>",
            "<script type=\"text/javascript\">",
            plotly_js,
            "</script>",
            "</head>",
            "<body>",
            "<header>",
            f"<h1>{html.escape(title)}</h1>",
            subtitle_html,
            "</header>",
            "<main>",
            *sections,
            "</main>",
            "<script type=\"text/javascript\">",
            _legend_hover_script(),
            "</script>",
            "</body>",
            "</html>",
        ]
    )

    if output is not None:
        output.write_text(html_content)
        return None
    return html_content


def write_plan_svg(
    plan: EmsPlanOutput,
    output: Path,
    *,
    width: int = 1600,
    height: int = 900,
) -> None:
    """Write the plan as a static SVG for fixture review.

    Args:
        plan: The plan output to plot.
        output: Path to write the SVG image.
        width: Image width in pixels.
        height: Image height in pixels.
    """
    try:
        import matplotlib.dates as mdates
        import matplotlib.pyplot as plt
        from matplotlib.axes import Axes
        from matplotlib.ticker import FixedLocator, FuncFormatter
    except ImportError as exc:
        raise ImportError(
            "matplotlib is required for static SVG plotting: uv add matplotlib"
        ) from exc

    grid = _single_component(plan, "grid", GridComponentPlan)
    if grid is None:
        raise ValueError("Plan is missing required 'grid' component.")
    if not grid.net_kw:
        raise ValueError("Plan has no interval series to plot.")

    interval_points = grid.net_kw
    interval_end_times = _interval_end_times(plan, interval_points)
    local_tz = _plan_display_timezone(
        *[p.time for p in interval_points],
        interval_end_times[-1] if interval_end_times else None,
    )
    times = [_normalize_time(point.time, local_tz=local_tz) for point in interval_points]
    times.append(_normalize_time(interval_end_times[-1], local_tz=local_tz))

    load_component = _single_component(plan, "load", BaseLoadComponentPlan, optional=True)
    load_kw = _float_series(load_component.power_kw) if load_component is not None else [0.0] * len(
        interval_points
    )
    grid_net = _float_series(grid.net_kw)
    pv_components = _components_of_type(plan, PvComponentPlan)
    battery_components = _components_of_type(plan, BatteryComponentPlan)
    ev_components = _components_of_type(plan, LoadControlledEvComponentPlan)

    total_pv = _aggregate_series(
        {name: _float_series(component.actual_kw) for name, component in pv_components.items()}
    )
    total_curtailment = _aggregate_series(
        {name: _float_series(component.curtail_kw) for name, component in pv_components.items()}
    )
    curtailment_flags = _aggregate_bool_series(
        {
            name: _bool_series(component.curtailment)
            for name, component in pv_components.items()
        }
    )
    total_batt_charge = _aggregate_series(
        {name: _float_series(component.charge_kw) for name, component in battery_components.items()}
    )
    total_batt_discharge = _aggregate_series(
        {
            name: _float_series(component.discharge_kw)
            for name, component in battery_components.items()
        }
    )
    batt_soc_pct = {
        name: _float_series(component.soc_pct) for name, component in battery_components.items()
    }
    batt_soc_times = {
        name: _normalize_times(component.soc_pct, local_tz=local_tz)
        for name, component in battery_components.items()
    }
    total_ev_charge = _aggregate_series(
        {name: _float_series(component.charge_kw) for name, component in ev_components.items()}
    )
    ev_soc_pct = {
        name: _float_series(component.soc_pct) for name, component in ev_components.items()
    }
    ev_soc_times = {
        name: _normalize_times(component.soc_pct, local_tz=local_tz)
        for name, component in ev_components.items()
    }
    price_import = _float_series(grid.price_import_raw)
    price_export = _float_series(grid.price_export_raw)
    price_import_risk = _float_series(grid.price_import_effective)
    price_export_risk = _float_series(grid.price_export_effective)

    has_soc = any(_has_any(series) for series in batt_soc_pct.values()) or any(
        _has_any(series) for series in ev_soc_pct.values()
    )
    has_price = (
        _has_any(price_import)
        or _has_any(price_export)
        or _has_any(price_import_risk)
        or _has_any(price_export_risk)
    )
    total_cost = _interval_settlement_cost(
        import_kw=grid.import_kw,
        export_kw=grid.export_kw,
        price_import=grid.price_import_raw,
        price_export=grid.price_export_raw,
        end_times=interval_end_times,
    )
    total_import_kwh = _interval_energy_kwh(grid.import_kw, interval_end_times)
    total_export_kwh = _interval_energy_kwh(grid.export_kw, interval_end_times)

    power_max = max(
        max(abs(v) for v in grid_net) if grid_net else 0,
        max(abs(v) for v in load_kw) if load_kw else 0,
        max(abs(v) for v in total_pv) if total_pv else 0,
        max(abs(v) for v in total_curtailment) if total_curtailment else 0,
        max(abs(v) for v in total_batt_charge) if total_batt_charge else 0,
        max(abs(v) for v in total_batt_discharge) if total_batt_discharge else 0,
        max(abs(v) for v in total_ev_charge) if total_ev_charge else 0,
        1.0,
    )
    power_max = max(power_max * 1.1, 1.0)
    soc_values = [
        value for series in (*batt_soc_pct.values(), *ev_soc_pct.values()) for value in series
    ]
    soc_axis_max = max(max(soc_values, default=0.0), 100.0)
    price_max = max(
        max(abs(p) for p in price_import) if price_import else 0,
        max(abs(p) for p in price_export) if price_export else 0,
        max(abs(p) for p in price_import_risk) if price_import_risk else 0,
        max(abs(p) for p in price_export_risk) if price_export_risk else 0,
        0.01,
    )

    def color(name: str) -> tuple[float, float, float, float]:
        return _rgba(COLORS[name])

    with plt.rc_context(
        {
            "svg.fonttype": "none",
            "svg.hashsalt": "energy-assistant-plan",
            "font.family": "DejaVu Sans",
        }
    ):
        time_numbers = _date_numbers(times, mdates.date2num)
        fig, ax_power = plt.subplots(figsize=(width / 100, height / 100), dpi=100)
        fig.patch.set_facecolor("white")
        ax_power.set_facecolor("white")

        for index, active in enumerate(curtailment_flags):
            if active:
                ax_power.axvspan(
                    time_numbers[index],
                    time_numbers[index + 1],
                    facecolor=color("curtailment_fill"),
                    edgecolor="none",
                    zorder=0,
                )

        _plot_step_area(
            ax_power,
            time_numbers,
            total_pv,
            label="PV Power",
            line_color=color("pv"),
            fill_color=color("pv_fill"),
        )
        _plot_step_area(
            ax_power,
            time_numbers,
            total_curtailment,
            label="PV Curtailment",
            line_color=color("curtailment"),
            fill_color=color("curtailment_line"),
        )
        _plot_step_area(
            ax_power,
            time_numbers,
            load_kw,
            label="Load",
            line_color=color("load"),
            fill_color=color("load_fill"),
        )
        _plot_step_area(
            ax_power,
            time_numbers,
            grid_net,
            label="Grid Net",
            line_color=color("grid_net"),
            fill_color=color("grid_net_fill"),
            always=True,
        )
        _plot_step_area(
            ax_power,
            time_numbers,
            [-v for v in total_batt_charge],
            label="Battery Charge",
            line_color=color("batt_charge"),
            fill_color=color("batt_charge_fill"),
        )
        _plot_step_area(
            ax_power,
            time_numbers,
            total_batt_discharge,
            label="Battery Discharge",
            line_color=color("batt_discharge"),
            fill_color=color("batt_discharge_fill"),
        )
        _plot_step_area(
            ax_power,
            time_numbers,
            total_ev_charge,
            label="EV Charge",
            line_color=color("ev_charge"),
            fill_color=color("ev_charge_fill"),
        )

        axes: list[Axes] = [ax_power]
        if has_soc:
            ax_soc = ax_power.twinx()
            axes.append(ax_soc)
            ax_soc.set_ylim(-soc_axis_max, soc_axis_max)
            ax_soc.set_ylabel("SoC (%)")
            ax_soc.set_yticks([0, 20, 40, 60, 80, 100])
            ax_soc.axhline(100, color=color("batt_soc"), linewidth=1, linestyle=":", alpha=0.6)
            for name, series in batt_soc_pct.items():
                if _has_any(series):
                    label = f"Battery SoC ({name})" if len(batt_soc_pct) > 1 else "Battery SoC"
                    ax_soc.step(
                        _date_numbers(batt_soc_times[name], mdates.date2num),
                        series,
                        where="post",
                        label=label,
                        color=color("batt_soc"),
                        linewidth=2.2,
                        linestyle=":",
                    )
            for name, series in ev_soc_pct.items():
                if _has_any(series):
                    label = f"EV SoC ({name})" if len(ev_soc_pct) > 1 else "EV SoC"
                    ax_soc.step(
                        _date_numbers(ev_soc_times[name], mdates.date2num),
                        series,
                        where="post",
                        label=label,
                        color=color("ev_soc"),
                        linewidth=2.2,
                        linestyle=":",
                    )

        if has_price:
            ax_price = ax_power.twinx()
            axes.append(ax_price)
            ax_price.spines["right"].set_position(("axes", 1.1 if has_soc else 1.0))
            ax_price.set_ylim(-price_max * 1.1, price_max * 1.1)
            ax_price.set_ylabel("Price ($)")
            _plot_step_line(
                ax_price,
                time_numbers,
                price_import,
                label="Buy Price",
                color=color("price_import"),
            )
            _plot_step_line(
                ax_price,
                time_numbers,
                price_import_risk,
                label="Buy Price (Risk Bias)",
                color=color("price_import_risk"),
                linestyle=":",
                linewidth=1.5,
            )
            _plot_step_line(
                ax_price,
                time_numbers,
                price_export,
                label="Sell Price",
                color=color("price_export"),
            )
            _plot_step_line(
                ax_price,
                time_numbers,
                price_export_risk,
                label="Sell Price (Risk Bias)",
                color=color("price_export_risk"),
                linestyle=":",
                linewidth=1.5,
            )

        ax_power.set_xlim(time_numbers[0], time_numbers[-1])
        ax_power.set_ylim(-power_max, power_max)
        ax_power.set_ylabel("Power (kW)")
        ax_power.grid(True, color=(0.5, 0.5, 0.5, 0.2), linewidth=0.8)
        ax_power.axhline(0, color=(0.5, 0.5, 0.5, 0.5), linewidth=0.8)
        step_h = _hourly_major_step_hours(times[-1] - times[0])
        x_tick_instants = _on_the_hour_ticks(times[0], times[-1], local_tz, step_h)
        x_tick_numbers: list[float] = [cast(float, mdates.date2num(t)) for t in x_tick_instants]

        def _format_xaxis_tick(n: float, _pos: int | None) -> str:
            return mdates.num2date(n, tz=local_tz).strftime("%I:%M %p\n%d %b")

        ax_power.xaxis.set_major_locator(FixedLocator(x_tick_numbers))
        ax_power.xaxis.set_major_formatter(FuncFormatter(_format_xaxis_tick))

        fig.suptitle(
            "EMS Plan | "
            f"Cost: ${total_cost:.2f} | "
            f"Grid Export: {total_export_kwh:.2f} kWh | "
            f"Grid Import: {total_import_kwh:.2f} kWh",
            fontsize=16,
        )

        handles: list[Any] = []
        labels: list[str] = []
        for axis in axes:
            axis_handles, axis_labels = axis.get_legend_handles_labels()
            handles.extend(axis_handles)
            labels.extend(axis_labels)
        if handles:
            fig.legend(
                handles,
                labels,
                loc="lower center",
                ncol=min(4, len(handles)),
                frameon=True,
                bbox_to_anchor=(0.5, 0.02),
            )

        fig.subplots_adjust(left=0.06, right=0.84 if has_price else 0.9, top=0.92, bottom=0.16)
        output.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(output, format="svg", metadata={"Date": None})
        plt.close(fig)

    if not output.exists():
        raise ValueError(f"Failed to write plan SVG to {output}")
    if output.stat().st_size == 0:
        raise ValueError(f"Plan SVG {output} is empty")


def _plot_step_area(
    ax: Any,
    times: Sequence[float],
    values: list[float],
    *,
    label: str,
    line_color: tuple[float, float, float, float],
    fill_color: tuple[float, float, float, float],
    always: bool = False,
) -> None:
    if not always and not _has_any(values):
        return
    step_values = _extend_step_values(times, values)
    ax.step(times, step_values, where="post", label=label, color=line_color, linewidth=2)
    ax.fill_between(times, step_values, 0, step="post", color=fill_color, linewidth=0)


def _plot_step_line(
    ax: Any,
    times: Sequence[float],
    values: list[float],
    *,
    label: str,
    color: tuple[float, float, float, float],
    linestyle: str = "-",
    linewidth: float = 2,
) -> None:
    if not _has_any(values):
        return
    ax.step(
        times,
        _extend_step_values(times, values),
        where="post",
        label=label,
        color=color,
        linewidth=linewidth,
        linestyle=linestyle,
    )


def _extend_step_values(times: Sequence[float], values: list[float]) -> list[float]:
    if len(times) != len(values) + 1:
        raise ValueError("Step plot requires interval edge times and one value per interval.")
    return [*values, values[-1]]


def _date_numbers(times: Sequence[datetime], converter: Any) -> list[float]:
    return [float(converter(time)) for time in times]


def _rgba(value: str) -> tuple[float, float, float, float]:
    match = re.fullmatch(
        r"rgba\(\s*(\d+),\s*(\d+),\s*(\d+),\s*([0-9.]+)\s*\)",
        value,
    )
    if match is None:
        raise ValueError(f"Unsupported color format: {value}")
    red, green, blue, alpha = match.groups()
    return (int(red) / 255.0, int(green) / 255.0, int(blue) / 255.0, float(alpha))


def _normalize_time(value: datetime, *, local_tz: tzinfo) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=local_tz)
    return value.astimezone(local_tz)


def _single_component[T](
    plan: EmsPlanOutput,
    component_type: str,
    model: type[T],
    *,
    optional: bool = False,
) -> T | None:
    matches = [
        component for component in plan.components.values() if component.type == component_type
    ]
    if not matches:
        if optional:
            return None
        raise ValueError(f"Plan is missing required {component_type!r} component.")
    if len(matches) != 1:
        raise ValueError(f"Expected exactly one {component_type!r} component, got {len(matches)}.")
    component = matches[0]
    if not isinstance(component, model):
        raise TypeError(f"Plan component {component_type!r} had unexpected model type.")
    return component


def _components_of_type[T](plan: EmsPlanOutput, model: type[T]) -> dict[str, T]:
    return {
        component_id: component
        for component_id, component in sorted(plan.components.items())
        if isinstance(component, model)
    }


def _float_series(points: list[EmsSeriesPoint]) -> list[float]:
    return [float(point.value) for point in points]


def _bool_series(points: list[EmsSeriesPoint]) -> list[bool]:
    return [bool(point.value) for point in points]


def _normalize_times(points: list[EmsSeriesPoint], *, local_tz: tzinfo) -> list[datetime]:
    return [_normalize_time(point.time, local_tz=local_tz) for point in points]


def _interval_end_times(
    plan: EmsPlanOutput,
    interval_points: list[EmsSeriesPoint],
) -> list[datetime]:
    if not interval_points:
        return []
    starts = [point.time for point in interval_points]
    if len(starts) == 1:
        inferred_end = _plan_end_time(plan) or (starts[0] + timedelta(minutes=5))
        return [inferred_end]
    end_times = starts[1:]
    final_end = _plan_end_time(plan)
    if final_end is None or final_end <= starts[-1]:
        final_end = starts[-1] + (starts[-1] - starts[-2])
    end_times.append(final_end)
    return end_times


def _plan_end_time(plan: EmsPlanOutput) -> datetime | None:
    state_times = [
        points[-1].time
        for component in plan.components.values()
        if isinstance(component, BatteryComponentPlan | LoadControlledEvComponentPlan)
        for points in ([component.soc_pct] if component.soc_pct else [])
    ]
    if state_times:
        return max(state_times)
    interval_times = [
        points[-1].time
        for component in plan.components.values()
        for points in _component_interval_series(component)
        if points
    ]
    if interval_times:
        return max(interval_times)
    return None


def _component_interval_series(component: object) -> list[list[EmsSeriesPoint]]:
    if isinstance(component, GridComponentPlan):
        return [
            component.price_import_raw,
            component.price_export_raw,
            component.price_import_effective,
            component.price_export_effective,
            component.import_allowed,
            component.import_kw,
            component.export_kw,
            component.net_kw,
        ]
    if isinstance(component, BaseLoadComponentPlan):
        return [component.power_kw]
    if isinstance(component, InverterComponentPlan):
        return [component.ac_net_kw]
    if isinstance(component, PvComponentPlan):
        return [
            component.available_kw,
            component.actual_kw,
            component.curtail_kw,
            component.curtailment,
        ]
    if isinstance(component, BatteryComponentPlan):
        return [component.charge_kw, component.discharge_kw]
    if isinstance(component, LoadControlledEvComponentPlan):
        return [component.charge_kw, component.connected, component.charge_allowed]
    return []


def _interval_energy_kwh(points: list[EmsSeriesPoint], end_times: list[datetime]) -> float:
    if len(points) != len(end_times):
        raise ValueError("Interval energy inputs must have matching lengths.")
    total = 0.0
    for point, end_time in zip(points, end_times, strict=True):
        duration_h = (end_time - point.time).total_seconds() / 3600.0
        total += float(point.value) * duration_h
    return total


def _interval_settlement_cost(
    *,
    import_kw: list[EmsSeriesPoint],
    export_kw: list[EmsSeriesPoint],
    price_import: list[EmsSeriesPoint],
    price_export: list[EmsSeriesPoint],
    end_times: list[datetime],
) -> float:
    lengths = {
        len(import_kw),
        len(export_kw),
        len(price_import),
        len(price_export),
        len(end_times),
    }
    if len(lengths) != 1:
        raise ValueError("Interval settlement inputs must have matching lengths.")
    total = 0.0
    for import_point, export_point, import_price, export_price, end_time in zip(
        import_kw, export_kw, price_import, price_export, end_times, strict=True
    ):
        duration_h = (end_time - import_point.time).total_seconds() / 3600.0
        total += (
            float(import_point.value) * float(import_price.value)
            - float(export_point.value) * float(export_price.value)
        ) * duration_h
    return total


def _aggregate_series(series_dict: dict[str, list[float]]) -> list[float]:
    """Aggregate multiple series into a single total series."""
    if not series_dict:
        return []
    length = len(next(iter(series_dict.values())))
    total = [0.0] * length
    for series in series_dict.values():
        for i, v in enumerate(series):
            total[i] += v
    return total


def _aggregate_bool_series(series_dict: dict[str, list[bool]]) -> list[bool]:
    if not series_dict:
        return []
    length = len(next(iter(series_dict.values())))
    total = [False] * length
    for series in series_dict.values():
        for i, value in enumerate(series):
            total[i] = total[i] or value
    return total


def _has_any(values: list[float]) -> bool:
    return any(abs(value) > 1e-9 for value in values)

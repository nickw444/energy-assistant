"""Interactive HTML plotting using Plotly."""

from __future__ import annotations

import html
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, tzinfo
from pathlib import Path
from typing import Any

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

@dataclass(frozen=True, slots=True)
class ScenarioPlot:
    name: str
    plan: EmsPlanOutput | None = None
    error: str | None = None


@dataclass(frozen=True, slots=True)
class _StaticTrace:
    name: str
    axis: str
    times: list[datetime]
    values: list[float]
    stroke_color: str
    stroke_width: float = 2.0
    fill_color: str | None = None
    dash: str | None = None


@dataclass(frozen=True, slots=True)
class _PreparedStaticPlot:
    traces: list[_StaticTrace]
    interval_boundaries: list[datetime]
    curtailment_flags: list[bool]
    power_max: float
    soc_axis_max: float
    price_max: float
    total_cost: float
    total_import_kwh: float
    total_export_kwh: float


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

    local_tz = datetime.now().astimezone().tzinfo or UTC
    grid = _single_component(plan, "grid", GridComponentPlan)
    if grid is None:
        raise ValueError("Plan is missing required 'grid' component.")
    if not grid.net_kw:
        raise ValueError("Plan has no interval series to plot.")

    interval_points = grid.net_kw
    interval_end_times = _interval_end_times(plan, interval_points)
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
        xaxis={
            "title": None,
            "showgrid": True,
            "gridcolor": "rgba(128, 128, 128, 0.2)",
            "tickformat": "%I:%M %p\n%d %b",
            "hoverformat": "%Y-%m-%d %H:%M",
            "domain": [0.0, 0.88],
        },
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
    """Write the plan as a static SVG plot for fixture baselines and reviews.

    Args:
        plan: The plan output to plot.
        output: Path to write the SVG image.
        width: SVG viewport width in pixels.
        height: SVG viewport height in pixels.
    """
    svg = _build_plan_svg(plan, width=width, height=height)
    output.write_text(svg)
    if not output.exists():
        raise ValueError(f"Failed to write plan SVG to {output}")
    if output.stat().st_size == 0:
        raise ValueError(f"Plan SVG {output} is empty")


def write_plan_image(
    plan: EmsPlanOutput,
    output: Path,
    *,
    width: int = 1600,
    height: int = 900,
) -> None:
    """Backward-compatible wrapper for writing the static plan plot."""
    write_plan_svg(plan, output, width=width, height=height)


def _build_plan_svg(
    plan: EmsPlanOutput,
    *,
    width: int,
    height: int,
) -> str:
    prepared = _prepare_static_plot(plan)
    chart_left = 80
    chart_top = 110
    chart_right = width - 180
    chart_bottom = height - 145
    chart_width = chart_right - chart_left
    chart_height = chart_bottom - chart_top
    soc_axis_x = width - 88
    price_axis_x = width - 138

    start_time = prepared.interval_boundaries[0]
    end_time = prepared.interval_boundaries[-1]
    if end_time <= start_time:
        end_time = start_time + timedelta(minutes=5)

    def x_for(value: datetime) -> float:
        span = (end_time - start_time).total_seconds()
        offset = (value - start_time).total_seconds()
        return chart_left + (offset / span) * chart_width

    def y_for(axis: str, value: float) -> float:
        if axis == "soc":
            y_min, y_max = -prepared.soc_axis_max, prepared.soc_axis_max
        elif axis == "price":
            y_min, y_max = -prepared.price_max * 1.1, prepared.price_max * 1.1
        else:
            y_min, y_max = -prepared.power_max, prepared.power_max
        span = y_max - y_min or 1.0
        return chart_top + ((y_max - value) / span) * chart_height

    def step_points(times: list[datetime], values: list[float]) -> list[tuple[float, float]]:
        if not times or not values:
            return []
        points: list[tuple[float, float]] = []
        if len(times) == len(values) + 1:
            points.append((x_for(times[0]), values[0]))
            for index in range(1, len(values)):
                x_value = x_for(times[index])
                points.append((x_value, values[index - 1]))
                points.append((x_value, values[index]))
            points.append((x_for(times[-1]), values[-1]))
            return points

        points.append((x_for(times[0]), values[0]))
        for index in range(1, min(len(times), len(values))):
            x_value = x_for(times[index])
            points.append((x_value, values[index - 1]))
            points.append((x_value, values[index]))
        return points

    def path_from_points(points: list[tuple[float, float]], axis: str) -> str:
        svg_points = [
            f"{'M' if index == 0 else 'L'} {x_value:.2f} {y_for(axis, y_value):.2f}"
            for index, (x_value, y_value) in enumerate(points)
        ]
        return " ".join(svg_points)

    def area_path_from_points(points: list[tuple[float, float]], axis: str) -> str:
        if not points:
            return ""
        baseline_y = y_for(axis, 0.0)
        start_x = points[0][0]
        end_x = points[-1][0]
        line_segments = [
            f"L {x_value:.2f} {y_for(axis, y_value):.2f}" for x_value, y_value in points[1:]
        ]
        return (
            f"M {start_x:.2f} {baseline_y:.2f} "
            f"L {start_x:.2f} {y_for(axis, points[0][1]):.2f} "
            f"{' '.join(line_segments)} "
            f"L {end_x:.2f} {baseline_y:.2f} Z"
        )

    def parse_color(value: str) -> tuple[str, float]:
        if value.startswith("rgba(") and value.endswith(")"):
            red, green, blue, alpha = [part.strip() for part in value[5:-1].split(",")]
            return f"rgb({red}, {green}, {blue})", float(alpha)
        return value, 1.0

    def format_tick(value: float, *, decimals: int = 1, suffix: str = "") -> str:
        if abs(value) < 1e-9:
            value = 0.0
        return f"{value:.{decimals}f}{suffix}"

    tick_lines: list[str] = []
    grid_stroke, grid_opacity = parse_color("rgba(128, 128, 128, 0.18)")
    time_grid_stroke, time_grid_opacity = parse_color("rgba(128, 128, 128, 0.12)")
    for power_tick in [
        -prepared.power_max,
        -prepared.power_max / 2,
        0.0,
        prepared.power_max / 2,
        prepared.power_max,
    ]:
        y_value = y_for("power", power_tick)
        tick_lines.extend(
            [
                f'<line x1="{chart_left}" y1="{y_value:.2f}" x2="{chart_right}" y2="{y_value:.2f}" '
                f'stroke="{grid_stroke}" stroke-opacity="{grid_opacity:.3f}" stroke-width="1"/>',
                f'<text x="{chart_left - 12}" y="{y_value + 4:.2f}" text-anchor="end" '
                'font-size="12" fill="#52606d">'
                f"{html.escape(format_tick(power_tick, decimals=1))}</text>",
            ]
        )

    tick_indexes = list(range(min(len(prepared.interval_boundaries), 7)))
    if len(prepared.interval_boundaries) > 7:
        tick_indexes = sorted(
            {
                round(index * (len(prepared.interval_boundaries) - 1) / 6)
                for index in range(7)
            }
        )
    for index in tick_indexes:
        x_value = x_for(prepared.interval_boundaries[index])
        label = prepared.interval_boundaries[index].strftime("%H:%M %d %b")
        tick_lines.extend(
            [
                f'<line x1="{x_value:.2f}" y1="{chart_top}" x2="{x_value:.2f}" y2="{chart_bottom}" '
                f'stroke="{time_grid_stroke}" stroke-opacity="{time_grid_opacity:.3f}" '
                'stroke-width="1"/>',
                f'<text x="{x_value:.2f}" y="{chart_bottom + 28}" text-anchor="middle" '
                'font-size="12" fill="#52606d">'
                f"{html.escape(label)}</text>",
            ]
        )

    curtailment_rects: list[str] = []
    for index, active in enumerate(prepared.curtailment_flags):
        if not active:
            continue
        fill, opacity = parse_color(COLORS["curtailment_fill"])
        x0 = x_for(prepared.interval_boundaries[index])
        x1 = x_for(prepared.interval_boundaries[index + 1])
        curtailment_rects.append(
            f'<rect x="{x0:.2f}" y="{chart_top}" width="{x1 - x0:.2f}" height="{chart_height}" '
            f'fill="{fill}" fill-opacity="{opacity:.3f}"/>'
        )

    trace_elements: list[str] = []
    for trace in prepared.traces:
        points = step_points(trace.times, trace.values)
        if not points:
            continue
        stroke, stroke_opacity = parse_color(trace.stroke_color)
        dash_attr = ' stroke-dasharray="6 4"' if trace.dash else ""
        if trace.fill_color is not None:
            fill, fill_opacity = parse_color(trace.fill_color)
            area_path = area_path_from_points(points, trace.axis)
            trace_elements.append(
                f'<path d="{area_path}" fill="{fill}" '
                f'fill-opacity="{fill_opacity:.3f}" stroke="none"/>'
            )
        trace_elements.append(
            f'<path d="{path_from_points(points, trace.axis)}" fill="none" stroke="{stroke}" '
            f'stroke-opacity="{stroke_opacity:.3f}" stroke-width="{trace.stroke_width}"{dash_attr} '
            'stroke-linejoin="round" stroke-linecap="round"/>'
        )

    soc_ticks: list[str] = []
    for tick in [0, 20, 40, 60, 80, 100]:
        y_value = y_for("soc", float(tick))
        soc_ticks.append(
            f'<text x="{soc_axis_x + 10}" y="{y_value + 4:.2f}" font-size="12" fill="#52606d">'
            f"{tick}%</text>"
        )

    price_ticks: list[str] = []
    for tick in [
        -prepared.price_max * 1.1,
        0.0,
        prepared.price_max * 1.1,
    ]:
        y_value = y_for("price", tick)
        price_ticks.append(
            f'<text x="{price_axis_x - 10}" y="{y_value + 4:.2f}" text-anchor="end" '
            'font-size="12" fill="#52606d">'
            f"{html.escape(format_tick(tick, decimals=2))}</text>"
        )

    legend_elements: list[str] = []
    legend_x = chart_left
    legend_y = height - 82
    for trace in prepared.traces:
        stroke, stroke_opacity = parse_color(trace.stroke_color)
        fill, fill_opacity = parse_color(trace.fill_color or trace.stroke_color)
        legend_elements.extend(
            [
                f'<rect x="{legend_x}" y="{legend_y - 10}" width="18" height="10" fill="{fill}" '
                f'fill-opacity="{fill_opacity:.3f}" stroke="{stroke}" '
                f'stroke-opacity="{stroke_opacity:.3f}"/>',
                f'<text x="{legend_x + 24}" y="{legend_y - 1}" font-size="12" fill="#1f2933">'
                f"{html.escape(trace.name)}</text>",
            ]
        )
        legend_x += max(120, len(trace.name) * 8 + 36)
        if legend_x > width - 250:
            legend_x = chart_left
            legend_y += 22

    total_cost = f"${prepared.total_cost:.2f}"
    total_export = f"{prepared.total_export_kwh:.2f} kWh"
    total_import = f"{prepared.total_import_kwh:.2f} kWh"

    svg_lines: list[str] = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
            f'viewBox="0 0 {width} {height}" role="img" aria-labelledby="ems-plan-title">'
        ),
        '<rect width="100%" height="100%" fill="white"/>',
        '<g font-family="Inter, Avenir Next, Trebuchet MS, sans-serif">',
        '<title id="ems-plan-title">EMS plan output</title>',
        f'<text x="{width / 2:.2f}" y="36" text-anchor="middle" font-size="24" fill="#1f2933">'
        'EMS Plan</text>',
        f'<text x="{width / 2:.2f}" y="64" text-anchor="middle" '
        'font-size="14" fill="#52606d">'
        f'Cost 💰: {html.escape(total_cost)}   |   '
        f'Grid Export 📤: {html.escape(total_export)}   '
        f'|   Grid Import 📥: {html.escape(total_import)}</text>',
    ]
    svg_lines.extend(curtailment_rects)
    svg_lines.extend(tick_lines)
    svg_lines.extend(
        [
            f'<line x1="{chart_left}" y1="{chart_bottom:.2f}" '
            f'x2="{chart_right}" y2="{chart_bottom:.2f}" '
            'stroke="#1f2933" stroke-width="1.5"/>',
            f'<line x1="{chart_left}" y1="{chart_top}" x2="{chart_left}" y2="{chart_bottom:.2f}" '
            'stroke="#1f2933" stroke-width="1.5"/>',
            f'<line x1="{soc_axis_x}" y1="{chart_top}" x2="{soc_axis_x}" y2="{chart_bottom:.2f}" '
            'stroke="#4b5563" stroke-width="1"/>',
            f'<line x1="{price_axis_x}" y1="{chart_top}" '
            f'x2="{price_axis_x}" y2="{chart_bottom:.2f}" '
            'stroke="#4b5563" stroke-width="1"/>',
        ]
    )
    svg_lines.extend(trace_elements)
    svg_lines.extend(
        [
            f'<text x="{chart_left - 56}" y="{chart_top - 12}" '
            'font-size="13" fill="#1f2933">Power (kW)</text>',
            f'<text x="{soc_axis_x + 10}" y="{chart_top - 12}" '
            'font-size="13" fill="#1f2933">SoC (%)</text>',
            f'<text x="{price_axis_x - 10}" y="{chart_top - 12}" text-anchor="end" '
            'font-size="13" fill="#1f2933">Price ($)</text>',
        ]
    )
    svg_lines.extend(soc_ticks)
    svg_lines.extend(price_ticks)
    svg_lines.extend(legend_elements)
    svg_lines.extend(["</g>", "</svg>"])
    return "\n".join(svg_lines)


def _prepare_static_plot(plan: EmsPlanOutput) -> _PreparedStaticPlot:
    local_tz = datetime.now().astimezone().tzinfo or UTC
    grid = _single_component(plan, "grid", GridComponentPlan)
    if grid is None:
        raise ValueError("Plan is missing required 'grid' component.")
    if not grid.net_kw:
        raise ValueError("Plan has no interval series to plot.")

    interval_points = grid.net_kw
    interval_end_times = _interval_end_times(plan, interval_points)
    interval_boundaries = [
        _normalize_time(point.time, local_tz=local_tz) for point in interval_points
    ]
    interval_boundaries.append(_normalize_time(interval_end_times[-1], local_tz=local_tz))

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

    total_pv = _aggregate_series(pv_series)
    total_curtailment = _aggregate_series(
        {name: _float_series(component.curtail_kw) for name, component in pv_components.items()}
    )
    curtailment_flags = _aggregate_bool_series(
        {
            name: _bool_series(component.curtailment)
            for name, component in pv_components.items()
        }
    )
    total_batt_charge = _aggregate_series(batt_charge)
    total_batt_discharge = _aggregate_series(batt_discharge)
    total_ev_charge = _aggregate_series(ev_charge)
    grid_net = _float_series(grid.net_kw)

    traces: list[_StaticTrace] = []
    if _has_any(total_pv):
        traces.append(
            _StaticTrace(
                name="PV Power",
                axis="power",
                times=interval_boundaries,
                values=total_pv,
                stroke_color=COLORS["pv"],
                fill_color=COLORS["pv_fill"],
            )
        )
    if _has_any(total_curtailment):
        traces.append(
            _StaticTrace(
                name="PV Curtailment",
                axis="power",
                times=interval_boundaries,
                values=total_curtailment,
                stroke_color=COLORS["curtailment"],
                fill_color=COLORS["curtailment_line"],
            )
        )
    if _has_any(load_kw):
        traces.append(
            _StaticTrace(
                name="Load",
                axis="power",
                times=interval_boundaries,
                values=load_kw,
                stroke_color=COLORS["load"],
                fill_color=COLORS["load_fill"],
            )
        )
    traces.append(
        _StaticTrace(
            name="Grid Net",
            axis="power",
            times=interval_boundaries,
            values=grid_net,
            stroke_color=COLORS["grid_net"],
            fill_color=COLORS["grid_net_fill"],
        )
    )
    if _has_any(total_batt_charge):
        traces.append(
            _StaticTrace(
                name="Battery Charge",
                axis="power",
                times=interval_boundaries,
                values=[-value for value in total_batt_charge],
                stroke_color=COLORS["batt_charge"],
                fill_color=COLORS["batt_charge_fill"],
            )
        )
    if _has_any(total_batt_discharge):
        traces.append(
            _StaticTrace(
                name="Battery Discharge",
                axis="power",
                times=interval_boundaries,
                values=total_batt_discharge,
                stroke_color=COLORS["batt_discharge"],
                fill_color=COLORS["batt_discharge_fill"],
            )
        )
    if _has_any(total_ev_charge):
        traces.append(
            _StaticTrace(
                name="EV Charge",
                axis="power",
                times=interval_boundaries,
                values=total_ev_charge,
                stroke_color=COLORS["ev_charge"],
                fill_color=COLORS["ev_charge_fill"],
            )
        )

    for name, series in batt_soc_pct.items():
        if _has_any(series):
            traces.append(
                _StaticTrace(
                    name=f"Battery SoC ({name})" if len(batt_soc_pct) > 1 else "Battery SoC",
                    axis="soc",
                    times=batt_soc_times[name],
                    values=series,
                    stroke_color=COLORS["batt_soc"],
                    stroke_width=3.0,
                    dash="dot",
                )
            )
    for name, series in ev_soc_pct.items():
        if _has_any(series):
            traces.append(
                _StaticTrace(
                    name=f"EV SoC ({name})" if len(ev_soc_pct) > 1 else "EV SoC",
                    axis="soc",
                    times=ev_soc_times[name],
                    values=series,
                    stroke_color=COLORS["ev_soc"],
                    stroke_width=3.0,
                    dash="dot",
                )
            )

    if _has_any(price_import):
        traces.append(
            _StaticTrace(
                name="Buy Price",
                axis="price",
                times=interval_boundaries,
                values=price_import,
                stroke_color=COLORS["price_import"],
            )
        )
    if _has_any(price_import_risk):
        traces.append(
            _StaticTrace(
                name="Buy Price (Risk Bias)",
                axis="price",
                times=interval_boundaries,
                values=price_import_risk,
                stroke_color=COLORS["price_import_risk"],
                stroke_width=1.5,
                dash="dot",
            )
        )
    if _has_any(price_export):
        traces.append(
            _StaticTrace(
                name="Sell Price",
                axis="price",
                times=interval_boundaries,
                values=price_export,
                stroke_color=COLORS["price_export"],
            )
        )
    if _has_any(price_export_risk):
        traces.append(
            _StaticTrace(
                name="Sell Price (Risk Bias)",
                axis="price",
                times=interval_boundaries,
                values=price_export_risk,
                stroke_color=COLORS["price_export_risk"],
                stroke_width=1.5,
                dash="dot",
            )
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
        max(abs(value) for value in price_import) if price_import else 0,
        max(abs(value) for value in price_export) if price_export else 0,
        max(abs(value) for value in price_import_risk) if price_import_risk else 0,
        max(abs(value) for value in price_export_risk) if price_export_risk else 0,
        0.01,
    )
    soc_values = [
        value for series in (*batt_soc_pct.values(), *ev_soc_pct.values()) for value in series
    ]
    soc_axis_max = max(max(soc_values, default=0.0), 100.0)
    power_max = max(
        max(abs(value) for value in grid_net) if grid_net else 0,
        max(abs(value) for value in load_kw) if load_kw else 0,
        max(abs(value) for value in total_pv) if total_pv else 0,
        max(abs(value) for value in total_curtailment) if total_curtailment else 0,
        max(abs(value) for value in total_batt_charge) if total_batt_charge else 0,
        max(abs(value) for value in total_batt_discharge) if total_batt_discharge else 0,
        max(abs(value) for value in total_ev_charge) if total_ev_charge else 0,
        1.0,
    )
    power_max = max(power_max * 1.1, 1.0)

    return _PreparedStaticPlot(
        traces=traces,
        interval_boundaries=interval_boundaries,
        curtailment_flags=curtailment_flags,
        power_max=power_max,
        soc_axis_max=soc_axis_max,
        price_max=price_max,
        total_cost=total_cost,
        total_import_kwh=total_import_kwh,
        total_export_kwh=total_export_kwh,
    )


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

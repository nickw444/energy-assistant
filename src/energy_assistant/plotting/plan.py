"""Interactive HTML plotting using Plotly."""

from __future__ import annotations

import html
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, tzinfo
from pathlib import Path
from typing import Any

from energy_assistant.ems.models import (
    EmsPlanOutput,
    EvTimestepPlan,
    InverterTimestepPlan,
    TimestepPlan,
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

_CURTAILMENT_THRESHOLD_KW = 0.01


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

    local_tz = datetime.now().astimezone().tzinfo or UTC
    timesteps = plan.timesteps
    if not timesteps:
        raise ValueError("Plan has no timesteps to plot.")

    times = [_normalize_time(step.start, local_tz=local_tz) for step in timesteps]
    times.append(_normalize_time(timesteps[-1].end, local_tz=local_tz))
    time_labels = times[:-1]

    grid_net = [float(step.grid.net_kw) for step in timesteps]
    load_kw = [float(step.loads.base_kw) for step in timesteps]

    pv_inverters = _collect_inverter_series(timesteps, lambda inv: inv.pv_kw)
    batt_charge = _collect_inverter_series(timesteps, lambda inv: inv.battery_charge_kw)
    batt_discharge = _collect_inverter_series(timesteps, lambda inv: inv.battery_discharge_kw)
    batt_soc_pct = _collect_inverter_series(timesteps, lambda inv: inv.battery_soc_pct)

    ev_charge = _collect_ev_series(timesteps, lambda ev: ev.charge_kw)
    ev_soc_pct = _collect_ev_series(timesteps, lambda ev: ev.soc_pct)

    price_import = [float(step.economics.price_import) for step in timesteps]
    price_export = [float(step.economics.price_export) for step in timesteps]
    price_import_risk = [float(step.economics.price_import_effective) for step in timesteps]
    price_export_risk = [float(step.economics.price_export_effective) for step in timesteps]

    curtailment_by_inverter = _collect_inverter_series(
        timesteps, lambda inv: inv.pv_curtail_kw
    )
    total_curtailment = _aggregate_series(curtailment_by_inverter)
    curtailment_flags = [value > _CURTAILMENT_THRESHOLD_KW for value in total_curtailment]

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

    total_pv = _aggregate_series(pv_inverters)
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
                        x=time_labels,
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
                        x=time_labels,
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

    total_cost = sum(float(step.economics.segment_cost) for step in timesteps)
    total_import_kwh = sum(
        float(step.grid.import_kw) * float(step.duration_s) / 3600.0 for step in timesteps
    )
    total_export_kwh = sum(
        float(step.grid.export_kw) * float(step.duration_s) / 3600.0 for step in timesteps
    )

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


def write_plan_image(
    plan: EmsPlanOutput,
    output: Path,
    *,
    width: int = 1600,
    height: int = 900,
) -> None:
    """Write the plan as a static JPEG image for PR review.

    Args:
        plan: The plan output to plot.
        output: Path to write the JPEG image.
        width: Image width in pixels.
        height: Image height in pixels.
    """
    fig, _ = _build_plan_figure(plan, include_hover=False)
    fig.write_image(str(output), width=width, height=height, format="jpeg", scale=2)
    if not output.exists():
        raise ValueError(f"Failed to write plan image to {output}")
    if output.stat().st_size == 0:
        raise ValueError(f"Plan image {output} is empty")


def _normalize_time(value: datetime, *, local_tz: tzinfo) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=local_tz)
    return value.astimezone(local_tz)


def _collect_inverter_series(
    timesteps: list[TimestepPlan],
    accessor: Callable[[InverterTimestepPlan], float | None],
) -> dict[str, list[float]]:
    names = sorted({inv.name for step in timesteps for inv in step.inverters.values()})
    series: dict[str, list[float]] = {name: [] for name in names}
    for step in timesteps:
        inv_map = {inv.name: inv for inv in step.inverters.values()}
        for name in names:
            inv = inv_map.get(name)
            raw = accessor(inv) if inv is not None else None
            series[name].append(float(raw) if raw is not None else 0.0)
    return series


def _collect_ev_series(
    timesteps: list[TimestepPlan],
    accessor: Callable[[EvTimestepPlan], float | None],
) -> dict[str, list[float]]:
    names = sorted({ev.name for step in timesteps for ev in step.loads.evs.values()})
    series: dict[str, list[float]] = {name: [] for name in names}
    for step in timesteps:
        ev_map = {ev.name: ev for ev in step.loads.evs.values()}
        for name in names:
            ev = ev_map.get(name)
            raw = accessor(ev) if ev is not None else None
            series[name].append(float(raw) if raw is not None else 0.0)
    return series


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


def _has_any(values: list[float]) -> bool:
    return any(abs(value) > 1e-9 for value in values)

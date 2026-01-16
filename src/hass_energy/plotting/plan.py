"""Interactive HTML plotting using Plotly."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, tzinfo
from pathlib import Path

from hass_energy.ems.models import (
    EmsPlanOutput,
    EvTimestepPlan,
    InverterTimestepPlan,
    TimestepPlan,
)


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
    try:
        import plotly.graph_objects as go  # pyright: ignore[reportUnknownVariableType]
        from plotly.subplots import make_subplots  # pyright: ignore[reportUnknownVariableType]
    except ImportError as exc:
        raise ImportError("plotly is required for interactive plots: uv add plotly") from exc

    local_tz = datetime.now().astimezone().tzinfo or UTC
    timesteps = plan.timesteps
    if not timesteps:
        raise ValueError("Plan has no timesteps to plot.")

    times = [_normalize_time(step.start, local_tz=local_tz) for step in timesteps]
    times.append(_normalize_time(timesteps[-1].end, local_tz=local_tz))

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

    has_soc = any(_has_any(series) for series in batt_soc_pct.values()) or any(
        _has_any(series) for series in ev_soc_pct.values()
    )
    has_price = _has_any(price_import) or _has_any(price_export)

    fig = make_subplots(
        rows=1,
        cols=1,
        specs=[[{"secondary_y": True}]],
    )

    colors = {
        "pv": "rgba(255, 193, 7, 1.0)",
        "pv_fill": "rgba(255, 193, 7, 0.5)",
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
    }

    time_labels = times[:-1]
    legend_group_power = "Power"
    legend_group_soc = "State of Charge"
    legend_group_price = "Price"

    for name, series in pv_inverters.items():
        if _has_any(series):
            fig.add_trace(
                go.Scatter(
                    x=time_labels,
                    y=series,
                    name=f"Inverter {name} PV Power",
                    mode="lines",
                    fill="tozeroy",
                    fillcolor=colors["pv_fill"],
                    line={"color": colors["pv"], "width": 2, "shape": "hv"},
                    hovertemplate="%{y:.2f} kW<extra>PV {name}</extra>",
                    legendgroup=legend_group_power,
                ),
                secondary_y=False,
            )

    if _has_any(load_kw):
        fig.add_trace(
            go.Scatter(
                x=time_labels,
                y=load_kw,
                name="HASS Energy Load Base Power",
                mode="lines",
                fill="tozeroy",
                fillcolor=colors["load_fill"],
                line={"color": colors["load"], "width": 2, "shape": "hv"},
                hovertemplate="%{y:.2f} kW<extra>Load</extra>",
                legendgroup=legend_group_power,
            ),
            secondary_y=False,
        )

    if _has_any(grid_net):
        fig.add_trace(
            go.Scatter(
                x=time_labels,
                y=grid_net,
                name="HASS Energy Grid Net Power",
                mode="lines",
                fill="tozeroy",
                fillcolor=colors["grid_net_fill"],
                line={"color": colors["grid_net"], "width": 2, "shape": "hv"},
                hovertemplate="%{y:.2f} kW<extra>Grid Net</extra>",
                legendgroup=legend_group_power,
            ),
            secondary_y=False,
        )

    for name, charge_series in batt_charge.items():
        discharge_series = batt_discharge.get(name, [0.0] * len(charge_series))
        charge_neg = [-v for v in charge_series]
        if _has_any(charge_series):
            fig.add_trace(
                go.Scatter(
                    x=time_labels,
                    y=charge_neg,
                    name=f"Inverter {name} Battery Charge Power",
                    mode="lines",
                    fill="tozeroy",
                    fillcolor=colors["batt_charge_fill"],
                    line={"color": colors["batt_charge"], "width": 2, "shape": "hv"},
                    hovertemplate="%{y:.2f} kW<extra>Batt Charge</extra>",
                    legendgroup=legend_group_power,
                ),
                secondary_y=False,
            )
        if _has_any(discharge_series):
            fig.add_trace(
                go.Scatter(
                    x=time_labels,
                    y=discharge_series,
                    name=f"Inverter {name} Battery Discharge Power",
                    mode="lines",
                    fill="tozeroy",
                    fillcolor=colors["batt_discharge_fill"],
                    line={"color": colors["batt_discharge"], "width": 2, "shape": "hv"},
                    hovertemplate="%{y:.2f} kW<extra>Batt Discharge</extra>",
                    legendgroup=legend_group_power,
                ),
                secondary_y=False,
            )

    for name, series in ev_charge.items():
        if _has_any(series):
            charge_neg = [-v for v in series]
            fig.add_trace(
                go.Scatter(
                    x=time_labels,
                    y=charge_neg,
                    name=f"Load {name} Charge Power",
                    mode="lines",
                    fill="tozeroy",
                    fillcolor=colors["ev_charge_fill"],
                    line={"color": colors["ev_charge"], "width": 2, "shape": "hv"},
                    hovertemplate="%{y:.2f} kW<extra>EV Charge</extra>",
                    legendgroup=legend_group_power,
                ),
                secondary_y=False,
            )

    if has_soc:
        for name, series in batt_soc_pct.items():
            if _has_any(series):
                fig.add_trace(
                    go.Scatter(
                        x=time_labels,
                        y=series,
                        name=f"Inverter {name} Battery SoC",
                        mode="lines",
                        line={
                            "color": colors["batt_soc"],
                            "width": 4,
                            "shape": "hv",
                            "dash": "dot",
                        },
                        hovertemplate="%{y:.1f}%<extra>Batt SoC</extra>",
                        legendgroup=legend_group_soc,
                    ),
                    secondary_y=True,
                )
        for name, series in ev_soc_pct.items():
            if _has_any(series):
                fig.add_trace(
                    go.Scatter(
                        x=time_labels,
                        y=series,
                        name=f"Load {name} SoC",
                        mode="lines",
                        line={"color": colors["ev_soc"], "width": 4, "shape": "hv", "dash": "dot"},
                        hovertemplate="%{y:.1f}%<extra>EV SoC</extra>",
                        legendgroup=legend_group_soc,
                    ),
                    secondary_y=True,
                )

    if has_price:
        price_y_axis = "y3"
        if _has_any(price_import):
            current_price = price_import[0] if price_import else 0
            fig.add_trace(
                go.Scatter(
                    x=time_labels,
                    y=price_import,
                    name=f"Buy Price: {current_price:.2f} $/kWh",
                    mode="lines",
                    line={"color": colors["price_import"], "width": 2, "shape": "hv"},
                    yaxis=price_y_axis,
                    hovertemplate="%{y:.3f} $/kWh<extra>Buy Price</extra>",
                    legendgroup=legend_group_price,
                ),
            )
        if _has_any(price_export):
            current_price = price_export[0] if price_export else 0
            fig.add_trace(
                go.Scatter(
                    x=time_labels,
                    y=price_export,
                    name=f"Sell Price: {current_price:.2f} $/kWh",
                    mode="lines",
                    line={"color": colors["price_export"], "width": 2, "shape": "hv"},
                    yaxis=price_y_axis,
                    hovertemplate="%{y:.3f} $/kWh<extra>Sell Price</extra>",
                    legendgroup=legend_group_price,
                ),
            )

    total_cost = sum(float(step.economics.segment_cost) for step in timesteps)

    price_max = max(
        max(abs(p) for p in price_import) if price_import else 0,
        max(abs(p) for p in price_export) if price_export else 0,
        0.01,
    )

    power_max = max(
        max(abs(v) for v in grid_net) if grid_net else 0,
        max(abs(v) for v in load_kw) if load_kw else 0,
        max(max(abs(v) for v in series) for series in pv_inverters.values())
        if pv_inverters
        else 0,
        max(max(abs(v) for v in series) for series in batt_charge.values())
        if batt_charge
        else 0,
        max(max(abs(v) for v in series) for series in batt_discharge.values())
        if batt_discharge
        else 0,
        1.0,
    )
    power_max = max(power_max * 1.1, 1.0)

    fig.update_layout(
        title={
            "text": (
                f"<b>🔋 EMS Plan</b> &nbsp;|&nbsp; "
                f"Cumulative Cost: <span style='color:#00bcd4'>${total_cost:.2f}</span>"
            ),
            "x": 0.5,
            "xanchor": "center",
            "y": 0.98,
            "yanchor": "top",
            "font": {"size": 16},
        },
        xaxis={
            "title": None,
            "showgrid": True,
            "gridcolor": "rgba(128, 128, 128, 0.2)",
            "tickformat": "%I:%M %p\n%d %b",
            "hoverformat": "%Y-%m-%d %H:%M",
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
            "title": "Battery State of Charge",
            "overlaying": "y",
            "side": "right",
            "showgrid": False,
            "range": [0, 105],
            "ticksuffix": "%",
        },
        yaxis3={
            "title": "Price ($)",
            "overlaying": "y",
            "side": "right",
            "position": 0.95,
            "anchor": "free",
            "showgrid": False,
            "range": [-price_max * 1.1, price_max * 1.1],
            "tickformat": ".2f",
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
        margin={"l": 60, "r": 120, "t": 50, "b": 100},
    )

    fig.update_traces(
        hoverlabel={"namelength": -1},
    )

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

    legend_hover_script = """
    (function() {
        var gd = document.querySelector('.plotly-graph-div');
        if (!gd || !gd._fullData) {
            setTimeout(arguments.callee, 100);
            return;
        }
        var legend = gd.querySelector('.legend');
        if (!legend) {
            setTimeout(arguments.callee, 100);
            return;
        }
        var style = document.createElement('style');
        style.textContent = '.trace.faded { opacity: 0.15 !important; }';
        document.head.appendChild(style);

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
    })();
    """
    html_content: str = fig.to_html(
        full_html=True, include_plotlyjs=True, post_script=legend_hover_script
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


def _has_any(values: list[float]) -> bool:
    return any(abs(value) > 1e-9 for value in values)


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

    has_soc = any(_has_any(series) for series in batt_soc_pct.values()) or any(
        _has_any(series) for series in ev_soc_pct.values()
    )
    has_price = _has_any(price_import) or _has_any(price_export)

    fig = make_subplots(
        rows=1,
        cols=1,
        specs=[[{"secondary_y": True}]],
    )

    colors = {
        "pv": "rgba(255, 193, 7, 1.0)",
        "pv_fill": "rgba(255, 193, 7, 0.5)",
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
    }

    time_labels = times[:-1]
    legend_group_power = "Power"
    legend_group_soc = "State of Charge"
    legend_group_price = "Price"

    total_pv = [0.0] * len(time_labels)
    for _name, series in pv_inverters.items():
        if _has_any(series):
            for i, v in enumerate(series):
                total_pv[i] += v

    if _has_any(total_pv):
        fig.add_trace(
            go.Scatter(
                x=time_labels,
                y=total_pv,
                name="PV Power",
                mode="lines",
                fill="tozeroy",
                fillcolor=colors["pv_fill"],
                line={"color": colors["pv"], "width": 2, "shape": "hv"},
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
                fillcolor=colors["load_fill"],
                line={"color": colors["load"], "width": 2, "shape": "hv"},
                legendgroup=legend_group_power,
            ),
            secondary_y=False,
        )

    if _has_any(grid_net):
        fig.add_trace(
            go.Scatter(
                x=time_labels,
                y=grid_net,
                name="Grid Net",
                mode="lines",
                fill="tozeroy",
                fillcolor=colors["grid_net_fill"],
                line={"color": colors["grid_net"], "width": 2, "shape": "hv"},
                legendgroup=legend_group_power,
            ),
            secondary_y=False,
        )

    total_batt_charge = [0.0] * len(time_labels)
    total_batt_discharge = [0.0] * len(time_labels)
    for name in batt_charge:
        for i, v in enumerate(batt_charge[name]):
            total_batt_charge[i] += v
        for i, v in enumerate(batt_discharge.get(name, [0.0] * len(time_labels))):
            total_batt_discharge[i] += v

    if _has_any(total_batt_charge):
        charge_neg = [-v for v in total_batt_charge]
        fig.add_trace(
            go.Scatter(
                x=time_labels,
                y=charge_neg,
                name="Battery Charge",
                mode="lines",
                fill="tozeroy",
                fillcolor=colors["batt_charge_fill"],
                line={"color": colors["batt_charge"], "width": 2, "shape": "hv"},
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
                fillcolor=colors["batt_discharge_fill"],
                line={"color": colors["batt_discharge"], "width": 2, "shape": "hv"},
                legendgroup=legend_group_power,
            ),
            secondary_y=False,
        )

    total_ev_charge = [0.0] * len(time_labels)
    for _name, series in ev_charge.items():
        for i, v in enumerate(series):
            total_ev_charge[i] += v
    if _has_any(total_ev_charge):
        charge_neg = [-v for v in total_ev_charge]
        fig.add_trace(
            go.Scatter(
                x=time_labels,
                y=charge_neg,
                name="EV Charge",
                mode="lines",
                fill="tozeroy",
                fillcolor=colors["ev_charge_fill"],
                line={"color": colors["ev_charge"], "width": 2, "shape": "hv"},
                legendgroup=legend_group_power,
            ),
            secondary_y=False,
        )

    if has_soc:
        for name, series in batt_soc_pct.items():
            if _has_any(series):
                fig.add_trace(
                    go.Scatter(
                        x=time_labels,
                        y=series,
                        name=f"Battery SoC ({name})",
                        mode="lines",
                        line={
                            "color": colors["batt_soc"],
                            "width": 3,
                            "shape": "hv",
                            "dash": "dot",
                        },
                        legendgroup=legend_group_soc,
                    ),
                    secondary_y=True,
                )
        for name, series in ev_soc_pct.items():
            if _has_any(series):
                fig.add_trace(
                    go.Scatter(
                        x=time_labels,
                        y=series,
                        name=f"EV SoC ({name})",
                        mode="lines",
                        line={"color": colors["ev_soc"], "width": 3, "shape": "hv", "dash": "dot"},
                        legendgroup=legend_group_soc,
                    ),
                    secondary_y=True,
                )

    if has_price:
        price_y_axis = "y3"
        if _has_any(price_import):
            fig.add_trace(
                go.Scatter(
                    x=time_labels,
                    y=price_import,
                    name="Buy Price",
                    mode="lines",
                    line={"color": colors["price_import"], "width": 2, "shape": "hv"},
                    yaxis=price_y_axis,
                    legendgroup=legend_group_price,
                ),
            )
        if _has_any(price_export):
            fig.add_trace(
                go.Scatter(
                    x=time_labels,
                    y=price_export,
                    name="Sell Price",
                    mode="lines",
                    line={"color": colors["price_export"], "width": 2, "shape": "hv"},
                    yaxis=price_y_axis,
                    legendgroup=legend_group_price,
                ),
            )

    total_cost = sum(float(step.economics.segment_cost) for step in timesteps)

    price_max = max(
        max(abs(p) for p in price_import) if price_import else 0,
        max(abs(p) for p in price_export) if price_export else 0,
        0.01,
    )

    power_max = max(
        max(abs(v) for v in grid_net) if grid_net else 0,
        max(abs(v) for v in load_kw) if load_kw else 0,
        max(abs(v) for v in total_pv) if total_pv else 0,
        max(abs(v) for v in total_batt_charge) if total_batt_charge else 0,
        max(abs(v) for v in total_batt_discharge) if total_batt_discharge else 0,
        1.0,
    )
    power_max = max(power_max * 1.1, 1.0)

    fig.update_layout(
        title={
            "text": f"EMS Plan | Cost: ${total_cost:.2f}",
            "x": 0.5,
            "xanchor": "center",
            "font": {"size": 18},
        },
        xaxis={
            "title": None,
            "showgrid": True,
            "gridcolor": "rgba(128, 128, 128, 0.2)",
            "tickformat": "%I:%M %p\n%d %b",
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
            "title": "SoC (%)",
            "overlaying": "y",
            "side": "right",
            "showgrid": False,
            "range": [0, 105],
            "ticksuffix": "%",
        },
        yaxis3={
            "title": "Price ($)",
            "overlaying": "y",
            "side": "right",
            "position": 0.95,
            "anchor": "free",
            "showgrid": False,
            "range": [-price_max * 1.1, price_max * 1.1],
            "tickformat": ".2f",
        },
        legend={
            "orientation": "h",
            "yanchor": "top",
            "y": -0.15,
            "xanchor": "center",
            "x": 0.5,
        },
        plot_bgcolor="white",
        paper_bgcolor="white",
        margin={"l": 60, "r": 120, "t": 50, "b": 100},
    )

    fig.write_image(str(output), width=width, height=height, format="jpeg", scale=2)

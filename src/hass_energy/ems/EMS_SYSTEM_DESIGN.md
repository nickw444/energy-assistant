Here’s a full end-to-end design doc for the EMS MILP system based on everything we’ve discussed. I’ll keep it practical and implementation-oriented, with code snippets you can drop into a real codebase.

---

# Home EMS MILP Optimiser — System Design Document

## 1. Purpose & Scope

This system is a **MILP-based optimiser** that:

* Takes a **structured plant configuration** (your YAML with grid, inverters, PV, battery, controllable loads like Tessie).
* Resolves **realtime + forecast data** from Home Assistant (and others).
* Builds a **PuLP MILP model** representing:

  * physical constraints (power limits, SoC, inverter topology),
  * economics (grid prices, FiT),
  * incentives (self-consumption, EV SoC, battery wear).
* Solves for an **optimal control plan** over a time horizon (e.g. 24h with 5-min intervals).
* Applies **only the first interval’s actions** to the real system; the rest is for visualisation.

Architecture is based on **Option 2 — hierarchical component builders**:

* The YAML config **is the graph**.
* We compile it top-down into MILP variables and constraints.
* Each component builder (grid, inverter, PV, battery, load) owns its own variables and constraints.

---

## 2. High-Level Architecture

### 2.1 Modules

Proposed Python package layout:

```text
ems/
  config/
    models.py          # Pydantic config schema
    loader.py          # Load + validate YAML
  horizon/
    horizon.py         # Time discretisation (Option C)
  data/
    timeseries.py      # TimeSeriesHandle interfaces and HA adapters
  model/
    builder.py         # MILPBuilder and component builders
    objective.py       # Objective construction
  runtime/
    solver.py          # End-to-end solve() orchestration
    actions.py         # Mapping MILP outputs → inverter / HA commands
```

### 2.2 Data Flow

At each optimisation tick:

1. **Load config** (once at startup) → `Config` (Pydantic).
2. **Resolve dynamic data**:

   * prices, PV forecast, load, SoC, etc. via `TimeSeriesHandle`s.
3. **Construct Horizon**:

   * now, intervals (5 min), number of intervals (e.g. 288 for 24h).
4. **Build MILP model** via `MILPBuilder`:

   * create variables & constraints for grid, inverters, PV, battery, loads.
   * add objective.
5. **Solve** with PuLP.
6. **Extract plan**:

   * actions for `t=0` → commands for Home Assistant / inverter API.
   * future intervals → visualisation.

---

## 3. Configuration Layer

You already have a detailed YAML. We’ll model it with Pydantic.

### 3.1 YAML (simplified example)

*(Token redacted and some details trimmed)*

```yaml
server:
  host: 127.0.0.1
  port: 8000
  data_dir: ./data

homeassistant:
  base_url: "https://hass.nickwhyte.com"
  token: "YOUR_LONG_LIVED_TOKEN"
  verify_tls: true

ems:
  interval_duration: 5
  num_intervals: 288   # 24h

plant:
  grid:
    max_import_kw: 13.0
    max_export_kw: 13.0
    realtime_grid_power:
      type: home_assistant
      entity: sensor.hass_energy_grid_power_smoothed_1m
    realtime_price_import:
      type: home_assistant
      entity: sensor.amber_general_price
    realtime_price_export:
      type: home_assistant
      entity: sensor.amber_feed_in_price
    price_import_forecast:
      type: home_assistant
      platform: amberelectric
      entity: sensor.amber_general_forecast
      use_advanced_price_forecast: true
    price_export_forecast:
      type: home_assistant
      platform: amberelectric
      entity: sensor.amber_feed_in_forecast
      use_advanced_price_forecast: true
    import_forbidden_periods:
      - start: "14:55"
        end: "21:05"

  load:
    realtime_load_power:
      type: home_assistant
      entity: sensor.hass_energy_load_power_smoothed_1m
    forecast:
      type: home_assistant
      platform: historical_average
      entity: sensor.hass_energy_load_power_smoothed_1m
      history_days: 7
      interval_duration: 5
      unit: W

  inverters:
    - name: Primary
      peak_power_kw: 10.0
      ac_efficiency_pct: 96.0
      curtailment: load-aware
      pv:
        realtime_power:
          type: home_assistant
          entity: sensor.hass_energy_pv_power_smoothed_1m
        forecast:
          type: home_assistant
          platform: solcast
          entities:
            - sensor.solcast_pv_forecast_forecast_today
            - sensor.solcast_pv_forecast_forecast_tomorrow
            - sensor.solcast_pv_forecast_forecast_day_3
      battery:
        capacity_kwh: 41.9
        min_soc_pct: 10.0
        max_soc_pct: 100.0
        reserve_soc_pct: 20.0
        max_charge_kw: 11.0 
        max_discharge_kw: 11.0
        state_of_charge_pct:
          type: home_assistant
          entity: sensor.inverter_battery_soc

    - name: Sungrow
      peak_power_kw: 5.0
      ac_efficiency_pct: 95.0
      curtailment: binary
      pv:
        forecast:
          type: home_assistant
          platform: solcast
          entities:
            - sensor.solcast_pv_forecast_forecast_today
            - sensor.solcast_pv_forecast_forecast_tomorrow
            - sensor.solcast_pv_forecast_forecast_day_3

loads:
  - name: "Tessie"
    load_type: "controlled_ev"
    min_power_kw: 0.0
    max_power_kw: 7.4
    energy_kwh: 78.0
    connected:
      type: home_assistant
      entity: binary_sensor.tesla_wall_connector_vehicle_connected
    realtime_power:
      type: home_assistant
      entity: sensor.tessie_charger_power
    state_of_charge_pct:
      type: home_assistant
      entity: sensor.tessie_battery
    soc_incentives:
      - target_soc_pct: 40.0
        incentive: 0.08
      - target_soc_pct: 60.0
        incentive: 0.06
      - target_soc_pct: 80.0
        incentive: 0.04
      - target_soc_pct: 90.0
        incentive: 0.00
```

### 3.2 Pydantic Models (sketch)

```python
# ems/config/models.py
from pydantic import BaseModel, Field
from typing import Literal, List, Optional


class HomeAssistantEntitySource(BaseModel):
    type: Literal["home_assistant"]
    entity: str = Field(min_length=1)


class PriceForecastSource(BaseModel):
    type: Literal["home_assistant"]
    platform: Literal["amberelectric", "solcast"]
    entity: str
    entities: Optional[list[str]] = None
    use_advanced_price_forecast: Optional[bool] = None


class ImportForbiddenPeriod(BaseModel):
    start: str  # "HH:MM"
    end: str


class GridConfig(BaseModel):
    max_import_kw: float
    max_export_kw: float
    realtime_grid_power: HomeAssistantEntitySource
    realtime_price_import: HomeAssistantEntitySource
    realtime_price_export: HomeAssistantEntitySource
    price_import_forecast: PriceForecastSource
    price_export_forecast: PriceForecastSource
    import_forbidden_periods: list[ImportForbiddenPeriod] = Field(default_factory=list)


class PVConfig(BaseModel):
    realtime_power: Optional[HomeAssistantEntitySource] = None
    forecast: PriceForecastSource  # repurposed for PV forecast (required)


class BatteryConfig(BaseModel):
    capacity_kwh: float
    min_soc_pct: float
    max_soc_pct: float
    reserve_soc_pct: float
    max_charge_kw: float
    max_discharge_kw: float
    state_of_charge_pct: HomeAssistantEntitySource


class InverterConfig(BaseModel):
    name: str
    peak_power_kw: float
    ac_efficiency_pct: float
    curtailment: Optional[Literal["load-aware", "binary"]] = None
    pv: PVConfig
    battery: Optional[BatteryConfig] = None


class LoadRealtimeConfig(BaseModel):
    realtime_load_power: HomeAssistantEntitySource
    forecast: Optional[HomeAssistantEntitySource] = None


class EVSocIncentive(BaseModel):
    target_soc_pct: float
    incentive: float


class ControlledEVConfig(BaseModel):
    name: str
    load_type: Literal["controlled_ev"]
    min_power_kw: float
    max_power_kw: float
    energy_kwh: float
    connected: HomeAssistantEntitySource
    realtime_power: HomeAssistantEntitySource
    state_of_charge_pct: HomeAssistantEntitySource
    soc_incentives: list[EVSocIncentive]


class PlantConfig(BaseModel):
    grid: GridConfig
    load: LoadRealtimeConfig
    inverters: list[InverterConfig]


class EMSConfig(BaseModel):
    interval_duration: int
    num_intervals: int


class RootConfig(BaseModel):
    ems: EMSConfig
    plant: PlantConfig
    loads: list[ControlledEVConfig]
```

---

## 4. Time & Horizon Model

### 4.1 Horizon (Option C — realtime lead-in + aligned forecast)

```python
# ems/horizon/horizon.py
from datetime import datetime, timedelta


def align_to_interval_boundary(now: datetime, interval_minutes: int) -> datetime:
    # Round up to next multiple of interval_minutes
    minutes = (now.minute // interval_minutes) * interval_minutes
    base = now.replace(minute=minutes, second=0, microsecond=0)
    if base < now:
        base += timedelta(minutes=interval_minutes)
    return base


class Horizon:
    def __init__(self, now: datetime, interval_minutes: int, num_intervals: int):
        self.now = now
        self.interval_minutes = interval_minutes
        self.num_intervals = num_intervals

        self.align_time = align_to_interval_boundary(now, interval_minutes)
        self.dt0_minutes = (self.align_time - now).total_seconds() / 60.0
        self.dt_regular_minutes = interval_minutes

    @property
    def T(self):
        return range(self.num_intervals)

    def dt_hours(self, t: int) -> float:
        if t == 0:
            return self.dt0_minutes / 60.0
        return self.dt_regular_minutes / 60.0

    def time_window(self, t: int) -> tuple[datetime, datetime]:
        if t == 0:
            return self.now, self.align_time
        start = self.align_time + timedelta(
            minutes=(t - 1) * self.interval_minutes
        )
        end = start + timedelta(minutes=self.interval_minutes)
        return start, end
```

---

## 5. Time Series Resolution Layer

### 5.1 TimeSeriesHandle Interface

```python
# ems/data/timeseries.py
from abc import ABC, abstractmethod
from typing import List
from ..horizon.horizon import Horizon


class TimeSeriesHandle(ABC):
    @abstractmethod
    def resolve(self, horizon: Horizon) -> List[float]:
        """Return one value per timestep in the horizon."""
        ...


class ScalarHandle(ABC):
    @abstractmethod
    def resolve_current(self) -> float:
        """Return a single current value (e.g. SoC%)."""
        ...
```

You’ll implement HA-backed subclasses, e.g. `HAEntityHandle`, `AmberPriceForecastHandle`, `SolcastForecastHandle`.

These are responsible for sticking together realtime + forecast into a single, aligned array for the horizon.

---

## 6. MILP Model Layer

We use PuLP as the MILP backend.

### 6.1 MILPBuilder Skeleton

```python
# ems/model/builder.py
import pulp
from typing import Dict, Any
from ..config.models import PlantConfig, ControlledEVConfig
from ..horizon.horizon import Horizon
from .objective import build_objective


class MILPModel:
    def __init__(self, problem: pulp.LpProblem, vars: dict[str, Any]):
        self.problem = problem
        self.vars = vars


class MILPBuilder:
    def __init__(
        self,
        plant_cfg: PlantConfig,
        ev_cfgs: list[ControlledEVConfig],
        horizon: Horizon,
        resolved_data: dict[str, Any],  # map of resolved time series, SoC, etc.
    ):
        self.plant_cfg = plant_cfg
        self.ev_cfgs = ev_cfgs
        self.horizon = horizon
        self.resolved = resolved_data

        self.problem = pulp.LpProblem("ems_optimisation", pulp.LpMinimize)
        self.vars: dict[str, Any] = {}

    def build(self) -> MILPModel:
        self._build_grid()
        self._build_ac_bus()
        self._build_inverters()
        self._build_ev_loads()
        build_objective(self.problem, self.vars, self.resolved, self.horizon)
        return MILPModel(self.problem, self.vars)
```

We’ll flesh out `_build_grid`, `_build_inverters`, `_build_ev_loads` etc. below.

---

## 7. Component Builders

### 7.1 Grid + AC Bus

```python
# ems/model/builder.py (continued)

    def _build_grid(self):
        T = self.horizon.T
        cfg = self.plant_cfg.grid

        P_import = pulp.LpVariable.dicts(
            "P_grid_import", T, lowBound=0
        )
        P_export = pulp.LpVariable.dicts(
            "P_grid_export", T, lowBound=0
        )

        self.vars["P_grid_import"] = P_import
        self.vars["P_grid_export"] = P_export

        for t in T:
            self.problem += (
                P_import[t] <= cfg.max_import_kw,
                f"grid_import_cap_t{t}",
            )
            self.problem += (
                P_export[t] <= cfg.max_export_kw,
                f"grid_export_cap_t{t}",
            )

        # Forbidden import periods
        grid_import_allowed = self.resolved["grid_import_allowed"]  # [0/1] per t
        for t in T:
            self.problem += (
                P_import[t] <= cfg.max_import_kw * grid_import_allowed[t],
                f"grid_import_forbidden_t{t}",
            )

    def _build_ac_bus(self):
        """
        AC bus balance is built after we have:
        - grid import/export vars
        - inverter AC vars
        - load vars
        So we call this at the end of build(), or treat it as a separate
        pass that uses self.vars.
        """
        pass  # placeholder; we’ll show the idea below
```

We’ll define AC bus balance once all components are created. Conceptually:

```python
def _build_ac_bus(self):
    T = self.horizon.T
    P_import = self.vars["P_grid_import"]
    P_export = self.vars["P_grid_export"]
    P_load = self.vars["P_load_total"]  # fixed load
    P_inv_ac = self.vars["P_inv_ac"]    # dict[inverter_name][t]
    P_ev = self.vars["P_ev_charge"]     # dict[ev_name][t]

    for t in T:
        self.problem += (
            P_import[t]
            + sum(P_inv_ac[inv][t] for inv in P_inv_ac.keys())
            - P_export[t]
            - P_load[t]
            - sum(P_ev[ev][t] for ev in P_ev.keys())
            == 0,
            f"ac_power_balance_t{t}",
        )
```

### 7.2 PV & Inverter

Per inverter:

* AC variable `P_inv_ac[i,t]` (signed).
* DC net power variable `P_inv_dc_net[i,t]`.
* PV used variables per PV.
* Boolean `Curtail_inv[i,t]`.

```python
    def _build_inverters(self):
        T = self.horizon.T
        self.vars["P_inv_ac"] = {}
        self.vars["P_inv_dc_net"] = {}
        self.vars["Curtail_inv"] = {}
        self.vars["P_pv_used"] = {}

        for inv_cfg in self.plant_cfg.inverters:
            inv_name = inv_cfg.name

            P_inv_ac = pulp.LpVariable.dicts(
                f"P_inv_ac[{inv_name}]", T,
                lowBound=-inv_cfg.peak_power_kw,
                upBound=inv_cfg.peak_power_kw,
            )
            P_inv_dc_net = pulp.LpVariable.dicts(
                f"P_inv_dc_net[{inv_name}]", T
            )
            Curtail = pulp.LpVariable.dicts(
                f"Curtail_inv[{inv_name}]", T, lowBound=0, upBound=1, cat="Binary"
            )

            self.vars["P_inv_ac"][inv_name] = P_inv_ac
            self.vars["P_inv_dc_net"][inv_name] = P_inv_dc_net
            self.vars["Curtail_inv"][inv_name] = Curtail

            # AC efficiency
            eta_ac = inv_cfg.ac_efficiency_pct / 100.0
            for t in T:
                self.problem += (
                    P_inv_ac[t] == eta_ac * P_inv_dc_net[t],
                    f"inv_dc_ac_link[{inv_name}]_t{t}",
                )

            # PV on this inverter
            pv_used_for_inv = {}
            pv_available = self.resolved["pv_available"][inv_name]  # dict[j][t]

            for j, pv_cfg in enumerate(inv_cfg.pv):
                P_pv_used = pulp.LpVariable.dicts(
                    f"P_pv_used[{inv_name},{j}]", T, lowBound=0
                )
                pv_used_for_inv[j] = P_pv_used

                for t in T:
                    P_avail = pv_available[j][t]
                    self.problem += (
                        P_pv_used[t] <= P_avail,
                        f"pv_used_cap[{inv_name},{j}]_t{t}",
                    )

            self.vars["P_pv_used"][inv_name] = pv_used_for_inv

            # Curtailment gating: sum(P_used) ≥ sum(P_avail) * (1 - Curtail)
            for t in T:
                total_used = sum(
                    pv_used_for_inv[j][t] for j in pv_used_for_inv.keys()
                )
                total_avail = sum(
                    pv_available[j][t] for j in pv_available.keys()
                )
                self.problem += (
                    total_used >= total_avail * (1 - Curtail[t]),
                    f"pv_curtail_gate[{inv_name}]_t{t}",
                )

            # DC bus balance gets finished after battery is built
```

### 7.3 Battery

For the battery attached to an inverter:

```python
    def _build_batteries(self):
        T = self.horizon.T
        if "P_bat_charge" not in self.vars:
            self.vars["P_bat_charge"] = {}
            self.vars["P_bat_discharge"] = {}
            self.vars["SOC"] = {}

        for inv_cfg in self.plant_cfg.inverters:
            inv_name = inv_cfg.name
            bat_cfg = inv_cfg.battery
            if bat_cfg is None:
                continue
            bat_id = f"{inv_name}_bat"

            P_ch = pulp.LpVariable.dicts(
                f"P_bat_charge[{bat_id}]", T, lowBound=0
            )
            P_dis = pulp.LpVariable.dicts(
                f"P_bat_discharge[{bat_id}]", T, lowBound=0
            )
            SOC = pulp.LpVariable.dicts(
                f"SOC[{bat_id}]", range(len(T)+1)  # SOC indexed 0..T
            )

            self.vars["P_bat_charge"][bat_id] = P_ch
            self.vars["P_bat_discharge"][bat_id] = P_dis
            self.vars["SOC"][bat_id] = SOC

            cap = bat_cfg.capacity_kwh
            min_soc = bat_cfg.min_soc_pct / 100.0 * cap
            max_soc = bat_cfg.max_soc_pct / 100.0 * cap
            reserve = bat_cfg.reserve_soc_pct / 100.0 * cap
            soc_now_pct = self.resolved["battery_soc_pct"][bat_id]
            soc_now_kwh = soc_now_pct / 100.0 * cap

            # Initial SOC
            self.problem += (
                SOC[0] == soc_now_kwh,
                f"soc_init[{bat_id}]",
            )

            # SOC bounds
            for t in range(len(T)+1):
                self.problem += (
                    SOC[t] >= reserve,
                    f"soc_reserve[{bat_id}]_t{t}",
                )
                self.problem += (
                    SOC[t] <= max_soc,
                    f"soc_max[{bat_id}]_t{t}",
                )

            # Dynamics
            eta_ch = 1.0  # or config if you want
            eta_dis = 1.0

            for idx, t in enumerate(T):
                dt = self.horizon.dt_hours(t)
                self.problem += (
                    SOC[idx+1]
                    == SOC[idx]
                       + eta_ch * P_ch[t] * dt
                       - (1 / eta_dis) * P_dis[t] * dt,
                    f"soc_dyn[{bat_id}]_t{t}",
                )

            # Terminal neutral constraint
            terminal_lb = max(reserve, soc_now_kwh)
            last_idx = len(T)
            self.problem += (
                SOC[last_idx] >= terminal_lb,
                f"soc_terminal[{bat_id}]",
            )
```

Then, in inverter DC balance:

```python
    def _finalise_inverter_dc_balance(self):
        T = self.horizon.T
        for inv_cfg in self.plant_cfg.inverters:
            inv_name = inv_cfg.name
            P_inv_dc = self.vars["P_inv_dc_net"][inv_name]
            pv_used = self.vars["P_pv_used"][inv_name]

            # Sum battery flows on this inverter
            bat_id = f"{inv_name}_bat"
            for t in T:
                P_ch_sum = 0
                P_dis_sum = 0
                if bat_id in self.vars["P_bat_charge"]:
                    P_ch_sum = self.vars["P_bat_charge"][bat_id][t]
                    P_dis_sum = self.vars["P_bat_discharge"][bat_id][t]

                total_pv_used = sum(
                    pv_used[j][t] for j in pv_used.keys()
                )

                # DC balance: PV used + bat_discharge - bat_charge = P_inv_dc_net
                self.problem += (
                    total_pv_used + P_dis_sum - P_ch_sum
                    == P_inv_dc[t],
                    f"dc_balance[{inv_name}]_t{t}",
                )
```

### 7.4 Load & EV

Distinguish between:

* **Non-controllable load** (parameter `P_load_total[t]` from resolved timeseries).
* **Controlled EV load** with its own SoC.

EV as example:

```python
    def _build_ev_loads(self):
        T = self.horizon.T
        self.vars["P_ev_charge"] = {}
        self.vars["SOC_ev"] = {}

        for ev_cfg in self.ev_cfgs:
            ev_name = ev_cfg.name
            P_ev = pulp.LpVariable.dicts(
                f"P_ev_charge[{ev_name}]", T,
                lowBound=ev_cfg.min_power_kw, upBound=ev_cfg.max_power_kw
            )
            SOC_ev = pulp.LpVariable.dicts(
                f"SOC_ev[{ev_name}]", range(len(T)+1)
            )

            self.vars["P_ev_charge"][ev_name] = P_ev
            self.vars["SOC_ev"][ev_name] = SOC_ev

            cap = ev_cfg.energy_kwh
            soc_now_pct = self.resolved["ev_soc_pct"][ev_name]
            soc_now_kwh = soc_now_pct / 100.0 * cap

            # Initial SoC
            self.problem += (
                SOC_ev[0] == soc_now_kwh,
                f"ev_soc_init[{ev_name}]",
            )

            for t in range(len(T)+1):
                self.problem += (
                    SOC_ev[t] >= 0,
                    f"ev_soc_min[{ev_name}]_t{t}",
                )
                self.problem += (
                    SOC_ev[t] <= cap,
                    f"ev_soc_max[{ev_name}]_t{t}",
                )

            for idx, t in enumerate(T):
                dt = self.horizon.dt_hours(t)
                self.problem += (
                    SOC_ev[idx+1] == SOC_ev[idx] + P_ev[t] * dt,
                    f"ev_soc_dyn[{ev_name}]_t{t}",
                )

            # AC bus will include P_ev_charge in balance
```

---

## 8. Objective Builder

```python
# ems/model/objective.py
import pulp
from ..horizon.horizon import Horizon


def build_objective(problem: pulp.LpProblem, vars: dict, resolved: dict, horizon: Horizon):
    T = horizon.T
    P_import = vars["P_grid_import"]
    P_export = vars["P_grid_export"]

    price_import = resolved["price_import"]  # [t]
    price_export = resolved["price_export"]  # [t]

    J = 0

    # Grid cost
    for t in T:
        dt = horizon.dt_hours(t)
        J += (P_import[t] * price_import[t]
              - P_export[t] * price_export[t]) * dt

    # Curtailment penalty
    w_curtail = resolved.get("w_curtail", 0.0)
    for inv_name, curt_dict in vars["Curtail_inv"].items():
        for t in T:
            J += w_curtail * curt_dict[t]

    # Battery cycling penalty
    w_cycle = resolved.get("w_cycle", 0.0)
    for bat_id, P_ch in vars.get("P_bat_charge", {}).items():
        P_dis = vars["P_bat_discharge"][bat_id]
        for t in T:
            dt = horizon.dt_hours(t)
            J += w_cycle * (P_ch[t] + P_dis[t]) * dt

    # EV terminal value (simple linear)
    λ_ev = resolved.get("lambda_ev", 0.0)
    for ev_name, SOC_ev in vars.get("SOC_ev", {}).items():
        last_idx = len(T)
        J -= λ_ev * SOC_ev[last_idx]

    problem += J
```

---

## 9. Runtime Orchestration

### 9.1 Solver Loop

```python
# ems/runtime/solver.py
from datetime import datetime
import pulp
from ..config.loader import load_config
from ..horizon.horizon import Horizon
from ..data.timeseries import resolve_all_timeseries
from ..model.builder import MILPBuilder


def run_optimisation_once(config_path: str):
    cfg = load_config(config_path)
    now = datetime.now()  # or injected clock

    horizon = Horizon(
        now=now,
        interval_minutes=cfg.ems.interval_duration,
        num_intervals=cfg.ems.num_intervals,
    )

    resolved_data = resolve_all_timeseries(cfg, horizon)

    builder = MILPBuilder(
        plant_cfg=cfg.plant,
        ev_cfgs=cfg.loads,
        horizon=horizon,
        resolved_data=resolved_data,
    )
    milp = builder.build()

    # Solve
    milp.problem.solve(pulp.PULP_CBC_CMD(msg=False))

    # Extract actions for t=0
    actions = extract_actions_for_t0(milp, cfg, horizon)
    plan = extract_plan_for_visualisation(milp, cfg, horizon)

    return actions, plan
```

### 9.2 Applying actions

`extract_actions_for_t0` should:

* For each inverter:

  * Derive desired **mode** (e.g. SelfUse, ForceCharge, ForceDischarge).
  * Derive **target charge power** if relevant.
* For Tessie:

  * Decide charging power setpoint (or on/off via wall connector).

Then map this to:

* Home Assistant service calls
* Direct inverter/EV APIs if applicable

This mapping is intentionally separate from the MILP; it’s glue code.

---

## 10. Testing Strategy

### 10.1 Unit tests

* `Horizon` time windows & `dt_hours` in edge cases.
* `TimeSeriesHandle` mapping horizon → arrays.
* Localised tests for each builder:

  * Battery SoC dynamics with fixed P_ch, P_dis.
  * PV curtailment gating logic.
  * Grid import forbidden windows.

### 10.2 Scenario tests (no HA)

Create synthetic scenarios:

* **Sunny day, midday price spike**:

  * Expect: battery discharges at spike, recharges later.
* **Negative prices overnight**:

  * Expect: charge battery and EV overnight, discharge later.
* **Zero FiT vs high FiT**:

  * Expect: self-consume bias vs export preference.

### 10.3 Integration tests (with HA stubbed)

* Replace HA calls with in-memory fake sensors.
* Validate full loop:

  * config → resolved_data → MILP → actions.

---

## 11. Invariants to Keep in Mind

* Every inverter:

  * has exactly one DC bus (internally),
  * owns the DC↔AC conversion and `peak_power_kw` constraint.
* All SoC is in **kWh** internally.
* `t=0` is realtime / partial; `t≥1` is aligned forecast.
* Only `t=0` actions are applied in the real world.
* Over a full horizon (24h), the battery’s terminal SoC must be ≥ initial SoC (subject to reserve).

---

If you’d like, I can next:

* Generate a more concrete `resolve_all_timeseries` skeleton tailored to your specific HA entities (Amber + Solcast), or
* Zoom in on the action-mapping layer (e.g. how to turn MILP outputs into inverter “mode” + power setpoints consistent with FoxESS / Sungrow/Home Assistant controls).

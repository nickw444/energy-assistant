# EMS MILP System Design (Implemented)

This document describes the **current implementation** under `src/hass_energy/ems/`.
It is intended to mirror the shipped code (builder, solver, horizon, forecast
alignment, resolver inputs). Keep it in sync with `src/hass_energy/ems/AGENTS.md`.

---

## 1. Scope and status

The EMS package builds and solves a PuLP MILP that produces a **time-stepped plan**
for grid import/export, PV utilization, battery usage, and controllable EV charging.
It does **not** currently apply control actions to devices; it only solves and
emits a plan for inspection/plotting. The plan is used by:

- CLI (`hass-energy ems solve`) for ad-hoc solves + plotting.
- Background worker (`hass_energy/worker/`) for scheduled solves every minute.
- API (`hass_energy/api/routes/plan.py`) for fetching/awaiting plan output.

The EMS implementation here is independent from the MILP v2 scaffolding under
`src/hass_energy/milp_v2/`.

---

## 2. Code map (actual modules)

Core EMS code lives in:

- `src/hass_energy/ems/builder.py`
  - Builds variables, constraints, and objective.
- `src/hass_energy/ems/planner.py`
  - Orchestrates build + solve and extracts a plan.
- `src/hass_energy/ems/horizon.py`
  - Time slotting, timezone resolution, import-forbidden evaluation.
- `src/hass_energy/ems/forecast_alignment.py`
  - Aligns forecast intervals to horizon slots with optional slot-0 overrides.

Supporting runtime pieces:

- `src/hass_energy/lib/source_resolver/`
  - `ValueResolver`, HA sources, and forecast interval models.
- `src/hass_energy/worker/`
  - Background scheduler that runs EMS every minute.
- `src/hass_energy/api/routes/plan.py`
  - Plan run/await endpoints.
- `src/hass_energy/plotting/plan.py`
  - Plotting helpers used by the CLI.

---

## 3. Runtime flow (what actually happens)

### 3.1 CLI solve (`hass-energy ems solve`)

1. `load_app_config()` parses YAML into `AppConfig`.
2. `ValueResolver` is created, config is marked for hydration, and HA data is fetched.
3. `EmsMilpPlanner.generate_ems_plan()`:
   - Resolves forecast inputs via `MILPBuilder.resolve_forecasts(...)`.
   - Builds horizon via `build_horizon()` (interval duration + shortest coverage length).
   - Builds MILP via `MILPBuilder.build(...)` using the resolved forecasts.
   - Solves with CBC (`pulp.PULP_CBC_CMD`).
   - Extracts a plan dictionary.
4. Plan JSON is written to `data_dir/ems_plan.json` by default.
5. `plot_plan()` renders a chart (optional).

### 3.2 Worker + API

- The worker (`hass_energy/worker/service.py`) schedules a solve every minute.
- Each run hydrates HA data, solves the MILP, and stores the latest plan in memory.
- API endpoints allow:
  - Triggering a run (`POST /plan/run`).
  - Fetching latest (`GET /plan/latest`).
  - Waiting for a fresh plan (`GET /plan/await`).

---

## 4. Configuration model (what the solver expects)

The EMS consumes `AppConfig` from `src/hass_energy/models/config.py`:

- `ems`: `EmsConfig`
  - `timestep_minutes` (default slot size)
  - `high_res_timestep_minutes`, `high_res_horizon_minutes` (optional; run a higher-resolution window before switching to the default timestep)
  - `min_horizon_minutes` (minimum forecast horizon; solver uses the shortest forecast length)
  - `timezone` (optional)
- `plant`: `PlantConfig`
  - `grid`: `GridConfig`
  - `load`: `PlantLoadConfig`
  - `inverters`: list[`InverterConfig`]
- `loads`: list[`LoadConfig`] (currently `controlled_ev` and `nonvariable_load`)

### 4.1 Grid (`GridConfig`)

Fields used by EMS:

- `max_import_kw`, `max_export_kw`
- `realtime_price_import`, `realtime_price_export`
- `price_import_forecast`, `price_export_forecast`
- `grid_price_bias_pct` (premium on import, discount on export)
- `import_forbidden_periods` (list of `TimeWindow`)

Note: `realtime_grid_power` exists in config but is **not used** by the EMS solver.

### 4.2 Plant load (`PlantLoadConfig`)

- `realtime_load_power`
- `forecast` (historical average)

Load forecasts are aligned to the horizon via time-weighted overlap, so the
source interval does not have to match `ems.timestep_minutes` (though matching
intervals can reduce unintended smoothing).
(enforced in `MILPBuilder._resolve_load_series`). The plant load should **exclude
controllable loads** (EV charging), which are modeled separately.

### 4.3 Inverters (`InverterConfig`)

Fields:

- `id` (slug-safe identifier used as plan key)
- `name`
- `peak_power_kw`
- `curtailment` (None | "binary" | "load-aware")
- `pv` (forecast + optional realtime)
- `battery` (optional)

### 4.4 Battery (`BatteryConfig`)

Fields:

- `capacity_kwh`
- `storage_efficiency_pct`
- `charge_cost_per_kwh`, `discharge_cost_per_kwh`
- `min_soc_pct`, `max_soc_pct`, `reserve_soc_pct`
- `max_charge_kw`, `max_discharge_kw` (optional)
- `state_of_charge_pct` (realtime)
- `realtime_power` (currently unused by EMS)

### 4.5 Loads (`LoadConfig`)

`controlled_ev` loads support:

- `min_power_kw`, `max_power_kw`, `energy_kwh`
- `connected` (binary sensor)
- `can_connect` (optional binary signal)
- `allowed_connect_times` (list of `TimeWindow`, optional)
- `connect_grace_minutes` (minutes before assuming EV can be connected)
- `realtime_power`, `state_of_charge_pct`
- `soc_incentives` (list of `{target_soc_pct, incentive}`)

`nonvariable_load` exists but is currently a placeholder (no constraints added).

---

## 5. Time handling and horizon

`build_horizon()` (see `src/hass_energy/ems/horizon.py`) creates the planning
slots used by the MILP.

- **Timezone resolution**:
  - If `ems.timezone` is set, it is used.
  - Otherwise uses `now.tzinfo` or system local timezone.
- **Slotting**:
  - Horizon start is floored to the base timestep boundary (high-res if configured).
  - Slots are **fixed-length** intervals of `timestep_minutes` minutes by default, or
    multi-resolution when the high-res interval fields are configured.
  - There is **no partial slot** at `t=0`; the first slot may partially precede `now`.
- **Import forbidden periods**:
  - `import_allowed[t]` is computed in the builder per slot by comparing the slot
    start time against `grid.import_forbidden_periods` and stored on `GridBuild`.
  - Time windows use local time-of-day and can wrap midnight.

Key types:

- `Horizon`: holds `now`, `start`, `slots`, and `T` range.
- `HorizonSlot`: `index`, `start`, `end`, `duration_h`.

---

## 6. Data resolution and forecast alignment

### 6.1 ValueResolver and HA sources

`ValueResolver` (`src/hass_energy/lib/source_resolver/resolver.py`) resolves
`EntitySource` instances by pulling data from `HassDataProvider`.

Supported source types:

- `HomeAssistantEntitySource` (single entity)
- `HomeAssistantMultiEntitySource` (multiple entities)
- `HomeAssistantHistoryEntitySource` (history data)

Forecast sources map HA data into interval lists:

- **Price forecasts**: `HomeAssistantAmberElectricForecastSource` →
  list[`PriceForecastInterval`]
- **PV forecasts**: `HomeAssistantSolcastForecastSource` →
  list[`PowerForecastInterval`]
- **Load forecast**: `HomeAssistantHistoricalAverageForecastSource` →
  list[`PowerForecastInterval`]

### 6.2 Alignment to horizon

`PowerForecastAligner` / `PriceForecastAligner`:

- Convert forecast intervals into a per-slot series.
- Require the forecast to **cover the full horizon**.
- Allow a **missing slot 0** only when `first_slot_override` is provided.
- For each slot, the aligner selects the first interval that overlaps the slot
  (no interpolation or duration weighting).

### 6.3 Realtime overrides

The builder uses:

- Load forecast + realtime load override for **slot 0**.
- PV forecast + realtime PV override for **slot 0** (if realtime exists).
- Price forecast + realtime price override for **slot 0**.

If a forecast is missing and a realtime source exists, the series is filled with
that realtime value for all slots (currently used only when a forecast is
configured as optional, which is rare in EMS configs).

---

## 7. MILP model (variables and constraints)

All MILP construction happens in `MILPBuilder`.

### 7.1 Variables (key sets)

Grid:

- `P_grid_import[t]` (kW)
- `P_grid_export[t]` (kW)
- `P_grid_import_violation_kw[t]` (kW, slack for forbidden imports)
- `Grid_import_on[t]` (binary selector)

Inverters (per inverter):

- `P_pv_kw[inv][t]` (PV output after curtailment)
- `P_inv_ac_net_kw[inv][t]` (net AC flow)
- `Curtail_inv[inv][t]` (binary when curtailment enabled)

Batteries (per inverter):

- `P_batt_charge_kw[inv][t]`
- `P_batt_discharge_kw[inv][t]`
- `E_batt_kwh[inv][t]` (SoC at slot boundaries, indexed 0..N)
- `Batt_charge_mode[inv][t]` (binary; charge vs discharge)

EV loads (per EV):

- `P_ev_charge_kw[ev][t]`
- `E_ev_kwh[ev][t]` (SoC indexed 0..N)
- `Ev_charge_ramp_kw[ev][t]` (absolute ramp magnitude)
- `Ev_charge_anchor_kw[ev]` (absolute deviation from realtime power at t=0)
- `Ev_*_incentive_*` (piecewise incentive segment variables)

### 7.2 Grid constraints

- Import/export exclusivity via `Grid_import_on[t]`:
  - `P_grid_import[t] <= max_import * Grid_import_on[t]`
  - `P_grid_export[t] <= max_export * (1 - Grid_import_on[t])`
- Import forbidden periods:
  - `P_grid_import[t] <= max_import * import_allowed[t] + P_grid_import_violation[t]`
  - `P_grid_import_violation[t]` keeps feasibility and is heavily penalized.

### 7.3 PV curtailment modes

Per inverter, one of:

- **No curtailment** (`curtailment: null`):
  - `P_pv_kw[t] == forecast[t]`

- **Binary curtailment** (`curtailment: "binary"`):
  - `P_pv_kw[t] == forecast[t] * (1 - Curtail_inv[t])`
  - `Curtail_inv[t]` fully shuts PV off when 1.

- **Load-aware curtailment** (`curtailment: "load-aware"`):
  - `P_pv_kw[t] <= forecast[t]`
  - `P_pv_kw[t] >= forecast[t] * (1 - Curtail_inv[t])`
  - `P_grid_export[t] <= max_export * (1 - Curtail_inv[t])`

This makes `Curtail_inv[t] == 1` a signal that export is blocked and PV can be
reduced below forecast.

### 7.4 Inverter net AC flow

- If no battery: `P_inv_ac_net_kw[t] == P_pv_kw[t]`
- With battery: `P_inv_ac_net_kw[t] == P_pv_kw[t] + P_batt_discharge - P_batt_charge`

No explicit DC/AC efficiency is modeled in EMS v3.

### 7.5 Battery constraints

Per inverter battery:

- Charge/discharge limits (`max_charge_kw`, `max_discharge_kw`).
- Binary mode selector prevents simultaneous charge + discharge:
  - `P_charge[t] <= limit * mode[t]`
  - `P_discharge[t] <= limit * (1 - mode[t])`
- SoC bounds:
  - min bound uses `min_soc_pct`.
  - max bound uses `max_soc_pct`.
- Export reserve:
  - Grid export is blocked unless SoC stays above `reserve_soc_pct` for the slot.
- SoC dynamics:
  - `E[t+1] = E[t] + (P_charge * eta - P_discharge / eta) * dt`
  - `eta = storage_efficiency_pct / 100`.
- Terminal constraint:
  - `E[end] >= E[start]` (non-decreasing across horizon).

### 7.6 EV constraints

For `controlled_ev` loads:

- **Connection gating** (per slot):
  - If `connected` is true, all slots are allowed.
  - If `connected` is false and `can_connect` is false, no slots are allowed.
  - Otherwise, slots are allowed only after `connect_grace_minutes` and within
    `allowed_connect_times` windows (if provided).
- **Min power handling**:
  - If `min_power_kw > 0`, a binary `charge_on[t]` enforces either 0 or
    `[min_power_kw, max_power_kw]`.
  - If `min_power_kw == 0`, charging is fully continuous.
- **Ramp penalty variables**:
  - `Ev_charge_ramp_kw[t] >= |P_ev[t] - P_ev[t-1]|` for `t > 0`.
- **Soft realtime anchor**:
  - `Ev_charge_anchor_kw >= |P_ev[0] - realtime_power|`.
- **SoC dynamics** (charge-only):
  - `E_ev[t+1] = E_ev[t] + P_ev[t] * dt`.

### 7.7 EV SoC incentives

The EV terminal SoC is decomposed into piecewise segments:

- Incentive targets must be **non-decreasing**.
- Each segment covers a SoC range and has a per-kWh reward.
- A trailing zero-incentive segment fills remaining capacity.
- Constraint: `sum(segments) == E_ev[terminal]`.

### 7.8 AC power balance

System balance is enforced per slot:

- `P_grid_import + sum(P_inv_ac_net) - P_grid_export == load_kw + controllable_loads`

`load_kw` comes from the plant load forecast, and controllable loads currently
include EV charge power.

---

## 8. Objective function (current terms)

The objective is a sum of:

1. **Energy cost** (per slot):
   - `import_cost - export_revenue`.
   - If export price is exactly zero, a tiny **export bonus** (1e-4) is used
     to prefer export over curtailment.
2. **Forbidden import penalty**:
   - Large penalty (`w_violation = 1e3`) on `P_grid_import_violation_kw`.
3. **Early-flow tie-breaker**:
   - Tiny negative weight on `(P_import + P_export) / (t+1)` to bias flow earlier.
4. **Battery wear cost**:
   - `charge_cost_per_kwh * charge + discharge_cost_per_kwh * discharge`.
5. **Battery timing tie-breaker**:
   - Tiny time-weighted throughput penalty to stabilize dispatch ordering.
6. **Terminal SoC value** (optional):
   - `-soc_value_per_kwh * E_batt[terminal]` rewards stored energy at horizon end.
7. **EV incentive rewards**:
   - Subtract incentive per kWh on terminal SoC segments.
8. **EV ramp penalties**:
   - Penalize `Ev_charge_ramp_kw[t]` for `t > 0`.
9. **EV anchor penalty**:
   - Penalize `Ev_charge_anchor_kw` at slot 0.

---

## 9. MPC anchoring behavior

The EMS uses a standard MPC-style horizon but **anchors** some inputs at slot 0:

- Load, PV, and prices override **slot 0** with realtime values.
- Battery and EV SoC at `t=0` are set from realtime sensors.
- EV charge power uses a **soft** anchor (penalty only) to remain near realtime power.
- Grid realtime power is **not** used by the EMS builder.

Note: Because the horizon start is floored to the interval boundary, slot 0 can
cover time before `now`. The slot-0 override compensates for this in practice.

---

## 10. Plan output format

`EmsMilpPlanner.generate_ems_plan()` returns a plan dict with:

Top-level keys:

- `generated_at` (epoch seconds)
- `status` (solver status string)
- `objective` (float)
- `ev_connected` (map of EV -> bool)
- `ev_realtime_power_kw` (map of EV -> realtime power)
- `battery_capacity_kwh` (map of inverter -> capacity)
- `ev_capacity_kwh` (map of EV -> capacity)
- `slots` (list of per-slot records)

Per-slot keys include:

- Time: `index`, `start`, `end`, `duration_s`
- Grid: `grid_import_kw`, `grid_export_kw`, `grid_import_violation_kw`, `grid_kw`
- Load: `load_kw`, `load_total_kw`
- Prices: `price_import`, `price_export`
- Costs: `segment_cost`, `cumulative_cost`
- PV: `pv_kw`, `pv_available_kw`, `pv_inverters`, `pv_inverters_available`
- Battery: `battery_charge_kw`, `battery_discharge_kw`, `battery_soc_kwh`
- EV: `ev_charge_kw`, `ev_soc_kwh`
- Inverter: `inverter_ac_net_kw`
- Curtailment: `curtail_inverters`, `curtail_any`
- Import policy: `import_allowed`

Note: `pv_available_*` fields currently mirror `pv_*` in EMS v3 (no separate
"available" series is stored).

---

## 11. Plotting and visualization

`plot_plan()` in `src/hass_energy/plotting/plan.py` renders:

- Net grid, PV, inverter net AC, battery net, base load, and EV charge.
- Price and cost panels when available.
- SoC panel (battery + EV), using percent when capacities are available.

---

## 12. Testing

EMS tests live under `tests/hass_energy/ems/`:

- `test_builder.py`: core MILP behavior (prices, curtailment, alignment).
- `test_forecast_alignment.py`: strict horizon coverage and slot-0 override.
- `test_fixture_baselines.py`: fixture replay against summarized `ems_plan.json`
  baselines (set `EMS_SCENARIO` to target a specific bundle).

Fixtures can be recorded via:

- `hass-energy ems record-scenario --name <scenario>`
- `hass-energy ems refresh-baseline --name <name-or-path>`
- `hass-energy ems solve --scenario <name-or-path>`

---

## 13. Known limitations and future work

Current gaps or intentional simplifications:

- No actuation layer (EMS only produces plans).
- No DC/AC efficiency or inverter loss modeling.
- EV discharge is not modeled (charge-only).
- No battery/EV ramp smoothing beyond current tie-breakers.
- No explicit demand charges, peak power penalties, or block tariffs.
- `nonvariable_load` is a placeholder (no constraints).
- Grid realtime power is unused.
- Forecast alignment is stepwise (no interpolation).
- Horizon is fixed-size; no multi-day stitching logic.

## 14. Unimplemented items from the original design

Items that were proposed in the original design but remain unimplemented in the
current EMS stack:

- **Non-variable/deferrable loads**: `nonvariable_load` exists but adds no
  constraints or scheduling logic yet.
- **Action application layer**: there is no mapping of slot-0 decisions into
  Home Assistant service calls or inverter/EV commands.
- **Pre-MPC slot / partial slot handling**: no slot `-1` or partial lead-in is
  modeled; slot 0 is a full interval starting at the floored boundary.
- **Explicit DC bus + AC efficiency modeling**: the model uses a simplified
  AC net equation without inverter/DC efficiency losses.
- **EV departure targets & switching penalties**: no explicit departure-time
  constraints or on/off switching penalty beyond the current soft ramp penalty.
- **Improved curtailment behavior**: no explicit incentive to curtail when
  export price is negative or zero beyond the small export bonus.
- **Output hierarchy**: plan output is a flat per-slot dict; no structured
  plant hierarchy in the plan payload.
- **Cost reporting clarity**: incentives are included in the objective, and
  there's a TODO to ensure they don't appear in “real” cost totals.
- **Forecast conditioning**: no interpolation or blending of realtime load into
  the load forecast beyond slot-0 override.

## 15. EMS TODOs

Tracked TODOs in the EMS package.

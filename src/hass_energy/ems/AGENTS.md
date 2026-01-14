## EMS Overview

This file is the canonical EMS design reference and replaces
`src/hass_energy/ems/EMS_SYSTEM_DESIGN.md`.

The EMS package models energy flows with a PuLP MILP and produces a
time-stepped plan for plotting/inspection. The core code lives in:

- `src/hass_energy/ems/builder.py` (builds the MILP)
- `src/hass_energy/ems/planner.py` (solves and extracts plan output)
- `src/hass_energy/ems/horizon.py` (time horizon and slotting)
- `src/hass_energy/ems/forecast_alignment.py` (forecast alignment)
- `src/hass_energy/ems/models.py` (typed plan output models; `EmsMilpPlanner.generate_ems_plan` returns `EmsPlanOutput`)

## EMS Design (Canonical)

### Data flow
1. `MILPBuilder.resolve_forecasts(...)` resolves forecast series into `ResolvedForecasts`, including the shortest coverage length.
2. `build_horizon(...)` constructs time slots aligned to `EmsConfig.timestep_minutes`, sized to the shortest forecast horizon (bounded by `EmsConfig.min_horizon_minutes`). If `high_res_horizon_minutes` / `high_res_timestep_minutes` are set, slots run at higher resolution before switching back to the default timestep.
3. `MILPBuilder.build(...)` builds the MILP using the resolved forecasts.
4. `EmsMilpPlanner.generate_ems_plan(...)` solves the model (CBC) and extracts a plan.
5. `plot_plan(...)` visualizes series (net grid, PV, battery, prices, costs, SoC).

### Inputs & resolution
- `PlantConfig` + `LoadConfig` + `EmsConfig` define topology; horizon length shrinks to the shortest forecast horizon but must be ≥ `EmsConfig.min_horizon_minutes`.
- `EmsConfig.timestep_minutes` plus optional `high_res_timestep_minutes` and `high_res_horizon_minutes` define multi-resolution horizons (e.g., 30-minute slots by default, with 5-minute slots for the first 2 hours).
  - When switching to a new interval size, the boundary is snapped forward to the next slot boundary for that interval to keep coarse slots aligned to the wall clock.
  - If the remaining horizon length does not divide evenly into the final slot size, the last slot is shortened to fit the forecast coverage.
- Forecasts resolve through `MILPBuilder.resolve_forecasts(...)` into `ResolvedForecasts`
  (defined in `src/hass_energy/ems/models.py`) containing `PowerForecastInterval` and
  `PriceForecastInterval` sequences.
- `ResolvedForecasts` is data-only and flat (`grid_price_import`, `grid_price_export`, `load`, `inverters_pv`); `min_coverage_intervals` is computed during resolution.
- Realtime override values are resolved during `MILPBuilder.build(...)`, not stored in `ResolvedForecasts`.
- Alignment:
  - `PowerForecastAligner` / `PriceForecastAligner` align intervals to the horizon.
  - Alignment is strict: forecasts must fully cover the horizon (no wrapping), with a small tolerance for sub-minute gaps.
  - Slot alignment uses a time-weighted average when horizon slots are longer than forecast intervals.
  - `first_slot_override` replaces the first slot with a realtime value.
- Plant load forecasts/realtime values should exclude controlled loads; controllable loads are added separately in the MILP.
- Historical-average load forecasts support `forecast_horizon_hours` to repeat the daily profile beyond 24h.
- Controlled EV loads can assume future connectivity using `connect_grace_minutes` plus optional `can_connect` and `allowed_connect_times` constraints.
- Controlled EV loads apply a small internal ramp penalty to discourage large per-slot changes in charge power.
- Controlled EV loads include a soft anchor penalty that keeps slot 0 close to realtime charge power; when realtime power is near zero (below 0.1 kW), the anchor penalty is skipped so charging can start immediately.
- Load-aware curtailment is forced on whenever export price is negative so PV can follow load and export is blocked for those slots.

### MPC anchoring behavior
Slot 0 is used as the MPC decision window, but some realtime inputs anchor the
model at the start of the horizon:
- Realtime load, PV, and prices override slot 0 via `first_slot_override` when
  forecasts are available. This constrains exogenous inputs for slot 0 but does
  not directly set decision variables.
- EV charge power has a **soft** slot-0 anchor (penalty on deviation from
  realtime power). When realtime power is near zero (< 0.1 kW), the anchor
  penalty is skipped so slot 0 can start charging without bias.
- Load-aware curtailment is forced on when export prices go negative, making PV output flexible and blocking export in those slots.
- Battery/EV SoC initialize `E_*[0]` using realtime sensors, and EV
  connectivity gates charging. These are feasibility anchors across the horizon.
- Realtime grid power is **not** used by the EMS builder.

### Variables (key decision variables)
- Grid:
  - `P_grid_import[t]`, `P_grid_export[t]`
  - `P_grid_import_violation_kw[t]` for forbidden import periods
- Inverters:
  - `P_pv_kw[inv][t]` (PV output after curtailment)
  - `P_inv_ac_net_kw[inv][t]` (net AC flow per inverter)
  - `Curtail_inv[inv][t]` when curtailment is enabled
- Batteries (per inverter):
  - `P_batt_charge_kw[inv][t]`
  - `P_batt_discharge_kw[inv][t]`
  - `E_batt_kwh[inv][t]` (SoC, slot-boundary indexed)
  - `batt_charge_mode[t]` (binary: charge vs discharge; idle allowed)

### Core constraints
- Grid:
  - Import/export exclusivity via a per-slot binary selector (no simultaneous flow).
  - Import cap with violation slack during forbidden periods.
- PV:
  - No curtailment: `P_pv_kw == forecast`.
  - Binary curtailment: `P_pv_kw == forecast * (1 - Curtail_inv)`.
  - Load-aware curtailment: `P_pv_kw` bounded by forecast with export blocked.
- Inverter net AC:
  - `P_inv_ac_net_kw = P_pv_kw + P_batt_discharge - P_batt_charge`.
- Battery:
  - Charge/discharge limits (optional overrides).
  - Single binary charge/discharge mode selector.
  - SoC bounds from `min_soc_pct` and `max_soc_pct`.
  - Grid export is blocked when SoC is below `reserve_soc_pct` (self-consumption can still discharge to `min_soc_pct`).
  - SoC update uses `storage_efficiency_pct`.
  - Terminal SoC constraint: `E_batt[end] >= E_batt[start]`.
- AC balance (system):
  - `P_grid_import + sum(P_inv_ac_net_kw) - P_grid_export == load + controllable_loads`.

### Objective (current terms)
- Energy cost:
  - `import_cost - export_revenue` (with a tiny export bonus when price = 0).
- Forbidden import violations:
  - Large penalty on `P_grid_import_violation_kw`.
- Battery wear:
  - `throughput_cost_per_kwh` applied to charge + discharge.
- Early-flow tie-breaker:
  - Small time-decay bonus on total grid flow `(P_import + P_export)` favoring earlier slots.

### Outputs & plotting
`planner.py` emits per-slot:
- `grid_import_kw`, `grid_export_kw`, `grid_kw`
- `pv_kw`, `pv_inverters`, `curtail_inverters`
- `battery_charge_kw`, `battery_discharge_kw`, `battery_soc_kwh`
- `ev_charge_kw`, `ev_soc_kwh`
- `inverter_ac_net_kw`
- `price_import`, `price_export`, `segment_cost`, `cumulative_cost`

Top-level output:
- `objective_value` (solver objective value; may be `None` if no value is available)

Plotting (`src/hass_energy/plotting/plan.py`):
- Main panel includes net grid, PV, net battery, inverter net AC, base load, and EV charge.
- Price and cost panels render with hover tooltips.
- SoC panel displays percentages (0–100%+, with headroom if needed) for clarity.

### Testing guidance
- EMS tests live under `tests/hass_energy/ems/`.
- Treat the EMS solver as a black box; test inputs/outputs rather than private helpers.
- Use recorded Home Assistant fixtures for complex scenarios. Record via
  `hass-energy ems record-scenario` (writes `ems_fixture.json`, `ems_config.yaml`,
  and `ems_plan.json` under `tests/fixtures/ems/`; `--name` writes to a subdir).
- Replay fixtures offline via `hass-energy ems solve --scenario <name>` to view
  plots or output JSON without a live Home Assistant connection.
- Snapshot tests use `syrupy` with a summarized plan payload for easy diffs.
  Set `EMS_SCENARIO=<name>` to point tests at a named fixture subdirectory.

### Known gaps / future work
- Controlled EV load modeling is now supported (charge-only with SoC incentives).
- EV departure-time targets and on/off switching penalties are still deferred.
- No smoothing/ramping constraints beyond current tie-breakers.
- Inverter/DC efficiency modeling is intentionally simplified.

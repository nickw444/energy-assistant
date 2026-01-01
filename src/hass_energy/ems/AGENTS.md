## EMS Overview

This file is the canonical EMS design reference and replaces
`src/hass_energy/ems/EMS_SYSTEM_DESIGN.md`.

The EMS package models energy flows with a PuLP MILP and produces a
time-stepped plan for plotting/inspection. The core code lives in:

- `src/hass_energy/ems/builder.py` (builds the MILP)
- `src/hass_energy/ems/solver.py` (solves and extracts plan output)
- `src/hass_energy/ems/horizon.py` (time horizon and slotting)
- `src/hass_energy/ems/forecast_alignment.py` (forecast alignment)

## EMS Design (Canonical)

### Data flow
1. `build_horizon(...)` constructs time slots aligned to `EmsConfig.interval_duration`.
2. `MILPBuilder.build()` resolves forecast series and builds the MILP.
3. `solve_once(...)` solves the model (CBC) and extracts a plan.
4. `plot_plan(...)` visualizes series (net grid, PV, battery, prices, costs, SoC).

### Inputs & resolution
- `PlantConfig` + `LoadConfig` + `EmsConfig` define topology and horizon.
- Forecasts resolve through `ValueResolver` into `PowerForecastInterval` and
  `PriceForecastInterval` sequences.
- Alignment:
  - `PowerForecastAligner` / `PriceForecastAligner` align intervals to the horizon.
  - Alignment is strict: forecasts must fully cover the horizon (no wrapping).
  - `first_slot_override` replaces the first slot with a realtime value.
- Plant load forecasts/realtime values should exclude controlled loads; controllable loads are added separately in the MILP.
- Controlled EV loads can assume future connectivity using `connect_grace_minutes` plus optional `can_connect` and `allowed_connect_times` constraints.
- Controlled EV loads apply a small internal ramp penalty to discourage large per-slot changes in charge power.

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
  - SoC bounds from `min_soc_pct`, `reserve_soc_pct`, `max_soc_pct`.
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
`solver.py` emits per-slot:
- `grid_import_kw`, `grid_export_kw`, `grid_kw`
- `pv_kw`, `pv_inverters`, `curtail_inverters`
- `battery_charge_kw`, `battery_discharge_kw`, `battery_soc_kwh`
- `ev_charge_kw`, `ev_soc_kwh`
- `inverter_ac_net_kw`
- `price_import`, `price_export`, `segment_cost`, `cumulative_cost`

Plotting (`src/hass_energy/plotting/plan.py`):
- Main panel includes net grid, PV, net battery, inverter net AC, base load, and EV charge.
- Price and cost panels render with hover tooltips.
- SoC panel displays percentages (0–100%+, with headroom if needed) for clarity.

### Testing guidance
- EMS tests live under `tests/hass_energy/ems/`.
- Treat the EMS solver as a black box; test inputs/outputs rather than private helpers.
- Use recorded Home Assistant fixtures for complex scenarios. Record via
  `hass-energy ems record-fixture --name <scenario>` and pair with a matching
  config at `tests/fixtures/ems/ems_config.yaml` (or update the test path).
- Snapshot tests use `syrupy` with a summarized plan payload for easy diffs.

### Known gaps / future work
- Controlled EV load modeling is now supported (charge-only with SoC incentives).
- EV departure-time targets and on/off switching penalties are still deferred.
- No smoothing/ramping constraints beyond current tie-breakers.
- Inverter/DC efficiency modeling is intentionally simplified.

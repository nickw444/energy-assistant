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
- Controlled EV loads charge at any rate within min/max bounds while connected; optional switch penalties can discourage on/off flapping.
- Negative export prices are handled by the objective; export remains feasible and the solver decides whether to export or curtail.

### MPC anchoring behavior
Slot 0 is used as the MPC decision window, but some realtime inputs anchor the
model at the start of the horizon:
- Realtime load, PV, and prices override slot 0 via `first_slot_override` when
  forecasts are available. This constrains exogenous inputs for slot 0 but does
  not directly set decision variables.
- Grid export remains feasible when export prices are negative; curtailment is left as a solver decision.
- Battery/EV SoC initialize `E_*[0]` using realtime sensors, and EV
  connectivity gates charging. These are feasibility anchors across the horizon.
- EV switch penalties (when enabled) use realtime charger state to seed the
  t0 switch indicator without introducing a t-1 decision slot.
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
  - Terminal SoC constraint: hard by default; when `ems.terminal_soc.mode` is `soft` or
    `adaptive` (and the horizon is shorter than `terminal_soc.short_horizon_minutes`),
    the target SoC is relaxed toward the reserve level and enforced with a slack
    variable plus per-kWh penalty.
- AC balance (system):
  - `P_grid_import + sum(P_inv_ac_net_kw) - P_grid_export == load + controllable_loads`.

### Objective (current terms)
- Energy cost:
  - `import_cost - export_revenue` (with a tiny export bonus when price = 0).
- Grid price bias (`plant.grid.grid_price_bias_pct`) adds a premium to import prices and discount to export revenue, making grid interaction less attractive.
- Forbidden import violations:
  - Large penalty on `P_grid_import_violation_kw`.
- Battery wear:
  - `discharge_cost_per_kwh` applied to discharge, `charge_cost_per_kwh` applied to charge.
  - Both default to 0.0; set `charge_cost_per_kwh: 0.0` to capture PV energy freely.
  - Efficiency losses are already in the SoC dynamics constraints.
- Battery timing tie-breaker:
  - Tiny time-weighted throughput penalty to stabilize dispatch ordering across
    equivalent-cost slots.
- Terminal SoC shortfall penalty:
  - Applied when the terminal constraint is softened; default penalty uses the average
    import price (unless `ems.terminal_soc.penalty_per_kwh` is set) and scales with
    the horizon ratio vs `terminal_soc.short_horizon_minutes`.
- EV SoC incentives:
  - Piecewise per-kWh rewards for energy charged above the current SoC, based on the configured target bands.
- EV switch penalty:
  - Optional per-switch cost to discourage rapid on/off cycling (not time-weighted).
  - The t0 switch indicator compares `charge_on[0]` to the realtime charger state.
- Incentives are scaled by `(1 - grid_price_bias)` so they compete fairly with export tariffs (an 8c incentive ties with an 8c export tariff).
- Early-flow tie-breaker:
  - Small time-decay bonus on total grid flow `(P_import + P_export)` favoring earlier slots.
- Terminal SoC value:
  - Per-kWh reward for stored battery energy at horizon end.
  - Configurable via `plant.inverters[].battery.soc_value_per_kwh` (default: disabled).
  - Incentivizes higher battery charging when export prices are low but positive.
  - Also makes charging preferred over curtailment when battery headroom exists.

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
- Fixtures use a hierarchical structure: `tests/fixtures/ems/<fixture>/<scenario>/`
  - `<fixture>/ems_config.yaml` — shared config for all scenarios in that fixture
  - `<fixture>/<scenario>/ems_fixture.json` — captured realtime data
  - `<fixture>/<scenario>/ems_plan.json` — summarized baseline
  - `<fixture>/<scenario>/ems_plan.hash` — hash for change detection
  - `<fixture>/<scenario>/ems_plan.jpeg` — plot image
- Record new scenarios via `hass-energy ems record-scenario --fixture <fixture> --name <scenario>`.
  The config is only written if it doesn't already exist (shared across scenarios).
- Replay fixtures offline via `hass-energy ems solve --fixture <fixture> --scenario <scenario>`
  to view plots or output JSON without a live Home Assistant connection.
- Build a multi-scenario visual report with `hass-energy ems scenario-report` to render
  every fixture into a single HTML page. Use `--fixture <fixture>` to filter to one fixture.
- When making EMS changes, validate against a checked-in fixture by replaying it
  and comparing the generated plan summary to the stored `ems_plan.json` for the
  same scenario. This is the preferred offline sanity check before updating snapshots.
  Example workflow:
  - `hass-energy ems solve --fixture nwhass --scenario short-horizon --output /tmp/ems_plan.actual.json`
  - Use `/tmp/ems_plan.actual.json` for deep debugging; the checked-in baseline is summarized for diffs.
- Refresh baselines with `hass-energy ems refresh-baseline`:
  - Omit `--fixture` and `--scenario` to refresh all fixtures and scenarios.
  - Use `--fixture <fixture>` to refresh all scenarios in one fixture.
  - Use `--fixture <fixture> --scenario <scenario>` for a specific scenario.
- The `ems_plan.jpeg` image is checked in for PR review; it regenerates only when
  the plan hash changes. The hash file (`ems_plan.hash`) stores a SHA256 prefix
  of the plan summary (excluding `generated_at`) for stable change detection.
- Fixture baseline tests compare the generated plan summary against the stored
  `ems_plan.json` for each bundle. Set `EMS_SCENARIO=<fixture>/<scenario>` to target a specific scenario.

### Known gaps / future work
- Controlled EV load modeling is now supported (charge-only with SoC incentives).
- EV departure-time targets and on/off switching penalties are still deferred.
- No smoothing/ramping constraints beyond current tie-breakers.
- Inverter/DC efficiency modeling is intentionally simplified.

````markdown
# EMS Objective Simplification & Preference-Budget Plan

Goal: simplify and de-conflict the MILP objective for batteries, curtailment, and EVs while preserving real economic optimisation and giving the user *bounded* control over non-economic “preference” behaviour.

This document is written against the current codebase described in `hass-energy.context.md`. :contentReference[oaicite:0]{index=0}

---

## 1. High-level design (Option A)

### 1.1 Concepts

1. **Single economic layer + bounded preferences**
   - Core objective remains **real money**:
     - Grid import/export cost. :contentReference[oaicite:1]{index=1}  
     - EV SoC incentives (treated as $/kWh willingness to pay). :contentReference[oaicite:2]{index=2}
   - Battery wear and PV curtailment are treated as **preferences expressed in $/kWh**, but they are:
     - Symmetric for charge/discharge.
     - **Globally capped** by `ems.max_profit_sacrifice_per_day` so they can never worsen the bill beyond a user-chosen budget.

2. **Negative export price control**
   - New config knob on the grid:
     - `allow_negative_export: bool`
   - If `False`, the model is **forbidden** from exporting in any slot with `price_export < 0`, avoiding the “PV export at negative FiT because curtailment cost is higher” failure mode.
   - If `True`, the user explicitly allows negative-priced export; the model treats it as a real economic decision (e.g. still exporting if it has no better alternative).

3. **Battery wear**
   - Modelled as **symmetric throughput wear**: one $/kWh applied to `(charge + discharge)` instead of separate charge/discharge knobs.
   - Interpreted as: “I’m willing to sacrifice up to `X` $/day of profit to reduce battery cycling by Y $/kWh.”

4. **Curtailment**
   - Still modelled as $/kWh of curtailed PV (wasted energy), but:
     - Conceptually a **preference weight**, not a true tariff.
     - Scaled under the same budget as battery wear.

5. **EV SoC incentives**
   - Keep existing piecewise incentives as **purely economic value**. They can and should compete directly with export price. :contentReference[oaicite:3]{index=3}  
   - EV charging arbitrage is constrained by:
     - Export/import price spread.
     - Battery wear preference cost (bounded by budget).
     - User’s chosen incentive schedule.

---

## 2. Config/schema changes

All config models live under `src/hass_energy/models/config.py` and related modules as per AGENTS. :contentReference[oaicite:4]{index=4}

### 2.1 Grid config: `allow_negative_export`

**Where**

- `GridConfig` in `src/hass_energy/models/config.py` (already holds import/export constraints and forecast entities).

**Changes**

```python
class GridConfig(BaseModel):
    # existing fields …
    max_import_kw: float
    max_export_kw: float
    # …
    # NEW
    allow_negative_export: bool
````

**Behaviour**

* No default; must be specified in YAML (hard fail if omitted).
* For unreleased fixtures/configs, update all `ems_config.yaml` under `tests/fixtures/ems/**` to include this field.

**Usage in planner**

* In `EmsMilpPlanner` / `MILPBuilder` (see objective snippet in context). 

* When building `export_price_eff`:

  ```python
  allow_negative = self._plant.grid.allow_negative_export
  export_bonus = 1e-4

  export_price_eff = []
  for t in horizon.T:
      raw = float(price_export[t])
      if not allow_negative and raw < 0.0:
          eff = 0.0                      # export allowed only if price >= 0
      elif abs(raw) <= 1e-9:
          eff = export_bonus             # prefer export over curtailment when exactly zero
      else:
          eff = raw
      export_price_eff.append(eff)
  ```

* Grid constraints remain unchanged; export is still allowed by power limits, but negative-price export is economically neutral (revenue = 0) if forbidden by this flag.

---

### 2.2 EMS config: `max_profit_sacrifice_per_day`

**Where**

* `EmsConfig` in `src/hass_energy/models/config.py`. 

**New field**

```python
class EmsConfig(BaseModel):
    # existing fields
    timestep_minutes: int
    min_horizon_minutes: int
    # terminal_soc, etc.

    # NEW: daily cap on extra cost from preferences (battery wear + curtailment)
    max_profit_sacrifice_per_day: float
```

* Units: **AUD/day** (or your base currency per day).
* Semantics: “Across this MILP horizon, non-tariff terms can worsen the nominal bill by at most this amount.”

**Validation**

* `max_profit_sacrifice_per_day >= 0`.
* Hard fail (Pydantic) if missing:

  * Remove any default.
  * Update all `tests/fixtures/ems/*/ems_config.yaml` accordingly.

---

### 2.3 Battery wear config (discriminated union)

Current objective uses: 

* `charge_cost_per_kwh`
* `discharge_cost_per_kwh`
* `export_penalty_per_kwh`

We replace this with a **discriminated union**:

**Where**

* `BatteryConfig` in `src/hass_energy/models/config.py`.

**New types**

```python
from typing import Literal, Union
from pydantic import BaseModel, Field

class BatteryWearNone(BaseModel):
    mode: Literal["none"]

class BatteryWearSymmetric(BaseModel):
    mode: Literal["symmetric"]
    cost_per_kwh: float  # >= 0, AUD per kWh throughput

BatteryWearConfig = Field(discriminator="mode")(
    Union[BatteryWearNone, BatteryWearSymmetric]
)
```

Then in `BatteryConfig`:

```python
class BatteryConfig(BaseModel):
    # existing fields: capacity, min_soc_pct, max_soc_pct, reserve_soc_pct, etc.
    # REMOVE legacy knobs:
    # charge_cost_per_kwh: float = 0.0
    # discharge_cost_per_kwh: float = 0.0
    # export_penalty_per_kwh: float = 0.0

    wear: BatteryWearConfig  # no default
```

**Validation**

* `BatteryWearSymmetric.cost_per_kwh >= 0`.
* Pydantic `extra="forbid"` on `BatteryConfig` will make any old configs with `charge_cost_per_kwh` etc fail hard, as requested.

**Migration in repo**

* Update fixture configs and any example YAMLs (`README.md`, `AGENTS.md`) to use:

  ```yaml
  battery:
    # …
    wear:
      mode: symmetric
      cost_per_kwh: 0.03
  ```

  or

  ```yaml
  battery:
    # …
    wear:
      mode: none
  ```

---

### 2.4 PV curtailment config semantics

Config already has per-inverter `curtailment_cost_per_kwh` used in the objective. 

* We **keep the field name** but reinterpret it as:

  * “Relative preference cost per kWh curtailed” (not assumed to be ≥ tariff).
* No schema change required, but docs and comments in `AGENTS.md` and `planner.py` need updating (see section 5).

---

## 3. Planner / MILP objective changes

All objective construction happens in `MILPBuilder` as described in `AGENTS.md`. 
The relevant code is in `src/hass_energy/ems/planner.py` (method that builds `objective`). 

### 3.1 Compute horizon-scaled preference budget

In `EmsMilpPlanner` (or inside `MILPBuilder.build_objective`), after building the plant and horizon:

```python
ems_cfg = self._app_config.ems
budget_per_day = float(ems_cfg.max_profit_sacrifice_per_day)

# Convert horizon to “day equivalents”
horizon_days = horizon.total_minutes / (60.0 * 24.0)
pref_budget = budget_per_day * horizon_days
```

If `pref_budget <= 0`, we can disable preference scaling (see below).

### 3.2 Remove legacy battery wear & export penalty

Replace the existing block:

```python
# Battery wear costs applied separately to discharge and charge...
for inverter in self._plant.inverters:
    battery = inverter.battery
    ...
    discharge_cost = battery.discharge_cost_per_kwh
    charge_cost = battery.charge_cost_per_kwh
    ...
    export_penalty = battery.export_penalty_per_kwh
    batt_export_series = inverters.battery_export_kw.get(inverter.id)
    ...
```

with our new symmetric wear + no export penalty:

```python
total_pref_kwh_upper_bound = 0.0

for inverter in self._plant.inverters:
    battery = inverter.battery
    if battery is None:
        continue
    inv_vars = inverter_by_id.get(inverter.id)
    if inv_vars is None:
        continue

    charge_series = inv_vars.P_batt_charge_kw
    discharge_series = inv_vars.P_batt_discharge_kw
    if charge_series is None or discharge_series is None:
        continue

    # Upper bound of throughput for scaling preferences later.
    for t in horizon.T:
        max_throughput_kw = (
            float(battery.max_charge_kw or 0.0)
            + float(battery.max_discharge_kw or 0.0)
        )
        total_pref_kwh_upper_bound += max_throughput_kw * horizon.dt_hours(t)

    # Keep tiny time-weighted tie-breaker as-is.
    w_batt_time = 1e-6
    objective += pulp.lpSum(
        w_batt_time
        * (charge_series[t] + discharge_series[t])
        * (t + 1)
        * horizon.dt_hours(t)
        for t in horizon.T
    )
```

Notes:

* We **remove** `battery.export_penalty_per_kwh` entirely.
* We no longer apply separate charge/discharge costs here; that will be done in the **preference-weighted** block below using the new `wear` config.

### 3.3 Bound preference cost (battery wear + curtailment)

After scanning batteries and inverters, also accumulate an upper bound on curtailment energy:

```python
for idx, inverter in enumerate(self._plant.inverters):
    inv_vars = inverter_by_id.get(inverter.id)
    if inv_vars is None or inv_vars.P_curtail_kw is None:
        continue

    curtail_series = inv_vars.P_curtail_kw
    for t in horizon.T:
        # Max possible curtail is forecast PV (or inverter limit).
        max_curtail_kw = float(inverter.pv_max_kw or 0.0)
        total_pref_kwh_upper_bound += max_curtail_kw * horizon.dt_hours(t)
```

> This is deliberately loose; we only need an upper bound to cap the worst-case preference cost.

Then compute a **scale factor**:

```python
if pref_budget <= 0.0 or total_pref_kwh_upper_bound <= 0.0:
    pref_scale = 0.0
else:
    # Ensure sum(preference_cost_per_kwh * kwh) <= pref_budget
    # when all preference flows are maxed out.
    pref_scale = pref_budget / total_pref_kwh_upper_bound
```

Now apply wear & curtailment costs using this scale:

```python
for inverter in self._plant.inverters:
    battery = inverter.battery
    inv_vars = inverter_by_id.get(inverter.id)
    if inv_vars is None:
        continue

    # Battery wear (symmetric throughput).
    if battery is not None:
        wear_cfg = battery.wear
        if isinstance(wear_cfg, BatteryWearSymmetric) and pref_scale > 0.0:
            wear_cost = wear_cfg.cost_per_kwh * pref_scale
            charge_series = inv_vars.P_batt_charge_kw
            discharge_series = inv_vars.P_batt_discharge_kw
            if charge_series is not None and discharge_series is not None:
                objective += pulp.lpSum(
                    wear_cost
                    * (charge_series[t] + discharge_series[t])
                    * horizon.dt_hours(t)
                    for t in horizon.T
                )

    # PV curtailment preference
    curtail_series = getattr(inv_vars, "P_curtail_kw", None)
    if curtail_series is not None and pref_scale > 0.0:
        curtail_cost_cfg = inverter.curtailment_cost_per_kwh
        if curtail_cost_cfg > 0.0:
            w_curtail_tie = 1e-6
            total = len(self._plant.inverters)
            idx = self._plant.inverters.index(inverter)
            tie_weight = w_curtail_tie * (total - idx)
            effective_cost = (curtail_cost_cfg * pref_scale) + tie_weight
            objective += pulp.lpSum(
                effective_cost * curtail_series[t] * horizon.dt_hours(t)
                for t in horizon.T
            )
```

Properties:

* If `max_profit_sacrifice_per_day == 0`, then `pref_scale == 0` and:

  * Battery wear & curtailment costs are effectively disabled (aside from the tiny tie-breaker).
* For any horizon, worst-case extra cost from these terms is ≤ `max_profit_sacrifice_per_day * horizon_days`.

This avoids the conflict where a high curtailment penalty can drive the objective to accept real negative-FiT export or dumb arbitrage: the “damage” is capped.

### 3.4 EV incentives unchanged (already economic)

Objective terms for EV incentives and smoothing remain as they are now: 

```python
# EV terminal SoC incentives (piecewise per-kWh rewards).
for segments in loads.ev_incentive_segments.values():
    for segment_var, incentive in segments:
        if abs(float(incentive)) <= 1e-12:
            continue
        objective += -float(incentive) * segment_var
```

* These are real `$` incentives and **not** scaled by `pref_scale`. They directly compete with export/import prices.
* The existing builder `ControlledEvLoad.soc_incentives` stays as your piecewise schedule (40/60/80%, 0.12/0.08/0.02, etc.), and the monotonicity check stays in `_build_ev_soc_incentives`. 

### 3.5 Terminal SoC penalty (battery)

The terminal SoC shortfall penalty already uses a price-aware economic value based on import price. 

* No structural change required; just **ensure** we:

  * Keep it in the economic layer (no scaling with `pref_scale`).
  * Consider tuning default penalty if needed after testing (but that’s a separate concern).

---

## 4. Grid export with negative FiT

With `allow_negative_export` integrated into `export_price_eff` (section 3.1):

* If `allow_negative_export: false`:

  * Export in negative price slots yields **no revenue** and therefore is never attractive unless forced by feasibility (but curtailment is always preferable because it carries at most the bounded preference cost).
* If `allow_negative_export: true`:

  * Export is allowed even when price is negative; this is explicitly chosen behaviour and subject to actual tariffs.

Curtailment constraints in `MILPBuilder` remain as is. 

---

## 5. Documentation updates

Update `src/hass_energy/ems/AGENTS.md` to reflect the new semantics. 

### 5.1 Objective section

Rewrite the “Objective (current terms)” bullet list to:

* Energy cost:

  * `import_cost - export_revenue` (with tiny export bonus when price = 0).
  * `allow_negative_export` controls whether negative prices are honoured or clamped to 0.
* Forbidden import violations:

  * Large penalty on `P_grid_import_violation_kw`.
* Battery wear (preference-bounded):

  * Symmetric wear cost applied to `(charge + discharge)` when `battery.wear.mode == "symmetric"`.
  * Scaled so the total additional bill impact across the horizon never exceeds `ems.max_profit_sacrifice_per_day * horizon_days`.
* PV curtailment (preference-bounded):

  * `curtailment_cost_per_kwh` per inverter, scaled by the same budget.
  * Tiny tie-breaker by inverter index for stable solutions.
* Terminal SoC shortfall penalty:

  * Price-aware per-kWh penalty for soft terminal constraints (unchanged).
* EV SoC incentives:

  * Piecewise per-kWh *economic* rewards, unbounded except by price and horizon.
* Early-flow, battery timing, EV ramp/anchor terms:

  * Describe them as **small stability tie-breakers**, not user-tunable economic knobs.

### 5.2 Config docs

Update config examples and comments wherever these fields appear:

* Remove mentions of:

  * `charge_cost_per_kwh`
  * `discharge_cost_per_kwh`
  * `export_penalty_per_kwh`
* Add:

  * `battery.wear.mode` and `battery.wear.cost_per_kwh`.
  * `grid.allow_negative_export`.
  * `ems.max_profit_sacrifice_per_day` with an explanation:

    * “Upper bound (per day) on how much grid bill the solver may sacrifice to satisfy battery wear and PV curtailment preferences.”

---

## 6. Testing & fixtures

### 6.1 Unit tests

Add tests under `tests/hass_energy/ems/`:

1. **Config schema**

   * `BatteryWearConfig` discriminated union:

     * `mode="none"` yields no wear cost term.
     * `mode="symmetric"` requires `cost_per_kwh >= 0`.
   * `EmsConfig.max_profit_sacrifice_per_day`:

     * Must be present and ≥ 0.
     * Confirm that omitting it causes validation failure.

2. **Preference budget scaling**

   * Build a small synthetic horizon with:

     * 1 inverter, 1 battery, 2 slots.
     * Known `max_charge_kw`, `max_discharge_kw`, `curtailment_cost_per_kwh`, `battery.wear.cost_per_kwh`, and `max_profit_sacrifice_per_day`.
   * Construct `MILPBuilder` and inspect the objective coefficients:

     * Sum of coefficients for wear + curtailment * maximum kWh ≤ `pref_budget`.

3. **Negative export control**

   * Scenario with negative export price:

     * `allow_negative_export: false`: verify that optimal solution never exports at negative price; PV is curtailed or used locally.
     * `allow_negative_export: true`: verify that solution may export when economically justified.

4. **EV incentive competition**

   * Synthetic scenario where:

     * Export price is constant (e.g. $0.12).
     * EV incentives schedule uses values just above/below that price.
   * Confirm that:

     * When incentive > price, model prefers EV SoC.
     * When incentive < price, model prefers export.

### 6.2 Fixture baselines

* Update `tests/fixtures/ems/**/ems_config.yaml`:

  * Add `allow_negative_export` and `max_profit_sacrifice_per_day`.
  * Add `battery.wear` block for each configured battery.

* Run:

  ```bash
  uv run hass-energy ems refresh-baseline --solver-msg
  ```

* Look at:

  * Plan images.
  * `ems_plan.json` and HTML plots.

* Specifically verify on fixtures with negative FiT periods and significant PV/battery:

  * No unwanted export at negative FiT when `allow_negative_export: false`.
  * Reasonable battery cycling consistent with new wear cost semantics.

---

## 7. Roll-out notes

* This is unreleased software; we can **break schemas** and cleanly migrate the repo:

  * Delete old config fields (do not deprecate).
  * Fix all fixture configs in one go.
* After implementation, add a short “tuning guide” section to AGENTS that explains:

  * Choose `max_profit_sacrifice_per_day` first (e.g. `$0.50/day`).
  * Set `battery.wear.cost_per_kwh` to reflect your intuition about wear (e.g. 3–5c/kWh).
  * Set `curtailment_cost_per_kwh` to describe how much you want to avoid wasting PV relative to battery wear, knowing both are bounded by the same budget.
  * Use EV `soc_incentives` as *real* “value per kWh in the EV” curves.

This should give you a coherent, conflict-reduced objective where:

* True economics (tariffs, EV value) dominate.
* Wear/curtailment are preferences capped by a clear daily budget.
* Negative FiT export cannot accidentally happen unless deliberately allowed in config.

```
```

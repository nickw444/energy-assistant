Here’s a plan you can hand straight to a coding agent.

I’ll describe **what** to change, **where conceptually** it lives (config, price pipeline, optimiser), and **what the end-state behaviour should be**, without assuming any particular repo structure.

---

## 0. Overall goal for the EMS

**End-state behaviour:**

* The EMS still minimises a single scalar objective (“effective cost”), but:

  * Distinguishes **real prices** (tariffs, FiT) from **effective prices** (after user preferences).
  * Has explicit, orthogonal knobs for:

    * *Import aversion / self-sufficiency*.
    * *Export reluctance (self-suff)*.
    * *Battery export anti-arbitrage margin*.
  * Keeps **curtailment decisions** based on **real prices only**.
* All “value” signals (tariffs, wear, premiums, incentives) are in the same units: **¢ per kWh** (or consistent currency unit).

Everything below is about getting to that end state.

---

## 1. Config-layer changes

### 1.1. Introduce new config fields

In the EMS config model (whatever form it takes: YAML, JSON, Pydantic, etc.), add the following high-level parameters:

**Grid preferences:**

* `grid.import_premium_c_per_kwh: float`

  * Extra “effective” cost per kWh of grid import to represent self-sufficiency preference.
* `grid.export_premium_c_per_kwh: float`

  * Extra “effective” reduction of export value per kWh to represent reluctance to export (self-suff pref).

**Battery export behaviour:**

* `battery.wear_cost_c_per_kwh: float`

  * Symmetric per-kWh wear cost, applied to both charge and discharge throughput.
* `battery.export_margin_c_per_kwh: float`

  * Extra margin required (on top of wear) before exporting energy from the battery is considered “worth it”.

**Curtailment thresholds (real price based):**

* `grid.min_export_price_c_per_kwh: float`

  * *Constraint-level* threshold: below this real FiT, export may be disabled altogether.
* (Optional but recommended) `grid.curtailment_min_export_price_c_per_kwh: float`

  * Threshold used to decide when curtailment should be allowed/encouraged. This is based only on the **real** export price.

### 1.2. Deprecate or remove legacy fields

If there are older, less clear fields, mark them for removal and/or migration, e.g.:

* `export_penalty_per_kwh` → **remove** in favour of:

  * `grid.export_premium_c_per_kwh` (general reluctance to export) and
  * `battery.export_margin_c_per_kwh` (battery-specific anti-arbitrage).
* Separate `battery.charge_wear_cost_c_per_kwh` and `battery.discharge_wear_cost_c_per_kwh` → **replace** with

  * unified `battery.wear_cost_c_per_kwh`.

Implementation steps:

1. Extend the config schema/types to include the new fields with sensible defaults (e.g. 0.0 for all premiums/margins).
2. Mark legacy fields as deprecated in comments/docs and, if relevant, in validation warnings.
3. If there is config-migration logic, add a migration path that:

   * Converts `export_penalty_per_kwh` into `grid.export_premium_c_per_kwh` or `battery.export_margin_c_per_kwh` as appropriate, or sets them to defaults and logs a warning.

---

## 2. Introduce “real vs effective” price concepts in the data model

Create a clear distinction in the **internal model state** between:

* **Real prices** (direct from tariffs / APIs / forecasts):

  * `price_import_c_per_kwh[t]`
  * `price_export_c_per_kwh[t]`

* **Effective prices** (used only in the objective, not in constraints like curtailment):

  * `eff_price_import_c_per_kwh[t]`
  * `eff_price_export_c_per_kwh[t]`

Implementation steps:

1. In the data structure passed into the optimiser (the “scenario” or “planning horizon” state), ensure there’s a place to store both:

   * Real prices per time step.
   * Effective prices per time step.

2. In the **price pre-processing layer** (whatever generates tariff time series), add a transformation step:

   * For each time step `t`:

     * `eff_price_import_c_per_kwh[t] = price_import_c_per_kwh[t] + grid.import_premium_c_per_kwh`
     * Start with `eff_price_export_c_per_kwh[t] = price_export_c_per_kwh[t] - grid.export_premium_c_per_kwh`
     * The battery-specific export margin will be handled inside the optimiser when modelling battery-driven exports (see §4).

3. Ensure all downstream code that builds the **objective** uses `eff_price_*` where appropriate, and **not** raw `price_*`.

---

## 3. Keep curtailment and export-allowed decisions based on real prices

Ensure curtailment and “export allowed” rules use **real FiT**, not effective prices.

Implementation steps:

1. In the logic that determines whether exporting is allowed or curtailed at time `t`:

   * Use `price_export_c_per_kwh[t]` and `grid.min_export_price_c_per_kwh`.

   For example, conceptually:

   ```pseudo
   export_allowed[t] = (price_export_c_per_kwh[t] >= grid.min_export_price_c_per_kwh)
   ```

2. If you have explicit curtailment variables (e.g. `curtailment[t]` representing spilled PV), and rules about when curtailment is allowed or encouraged:

   * Base any thresholds on `price_export_c_per_kwh[t]` and `grid.curtailment_min_export_price_c_per_kwh`.
   * Do **not** use `eff_price_export_c_per_kwh[t]` for these structural decisions.

3. In the constraint builder for the LP:

   * Use `export_allowed[t]` (a boolean/int 0/1) to gate the maximum export variable (`g_exp[t]`), e.g.

     ```math
     g_exp[t] <= export_allowed[t] * max_export_capacity
     ```

   * This keeps the “physics & regulatory rules” separate from preference-weighted economics.

End-state rule:

> **All structural constraints (curtailment rules, export allowed/disallowed, max flows) use only real prices and physical limits. Effective prices are only for ranking decisions in the objective.**

---

## 4. Update the LP formulation to include premiums and margins

This is the core change in the optimiser / LP builder.

### 4.1. Decision variables (conceptual)

Assume the optimiser has at least the following variables per time step:

* `g_imp[t]`  — grid import (kWh)
* `g_exp[t]`  — grid export (kWh)
* `b_ch[t]`   — battery charge energy (kWh)
* `b_dis[t]`  — battery discharge energy (kWh)
* `ev_ch[t]`  — EV charging energy (kWh)
* OPTIONAL: `b_exp[t]` — portion of `g_exp[t]` that specifically comes from the battery

If there’s currently no explicit separation for `b_exp[t]`, part of this task is to introduce it or an equivalent proxy so that battery export margin can be applied only to exported battery energy, not PV export.

### 4.2. Extend / refactor the objective

Refactor the objective to the following conceptual structure:

For each time step `t`, the contribution to the objective should be:

1. **Grid import cost with import premium:**

   ```math
   (eff_price_import_c_per_kwh[t]) * g_imp[t]
   ```

   where:

   ```math
   eff_price_import_c_per_kwh[t] = price_import_c_per_kwh[t]
                                 + grid.import_premium_c_per_kwh
   ```

2. **Grid export revenue with export premium and battery export margin:**

   Conceptual form:

   ```math
   - eff_price_export_c_per_kwh[t] * g_exp[t]
   - battery.export_margin_c_per_kwh * b_exp[t]
   ```

   where:

   ```math
   eff_price_export_c_per_kwh[t] = price_export_c_per_kwh[t]
                                 - grid.export_premium_c_per_kwh
   ```

   Notes:

   * `grid.export_premium_c_per_kwh` reduces the value of any export (self-suff bias).
   * `battery.export_margin_c_per_kwh` is applied only to the exported energy known to come from the battery (`b_exp[t]`).

3. **Battery wear cost:**

   ```math
   + battery.wear_cost_c_per_kwh * (b_ch[t] + b_dis[t])
   ```

4. **EV incentives:**

   * If the system already has piecewise incentives (e.g. configured as SoC-dependent rewards per kWh), ensure they remain **additive** in cents per kWh:

     ```math
     + cost_ev_incentive_c_per_kwh[t] * ev_ch[t]
     ```

     where `cost_ev_incentive_c_per_kwh[t]` is usually **negative** for “reward”.

5. **Other penalties/rewards (comfort, flexible loads, etc.)**:

   * Retain any existing terms, but ensure they are also in the same **currency unit** and just sum them into the objective.

Implementation steps in code:

1. Identify the current objective assembly function/module in the optimiser layer.
2. Replace any direct uses of `price_import` / `price_export` with `eff_price_import` / `eff_price_export` where they appear in the objective.
3. Add additional terms for:

   * `battery.wear_cost_c_per_kwh * (b_ch[t] + b_dis[t])`
   * `battery.export_margin_c_per_kwh * b_exp[t]` (only if `b_exp` exists; see below).
4. Ensure all coefficients are dimensionally consistent (¢ per kWh) and avoid double-counting existing penalties like `export_penalty_per_kwh`.

### 4.3. Modelling battery-only export margin (`b_exp[t]`)

If the current model distinguishes energy flows sufficiently, implement a constraint network to identify export from the battery. Options:

* **Preferred (if feasible):**
  Introduce a decision variable `b_to_grid[t]` (battery → grid flow) such that:

  * `b_to_grid[t] <= b_dis[t]` (battery cannot export more than it discharges).
  * `b_to_grid[t] <= g_exp[t]` (cannot export more than total export).
  * `b_to_grid[t] >= g_exp[t] + b_dis[t] - PV_to_load_and_grid_capacity[t]` etc., as needed to bound it tightly.
  * Use `b_to_grid[t]` (or `b_exp[t]`) as the variable that gets `battery.export_margin_c_per_kwh` applied in the objective.

* **Fallback (if the system doesn’t differentiate sources):**
  Approximate by applying `battery.export_margin_c_per_kwh` to **all** export (`g_exp[t]`) and document that this currently penalises PV export too. This is less precise but simpler. The ideal implementation is source-aware.

The coding agent should choose the approach that is consistent with the rest of the flow modelling in the system.

---

## 5. Incentives and margins: keep them additive, not automatically scaled

Ensure that:

* Premiums/margins (`import_premium`, `export_premium`, `export_margin`, `wear_cost`) are **pure additive economic terms** in the objective.
* EV incentives and any other rewards are also expressed as fixed cents per kWh and added directly.

Implementation steps:

1. Review any existing code that modifies incentives or rewards based on tariffs (e.g. “scale by price”).
2. Confirm that EV incentives and similar are implemented as direct linear coefficients in the objective.
3. Do **not** add logic that multiplies incentives by premiums/margins inside the optimiser.
   If dynamic relationships are desired, they should be handled in configuration or pre-processing, not inside the LP formulation.

---

## 6. Tests and validation scenarios

Add a set of automated tests (unit tests and scenario tests) to confirm behaviour of the new knobs, independent of repository layout.

### 6.1. Unit tests on price derivation

* Test that, given:

  * `price_import = 20`, `import_premium = 5` → `eff_price_import = 25`
  * `price_export = 8`, `export_premium = 2` → `eff_price_export = 6`
* Test that `grid.min_export_price_c_per_kwh` and `grid.curtailment_min_export_price_c_per_kwh` are applied using **real** prices, not effective ones.

### 6.2. Scenario tests: qualitative behaviours

Create simple toy scenarios (few time steps) and assert qualitative decisions:

1. **Import vs battery vs self-consumption:**

   * With `import_premium = 0`, system is happy to import when price is low.
   * With `import_premium` set high, system prefers discharging battery / using PV, even at moderate import prices.

2. **Export reluctance:**

   * With `export_premium = 0`, system exports any surplus PV when FiT > 0.
   * With `export_premium` set high, system prefers charging battery or curtailing (when allowed) over exporting at low FiT.

3. **Battery export margin:**

   * With `export_margin = 0`, battery exports whenever FiT > wear cost.
   * With `export_margin` positive, battery only exports when FiT is sufficiently high relative to wear + margin.

4. **Curtailment correctness:**

   * When `price_export` drops below `min_export_price`, exports are structurally disabled regardless of premiums.
   * When `price_export` is negative or very low, curtailment becomes attractive even if `export_premium` is 0.

These tests are there to guard against regressions in the objective/constraint logic.

---

## 7. Documentation / UX updates

Finally, update user-facing documentation / comments so the knobs are understandable:

* Document clearly:

  * `import_premium_c_per_kwh` = “how much extra per kWh you’re willing to ‘pretend’ grid import costs to express self-sufficiency preference”.
  * `export_premium_c_per_kwh` = “how much less you want to value export in the optimisation, to bias towards self-use”.
  * `battery.wear_cost_c_per_kwh` = “estimated lifetime wear cost per kWh of battery throughput”.
  * `battery.export_margin_c_per_kwh` = “extra profit per kWh you want to see before using the battery to export to grid”.
  * `min_export_price_c_per_kwh` and any curtailment threshold = “hard rules based on real FiT; these determine when export is structurally disallowed or curtailment is enabled”.

* Make clear the **separation of concerns**:

  * Real prices for constraints & curtailment.
  * Effective prices for the objective.
  * All numbers in the same currency (¢/kWh).

---

That’s the full implementation plan in generic terms.

A coding agent can now:

1. Extend the config schema and migration.
2. Add real vs effective price derivation in the tariff pre-processing layer.
3. Refactor the LP objective to use effective prices and add the new cost terms.
4. Keep curtailment logic on real prices only.
5. Introduce (or approximate) a battery-specific export flow for the export margin.
6. Add tests and docs to lock in the new behaviour.

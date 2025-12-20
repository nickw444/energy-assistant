Below is a **clear, end-to-end technical design document** focused specifically on **how the MILP constraint system is implemented to create an energy plan**.
This is written so it can be handed to an engineer and directly implemented.

---

# Design Document

## MILP Constraint System for Residential Energy Planning

---

## 1. Purpose

This document defines the **constraint system and objective formulation** for a Mixed Integer Linear Programming (MILP) optimiser that generates an optimal residential energy plan over a fixed horizon.

The optimiser:

* Minimises energy cost
* Respects physical and contractual constraints
* Produces a **time-indexed energy plan**
* Is executed in an **MPC loop** (solve → apply first step → repeat)

This document **does not** cover:

* Forecast generation
* UI / UX
* Home Assistant integration
* Solver selection details

---

## 2. Planning Model Overview

### 2.1 Time discretisation

* Planning horizon: `N` steps (e.g. 288)
* Timestep duration: `Δt` hours (e.g. 5 minutes = 1/12 hour)
* Index: `t ∈ {0 … N-1}`

All decisions are assumed **constant over a timestep**.

---

## 3. Decision Variables

All variables are indexed by timestep `t` unless otherwise stated.

### 3.1 Grid interaction

| Variable      | Type           | Meaning                       |
| ------------- | -------------- | ----------------------------- |
| `G_import[t]` | Continuous ≥ 0 | Power imported from grid (kW) |
| `G_export[t]` | Continuous ≥ 0 | Power exported to grid (kW)   |

---

### 3.2 PV curtailment

| Variable     | Type           | Meaning                 |
| ------------ | -------------- | ----------------------- |
| `PV_curt[t]` | Continuous ≥ 0 | PV power curtailed (kW) |

Curtailment is optional but required to model negative feed-in prices safely.

---

### 3.3 Battery variables (per battery `b`)

#### State

| Variable   | Type       | Meaning                       |
| ---------- | ---------- | ----------------------------- |
| `SOC[b,t]` | Continuous | Battery state of charge (kWh) |

#### Continuous control variant

| Variable        | Type           |
| --------------- | -------------- |
| `B_chg[b,t]`    | Continuous ≥ 0 |
| `B_dis[b,t]`    | Continuous ≥ 0 |
| `B_is_chg[b,t]` | Binary         |
| `B_is_dis[b,t]` | Binary         |

#### Discrete mode variant

| Variable           | Type   |
| ------------------ | ------ |
| `B_mode_chg[b,t]`  | Binary |
| `B_mode_dis[b,t]`  | Binary |
| `B_mode_idle[b,t]` | Binary |

---

### 3.4 EV charging (per EV `e`)

| Variable      | Type           | Meaning                |
| ------------- | -------------- | ---------------------- |
| `EV_pwr[e,t]` | Continuous ≥ 0 | EV charging power (kW) |

---

### 3.5 Deferrable loads (per HWS `h`)

| Variable    | Type   | Meaning     |
| ----------- | ------ | ----------- |
| `H_on[h,t]` | Binary | Load on/off |

---

## 4. Parameters (Inputs)

### 4.1 Forecasts (time-series)

| Parameter     | Units |
| ------------- | ----- |
| `L_base[t]`   | kW    |
| `PV[t]`       | kW    |
| `p_import[t]` | $/kWh |
| `p_export[t]` | $/kWh |

### 4.2 Availability masks

Boolean (0/1) parameters:

* `A_import[t]`
* `A_ev[e,t]`
* `A_hws[h,t]`

---

### 4.3 Battery parameters

| Parameter                      | Units |
| ------------------------------ | ----- |
| `SOC_init[b]`                  | kWh   |
| `SOC_min[b]`                   | kWh   |
| `SOC_max[b]`                   | kWh   |
| `SOC_reserve[b]`               | kWh   |
| `η_chg[b]`, `η_dis[b]`         | –     |
| `P_chg_max[b]`, `P_dis_max[b]` | kW    |

---

### 4.4 EV parameters

| Parameter             | Units |
| --------------------- | ----- |
| `EV_max_pwr[e]`       | kW    |
| `EV_target_energy[e]` | kWh   |
| `EV_value[e]`         | $/kWh |

---

### 4.5 Grid / inverter limits

| Parameter           | Units |
| ------------------- | ----- |
| `G_import_max`      | kW    |
| `G_export_max`      | kW    |
| `INV_import_max[j]` | kW    |
| `INV_export_max[j]` | kW    |

---

## 5. Core Constraints

### 5.1 Energy balance (system-wide)

For every timestep `t`:

```
PV[t] - PV_curt[t]
+ G_import[t]
+ Σ_b B_dis[b,t]
=
L_base[t]
+ Σ_e EV_pwr[e,t]
+ Σ_h (H_on[h,t] · P_hws[h])
+ Σ_b B_chg[b,t]
+ G_export[t]
```

This is the **fundamental conservation constraint**.

---

### 5.2 Grid limits

```
0 ≤ G_import[t] ≤ G_import_max
0 ≤ G_export[t] ≤ G_export_max
```

---

### 5.3 Import restriction windows (hard)

If grid import is disallowed:

```
G_import[t] ≤ G_import_max · A_import[t]
```

---

### 5.4 PV curtailment bounds

```
0 ≤ PV_curt[t] ≤ PV[t]
```

---

## 6. Battery Constraints

### 6.1 SOC dynamics

For each battery `b`:

```
SOC[b,0] = SOC_init[b]
```

For all `t`:

```
SOC[b,t+1] =
  SOC[b,t]
+ B_chg[b,t] · Δt · η_chg[b]
- B_dis[b,t] · Δt / η_dis[b]
```

---

### 6.2 SOC bounds & reserve

```
SOC_min[b] ≤ SOC[b,t] ≤ SOC_max[b]
SOC[b,t] ≥ SOC_reserve[b]
```

---

### 6.3 Continuous battery control

```
B_chg[b,t] ≤ P_chg_max[b] · B_is_chg[b,t]
B_dis[b,t] ≤ P_dis_max[b] · B_is_dis[b,t]
B_is_chg[b,t] + B_is_dis[b,t] ≤ 1
```

---

### 6.4 Discrete battery control

```
B_mode_chg[b,t] + B_mode_dis[b,t] + B_mode_idle[b,t] = 1

B_chg[b,t] = P_chg_max[b] · B_mode_chg[b,t]
B_dis[b,t] = P_dis_max[b] · B_mode_dis[b,t]
```

---

## 7. EV Charging Constraints

### 7.1 Availability

```
EV_pwr[e,t] ≤ EV_max_pwr[e] · A_ev[e,t]
```

---

### 7.2 Opportunistic energy cap

Let:

```
E_needed[e] = max(0, target_SOC - current_SOC)
```

Then:

```
Σ_t EV_pwr[e,t] · Δt ≤ E_needed[e]
```

This makes EV charging **soft-targeted**, not mandatory.

---

## 8. Deferrable Load (HWS) Constraints

### 8.1 Availability

```
H_on[h,t] ≤ A_hws[h,t]
```

---

### 8.2 Daily minimum runtime

Let:

```
steps_required =
  ceil((min_minutes_per_day - minutes_run_today) / step_minutes)
```

Constraint:

```
Σ_t H_on[h,t] ≥ steps_required
```

---

## 9. Inverter Shared Limits

For each inverter `j`:

### 9.1 Export limit

```
PV_to_AC[j,t] + Σ_b∈j B_dis[b,t] ≤ INV_export_max[j]
```

### 9.2 Import limit

```
Σ_b∈j B_chg[b,t] ≤ INV_import_max[j]
```

(Initial implementations may aggregate per-household if only one inverter exists.)

---

## 10. Objective Function

### 10.1 Base cost

Minimise:

```
Σ_t [
  G_import[t] · Δt · p_import[t]
- G_export[t] · Δt · p_export[t]
]
```

---

### 10.2 EV charging benefit

Add a **negative cost**:

```
- Σ_e ( EV_value[e] · Σ_t EV_pwr[e,t] · Δt )
```

This makes EV charging preferred **only when economically sensible**.

---

### 10.3 Full objective

```
Minimise total_cost = energy_cost - EV_benefit
```

---

## 11. Plan Output

The solver produces:

* Full horizon plan (`t = 0…N-1`)
* First timestep (`t = 0`) used for execution

Example output (conceptual):

```json
{
  "t": 0,
  "grid_import_kW": 0,
  "grid_export_kW": 0,
  "battery": { "mode": "force_discharge", "power_kW": 5 },
  "ev_charge_kW": 3.6,
  "hws_on": false
}
```

---

## 12. Execution Philosophy

* MILP produces **ideal energy intents**
* Execution layer:

  * Maps intents to device capabilities
  * Enforces fast safety checks
* MPC loop corrects forecast error every run

---

## 13. Key Design Principles (Summary)

* **All hard constraints live in MILP**
* **Preferences are modelled as objective terms**
* **Discrete actuator limitations are modelled explicitly**
* **No rule engines**
* **No priority flags**
* **Economics drives behaviour**

---

## 14. Why This Works

* Deterministic
* Explainable
* Scales with device count
* Matches real inverter/battery limitations
* Aligns with grid-operator and VPP practice

---

If you want next, I can:

* Translate this directly into **PuLP code**
* Produce a **constraint trace diagram**
* Walk through a **numerical worked example**
* Provide a **validation & infeasibility handling strategy**

Just tell me where you’d like to go next.

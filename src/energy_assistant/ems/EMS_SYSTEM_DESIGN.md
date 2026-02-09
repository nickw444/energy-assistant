# EMS MILP System Design (v4 Implementation)

This document describes the **current implementation** under `src/energy_assistant/ems/` and
mirrors the shipped code. For developer workflow notes, see `src/energy_assistant/ems/AGENTS.md`.

The defining design choice in v4 is a **reusable topology template** that is bound per-solve
into a **query-only** set of MILP fragments (vars/constraints/objective terms). Domain logic is
owned by the fragments themselves; the solve pipeline only assembles and solves the returned
constraints and objective expression.

## 1. Scope and status

The EMS package builds and solves a PuLP MILP that produces a **time-stepped plan** for:

- Grid import/export
- PV utilization + curtailment
- Battery charge/discharge + SoC tracking
- Controllable EV charging + SoC tracking

The EMS does not directly apply control actions to devices; it only emits a plan for inspection
and for higher-level intent mapping.

## 2. Code Map (Actual Modules)

Top-level orchestration:

- `src/energy_assistant/ems/planner.py`
  - Orchestrates horizon selection, input alignment, solve, and plan extraction.
- `src/energy_assistant/ems/system/factory.py`
  - Builds the persistent `EmsSystem` template and produces per-run `EmsInputs`.
- `src/energy_assistant/ems/system/system.py`
  - Binds the template per-run, assembles the PuLP problem, and merges per-component plan outputs.

Layer 0: topology + generic link components:

- `src/energy_assistant/ems/topology/graph.py`
- `src/energy_assistant/ems/topology/nodes.py`
- `src/energy_assistant/ems/topology/connection.py`
- `src/energy_assistant/ems/topology/link_components.py`

Layer 1: logical components (compose Layer 0):

- `src/energy_assistant/ems/components/*`

Supporting modules (unchanged from earlier versions):

- `src/energy_assistant/ems/horizon.py` (time slotting; single and multi-resolution horizons)
- `src/energy_assistant/ems/forecast_alignment.py` (aligns forecasts to horizon slots)
- `src/energy_assistant/ems/pricing.py` (price transforms: bias/risk/zero-export preference)
- `src/energy_assistant/ems/fixture_harness.py` (fixture capture/replay/baselines)

## 3. Runtime Flow (What Actually Happens)

### 3.1 Planner solve (`EmsMilpPlanner.generate_ems_plan`)

1. Resolve and hydrate Home Assistant inputs via `ValueResolver`.
2. `EmsSystemFactory.resolve_forecasts(...)` loads forecast intervals (load, PV per inverter, import/export prices)
   and determines the shortest coverage horizon.
3. `build_horizon(...)` constructs a time horizon (single-resolution or multi-resolution).
4. `EmsSystemFactory.build_inputs(...)` aligns forecasts into slot series and emits an `EmsInputs` bundle:
   load series, PV availability series, pricing series, import-allowed series, EV gating series, and scalars
   (initial SoC, realtime power, etc).
5. `EmsSystem.bind(ModelContext(...))` creates a `ModelSnapshot`:
   - Binds the hidden topology template into a per-run topology model
   - Collects all fragment constraints/objective terms
   - Assembles a PuLP `LpProblem` (generic assembly; no domain logic)
6. Solve with CBC (`pulp.PULP_CBC_CMD`).
7. Extract per-component timestep plans via `EmsSystem.build_timestep_plans(snapshot)` and return `EmsPlanOutput`.

### 3.2 Worker + API

The worker schedules the solve loop. Each run hydrates HA data, solves the MILP, and stores the
latest `EmsPlanOutput` for API consumers.

## 4. Layered Architecture

### 4.1 Layer 0 (Hidden): Topology primitives + LinkComponents

Layer 0 is a minimal graph representation of energy flow. It is not exposed as the user-facing
model. It exists to let Layer 1 compose physical constraints without a monolithic builder.

Key types:

- **Node templates** (persistent): `BusNodeTemplate`, `PortNodeTemplate`, `StorageNodeTemplate`
- **Node models** (per-run): allocate PuLP vars and return constraints/objective terms
- **Connection templates/models**: bidirectional flow variables with composable LinkComponents
- **LinkComponents**: small pluggable constraint/objective fragments attached to connections

#### 4.1.1 Physical law: bus balance

`BusNodeModel` creates one constraint per bus per timestep:

- `sum(incoming_kW[t]) - sum(outgoing_kW[t]) == 0`

Incoming flow is scaled by the composed connection efficiency for that direction.

#### 4.1.2 Storage nodes (SoC)

`StorageNodeModel` owns SoC variables `E_by_i[i]` for `i = 0..N` and enforces:

- Initial SoC equality (from inputs)
- SoC dynamics driven by the incident connection's charge/discharge directional flows
- Min/max SoC bounds
- Optional terminal SoC modes (hard or adaptive, parity with legacy behavior)

#### 4.1.3 LinkComponents (connection modifiers)

LinkComponents are the reusable building blocks for physical constraints and objective terms.
They are split into templates and per-run models.

Implemented primitives include:

- `DirectionalLimit` (hard directional max kW)
- `ExclusiveDirection` (binary selector prevents simultaneous bidirectional flow)
- `Efficiency` (directional transport efficiency applied in bus balance)
- `LinearCostSeries` (linear cost coefficients per kWh, per direction)
- `SoftDirectionalLimitSeries` (soft upper limit with slack + penalty objective)
- `FixedFlowSeries` (equality constraint to a per-slot series)
- `UpperBoundSeries` (per-slot kW upper bound)
- `GateSeries` (per-slot gating `P <= max * gate[t]`)

#### 4.1.4 Extra fragments (policies and higher-order constraints)

Some constraints naturally reference multiple Layer 0 primitives (e.g., "battery reserve blocks all export").
These are modeled as extra fragment templates bound per-run and included in the snapshot fragment list.

### 4.2 Layer 1: Logical components (compose Layer 0)

Layer 1 components own:

- How to construct their topology fragments (nodes, connections, LinkComponents, extra fragments)
- How to extract their own per-timestep plan output (plan iterators)

Implemented components:

- `SwitchboardComponent`: AC bus
- `GridComponent`: grid import/export with pricing and forbidden-import slack
- `BaseLoadComponent`: fixed base load series on the switchboard bus
- `PvComponent`: PV availability + curtailment modes (fixed, load-aware, binary) with curtail tracking
- `BatteryComponent`: storage + wear/time costs + export-reserve policy fragment
- `InverterComponent`: DC bus plus AC/DC transfer constraints; composes PV + optional battery
- `EvComponent`: charge-only storage + gating + switch penalty + optional SoC incentives

Direction conventions are fixed per connection template so plan extraction can deterministically map
`import` vs `export`, `charge` vs `discharge`, etc.

### 4.3 Layer 2: Inputs and system factory

`EmsSystemFactory` builds:

- A persistent `EmsSystem` template (topology template + component objects)
- Per-run `EmsInputs` aligned to the current horizon

The topology template is reusable across planning cycles; each solve binds it into a fresh per-run model.

## 5. Configuration model (what the solver expects)

The EMS consumes `AppConfig` from `src/energy_assistant/models/config.py`. The high-level configuration
shape is unchanged by the v4 refactor; the main difference is where the mapping happens (factory vs builder).

See:

- `EmsConfig` for timestep/horizon configuration
- `PlantConfig` for grid + load + inverter definitions
- `LoadConfig` for controllable EV definitions

## 6. Testing

Tests live under `tests/energy_assistant/ems/`:

- Unit tests target Layer 0 primitives and Layer 1 components (limits, balance, SoC, curtailment, EV control).
- Regression baselines are recorded under `tests/fixtures/ems/<fixture>/<scenario>/` and validated by
  `tests/energy_assistant/ems/test_fixture_baselines.py`.

Use:

- `uv run energy-assistant ems record-scenario --fixture <fixture> --name <scenario>`
- `uv run energy-assistant ems refresh-baseline --fixture <fixture> --name <scenario>`


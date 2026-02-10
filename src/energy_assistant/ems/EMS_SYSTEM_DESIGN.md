# EMS MILP System Design (v4)

This document describes the **current implementation** under `src/energy_assistant/ems/`.
For developer workflow notes, see `src/energy_assistant/ems/AGENTS.md`.

The defining design choice in v4 is a **persistent topology graph** with **deferred (mutable)
inputs** owned by Layer 1 components. Each planning run:

1. Components resolve realtime + forecast inputs and update their deferred boxes.
2. The topology activates a horizon (`EnergyGraph.set_horizon(...)`), creating per-run PuLP vars.
3. The solve pipeline assembles the PuLP problem by **querying** fragments for constraints/objective.

There is no monolithic builder and no key-based `EmsInputs` bundle.

## Code Map (Actual Modules)

Top-level orchestration:

- `src/energy_assistant/ems/planner.py`
  - Orchestrates horizon sizing, component updates, solve, and plan extraction.
- `src/energy_assistant/ems/system/factory.py`
  - Wires config into a persistent `EmsSystem` (components + topology graph).
- `src/energy_assistant/ems/system/system.py`
  - Coordinates per-run updates, builds a `ModelSnapshot`, and merges per-component plans.
- `src/energy_assistant/ems/milp/snapshot.py`
  - Assembles a PuLP `LpProblem` from query-only fragments and solves it.

Layer 0: topology + generic link components:

- `src/energy_assistant/ems/topology/graph.py`
- `src/energy_assistant/ems/topology/nodes.py`
- `src/energy_assistant/ems/topology/connection.py`
- `src/energy_assistant/ems/topology/deferred.py` (deferred boxes)
- `src/energy_assistant/ems/topology/link_components/*` (modular LinkComponents)

Layer 1: logical components (compose Layer 0):

- `src/energy_assistant/ems/components/*`

Supporting modules:

- `src/energy_assistant/ems/horizon.py` (time slotting; single and multi-resolution horizons)
- `src/energy_assistant/ems/forecast_alignment.py` (align forecasts to horizon slots)
- `src/energy_assistant/ems/pricing.py` (price transforms: bias/risk/zero-export preference)
- `src/energy_assistant/ems/fixture_harness.py` (fixture capture/replay/baselines)

## Runtime Flow

### Planner solve (`EmsMilpPlanner.generate_ems_plan`)

1. Determine the base interval used for horizon sizing (`high_res_timestep_minutes` or `timestep_minutes`).
2. Ask the `EmsSystem` for the shortest forecast coverage across its components.
3. Build a `Horizon` with `build_horizon(...)` (single- or multi-resolution).
4. Call `EmsSystem.update(horizon, resolver)`:
   - Each component resolves the sources it owns (realtime + forecast intervals).
   - Each component aligns to the horizon and writes the aligned series/scalars into `Deferred` boxes.
5. Create a `ModelSnapshot` with `ModelContext(horizon=...)`:
   - `ModelSnapshot` calls `EnergyGraph.set_horizon(horizon)`, allocating per-run PuLP vars.
   - It queries all fragments (`graph.fragments`) for constraints and objective expressions.
   - It assembles and solves a PuLP `LpProblem` (generic assembly; no domain logic).
6. Extract per-component timestep plans and return `EmsPlanOutput`.

### Worker + API

The worker runs the solve loop. Each run hydrates HA data, solves the MILP, and stores the latest
`EmsPlanOutput` for API consumers.

## Layered Architecture

### Layer 0 (Hidden): Topology primitives + LinkComponents

Layer 0 is a minimal graph representation of energy flow. It is not exposed as the user-facing
model. It exists to let Layer 1 compose physical constraints without a monolithic solver builder.

Key types:

- **Graph**: `EnergyGraph`
  - Holds persistent nodes, connections, and cross-cutting fragments.
  - Activates a run via `set_horizon(...)` (allocates PuLP vars, rebuilds constraints/objective).
- **Nodes**:
  - `BusNode`: creates one balance constraint per bus per timestep.
  - `PortNode`: terminal node (no intrinsic constraints).
  - `StorageNode`: SoC dynamics over the horizon (vars + constraints + optional terminal objective).
- **Connections**:
  - `Connection`: bidirectional nonnegative flow vars (`P_a_to_b`, `P_b_to_a`) per timestep.
  - Connections are extended by composable `LinkComponent`s.
- **Deferred boxes**:
  - `Deferred[T]` and `DeferredSeries[T]` are mutable containers updated by components each run.
  - Topology fragments read deferred values when building constraints/objective for the current horizon.

#### Physical law: bus balance

For each `BusNode` and timestep `t`:

- `sum(incoming_kW[t]) - sum(outgoing_kW[t]) == 0`

Incoming power is scaled by the composed **transport efficiency** for the relevant direction.

#### Storage SoC

`StorageNode` owns `E_by_i[i]` for `i = 0..N` and enforces:

- Initial SoC equality
- SoC dynamics driven by its single incident connection's charge/discharge flows
- Min/max bounds
- Optional terminal modes (`hard` or `adaptive`) and optional terminal value reward

Storage efficiency is modeled as a **connection component** (`StorageEfficiency`) so batteries and EVs
do not need bespoke node types.

#### LinkComponents (connection modifiers)

LinkComponents are small, composable, query-only fragments attached to a single connection:

- `DirectionalLimit` (hard directional max kW; optional `exclusive=True`)
- `TransportEfficiency` (directional multiplier applied in bus balance)
- `StorageEfficiency` (directional multiplier applied in storage SoC dynamics)
- `LinearCost` (linear cost per kWh, per direction)
- `SoftDirectionalLimit` (soft upper bound with slack + penalty objective)
- `FixedFlow` (fix a directional flow to a per-slot series)
- `UpperBound` (per-slot kW upper bound)
- `Gate` (per-slot gating `P <= max * gate[t]`, with input validation)

### Extra fragments (cross-cutting policies)

Some constraints reference multiple primitives (e.g., "battery reserve blocks all export", EV terminal incentives).
These are modeled as `GraphFragment`s added to the `EnergyGraph`, and rebuilt each run during `set_horizon(...)`.

### Layer 1: Logical components

Layer 1 components own:

- How to create and hold references to their topology primitives (nodes, connections, LinkComponents, fragments).
- How to mark hydration dependencies (`mark_for_hydration`).
- How to update deferred boxes each run (`update(horizon, resolver)`).
- How to extract their own plan outputs (`iter_timestep_plan(snapshot)`).

Implemented components:

- `SwitchboardComponent`: AC bus
- `GridComponent`: grid import/export, pricing objective, forbidden-import slack
- `BaseLoadComponent`: fixed base load series
- `PvComponent`: PV availability + curtailment modes (fixed, load-aware, binary) with curtail tracking
- `BatteryComponent`: storage + wear/time costs + export-reserve policy
- `InverterComponent`: DC bus plus AC/DC transfer constraints; composes PV + optional battery
- `EvComponent`: charge-only storage + gating + switch penalty + optional SoC incentives

Direction conventions are fixed per connection so plan extraction can deterministically map
`import` vs `export`, `charge` vs `discharge`, etc.

## Testing

Tests live under `tests/energy_assistant/ems/`:

- Unit tests target Layer 0 primitives and LinkComponents (limits, balance, SoC, curtailment, EV control).
- Regression baselines are recorded under `tests/fixtures/ems/<fixture>/<scenario>/` and validated by
  `tests/energy_assistant/ems/test_fixture_baselines.py`.

Use:

- `uv run energy-assistant ems record-scenario --fixture <fixture> --name <scenario>`
- `uv run energy-assistant ems refresh-baseline --fixture <fixture> --name <scenario>`


# EMS MILP System Design

This document describes the **current implementation** under `src/energy_assistant/ems/`.
For workflow notes, see `src/energy_assistant/ems/AGENTS.md`.

## Summary

The EMS architecture is split into:

- **Logical components:** Grid, Inverter, PV, Battery, EV, Base Load, Switchboard.
- **Topology primitives:** graph nodes, connections, connection policies, and graph fragments.

Key behavior:

- EMS uses a configured **fixed-shape rolling horizon** rather than sizing the horizon from forecast
  coverage.
- EMS config is split into a typed `inputs` registry and a flat logical `plant` registry.
- Components are **persistent definitions** that implement the typed
  `EmsComponent[TSolveState, TPlanExport]` contract, own input parameter boxes, and consume a
  per-solve `AppliedInputRegistry`.
- Input resolution happens outside the EMS component layer in `src/energy_assistant/inputs/provider.py`, which produces
  raw resolved inputs.
- Forecast alignment, slot-0 realtime replacement, coverage validation, and price tail extension
  application happen inside EMS in `src/energy_assistant/ems/inputs/application.py`.
- `EmsSystem` stores a flat `components` registry plus a normalized `PlantTopology`. User
  config keeps `connection: "<component_id>"` as a target component reference, while the side/port
  is inferred from the source and target component types.
- At solve time, components **update their input boxes** from resolved inputs, then **emit
  solve-scoped topology elements** for the current horizon and return explicit typed solve-state
  artifacts used for plan extraction.
- PuLP problems and topology objects remain solve-scoped; persistent reuse is at the component and
  horizon-shape level.

## Runtime Flow

`EmsMilpPlanner.generate_ems_plan(...)`:

1. Build the current solve window from the configured rolling `HorizonShape`.
2. Get persistent component definitions from `EmsSystemFactory`.
3. Resolve the configured `inputs` registry into a per-solve raw `ResolvedInputRegistry`.
4. Apply raw inputs to the current horizon to produce `AppliedInputRegistry`.
5. Call `EmsSystem.update_inputs(horizon, inputs)`.
6. Call `EmsSystem.build_snapshot(horizon)`:
   - create a fresh `EnergyGraph`,
   - call each component's `build_graph(...)` method in normalized topology order,
   - add all returned elements through `EnergyGraph.add_elements(...)`,
   - collect typed solve-state artifacts in `SolveStateStore`,
   - collect fragment constraints/objective into `ModelSnapshot`.
7. Solve PuLP model.
8. Ask each component to export its typed component plan from the solved snapshot and the typed
   `SolveStateStore`.
9. Return a flat `components` map keyed by plant component id.

## Component Contract

Each component supports:

- `update_inputs(horizon, inputs)` to populate persistent scalar/series parameters
- `describe_topology()` to declare its normalized parent attachment
- `build_graph(horizon, build_ctx) -> (elements, solve_state)` to create solve-scoped topology
  primitives
- `build_plan(snapshot, solve_state, plan_ctx)` for result extraction

Components keep configuration and helper objects persistently, while solve-scoped MILP objects
(nodes/connections/connection fragments) are rebuilt per solve. For plan extraction they keep
explicit typed solve-state objects in a `SolveStateStore`, alongside the resolved input parameters.

The primary machine-readable EMS export is a flat `components` map on `EmsPlanOutput`. The keys match
the flat logical `plant` registry directly. Each value is a typed component-plan union carrying
component-specific `series` and, where applicable, immediate component-local `intent`. Time series
remain exported as `{time, value}` points, with no ownership tree or merged hierarchy introduced on
top of the plant model.

An inverter remains one logical component with one AC/DC link, but several `pv` and `battery`
entries may attach to the same inverter `connection`. In that case the inverter expands all of
those children onto its shared DC bus and still exports one flat component plan per logical child.

Input hydration is not performed by EMS components directly. The input provider walks the typed
`inputs` config, uses the source resolver when running live, and returns resolved scalar values plus
raw forecast point maps. EMS then applies those raw forecasts to the current horizon before
components consume the aligned series.

## Topology Model

### EnergyGraph

`EnergyGraph` contains solve-scoped:

- nodes,
- connections,
- extra cross-cutting fragments.

Elements are added through a single generic interface (`add_element` / `add_elements`) over the
`GraphElement` supertype (node/connection/fragment).

`ModelSnapshot` queries `graph.fragments` and performs generic assembly:

- add all constraints with stable unique names,
- set objective as sum of fragment objectives.

### Connection Flow Semantics

Each connection tracks explicit directional source/destination side power:

- `power_in_ab`, `power_out_ab`
- `power_in_ba`, `power_out_ba`

Helpers:

- `flow_in_ab`, `flow_out_ab`, `flow_in_ba`, `flow_out_ba`
- `flow_into_node(node_id)` / `flow_out_of_node(node_id)`

Transfer mapping is segment-defined within the connection policy chain:

- `policies` is a named map (`dict[str, ConnectionPolicy]`), so components can retrieve
  specific policies by name with typed lookups.
- each policy segment sees its own `flow_in_*` / `flow_out_*` variables.
- the default segment transfer is passthrough and is expressed through the
  normal policy `constraints(...)` interface
  (`flow_in_ab == flow_out_ab` and `flow_in_ba == flow_out_ba`).
- `DirectionalEfficiency` is lossy
  (`flow_out_ab = eta_a_to_b * flow_in_ab`, `flow_out_ba = eta_b_to_a * flow_in_ba`).
- multiple policies compose by chaining segment outputs into the next segment inputs.

### Nodes

- `Node`: generalized node with metadata (`node_role`); applies per-slot
  balance/role constraints:
  - `bus`: enforce `sum_in == sum_out`
  - `producer`: enforce net non-import (`sum_out >= sum_in`)
  - `consumer`: enforce net non-export (`sum_in >= sum_out`)
  - `prosumer`: no additional role constraint
- `StorageNode`: SoC dynamics and optional terminal behavior, using concrete initial SoC and
  optional concrete price series.

## Connection Policies

Current primitives:

- `DirectionalLimit` (`None` means unbounded; `exclusive=True` requires finite bounds)
- `Passthrough` (optional explicit no-op; injected automatically when no policies are defined)
- `DirectionalEfficiency`
- `LinearCost`
- `SoftDirectionalLimit`
- `FixedFlow`
- `UpperBound`

Connection policies may own internal vars (binary/slack/aux) keyed by `connection.id`.

## Cross-Component Policies

Cross-cutting constraints are graph fragments, e.g.:

- battery reserve blocks export,
- EV terminal SoC incentive segments.

These are now driven by normalized topology context rather than a special-purpose capability layer.
For example, batteries bind reserve constraints to the grid connections that belong to the same
switchboard island as their inverter parent.

## Testing

- Unit tests: `tests/energy_assistant/ems/test_*.py`
- Regression tests: `tests/energy_assistant/ems/test_fixture_baselines.py`
- Fixture assets: `tests/fixtures/ems/<fixture>/<scenario>/`

Useful commands:

- `uv run ruff check src custom_components tests`
- `uv run pyright`
- `uv run pytest`

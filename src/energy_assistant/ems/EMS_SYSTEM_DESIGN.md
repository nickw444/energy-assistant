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
  `EmsComponent[TSolveState, TPlanExport]` contract and consume a per-solve
  `AppliedInputRegistry` directly inside `create_graph_elements(...)`.
- Input resolution happens outside the EMS component layer in `src/energy_assistant/inputs/provider.py`, which produces
  raw resolved inputs.
- Forecast alignment, slot-0 realtime replacement, coverage validation, and price tail extension
  application happen inside EMS in `src/energy_assistant/ems/inputs/application.py`.
- `EmsSystem` stores a flat `components` registry plus an `ordered_components` tuple. User config
  keeps `connection: "<component_id>"` as a typed logical connection. `EmsSystemFactory` eagerly
  resolves those strings into direct component references. Those logical component objects stay
  separate from physical node ids such as AC/DC bus ids. The attachment side/port is inferred from
  the two component types.
- At solve time, components **read the current applied inputs** inside `create_graph_elements(...)`,
  then **emit solve-scoped physical graph elements** for the current horizon, return explicit typed
  solve-state artifacts used for plan extraction, and optionally emit follow-up fragments in
  `create_graph_fragments(...)` after all local elements have been built.
- PuLP problems and topology objects remain solve-scoped; persistent reuse is at the component,
  horizon-factory, and input-applicator level.

## Runtime Flow

`EmsMilpPlanner.generate_ems_run(...)`:

1. Build the current solve window from the configured rolling `HorizonFactory`.
2. Use the caller-owned persistent `EmsSystem` built by `EmsSystemFactory`.
3. Resolve the configured `inputs` registry into a per-solve raw `ResolvedInputRegistry`.
4. Apply raw inputs to the current horizon to produce `AppliedInputRegistry`.
5. Call `EmsSystem.build_snapshot(horizon, inputs)`:
   - create a fresh `EnergyGraph`,
   - call each component's `create_graph_elements(...)` method in `ordered_components` order,
   - add all returned elements through `EnergyGraph.add_elements(...)`,
   - collect typed solve-state artifacts in `SolveStateStore`,
   - call each component's `create_graph_fragments(...)` after the local elements are built,
   - collect fragment constraints/objective into `ModelSnapshot`.
6. Solve PuLP model.
7. Ask `EmsSystem` to resolve each component's typed solve state from the typed
   `SolveStateStore`, call `extract_plan(...)`, and normalize the result to `ComponentPlan`.
8. Return a flat `components` map keyed by plant component id.

## Component Contract

Each component exposes stable topology metadata:

- `id`
- `component_type`
- direct component references for logical wiring, such as `switchboard` or `inverter`

Each component also supports:

- `create_graph_elements(horizon, inputs, build_ctx) -> (elements, solve_state)` to create
  solve-scoped topology primitives from the current applied inputs
- `create_graph_fragments(graph, build_ctx, solve_states) -> list[GraphElement]` for follow-up
  cross-component fragments after all local elements are present
- `extract_plan(snapshot, solve_state, plan_ctx)` for result extraction

`EmsSystem` owns the solve-state lookup and `ComponentPlan` normalization for export. Components
only know how to read their typed solve state once it has been retrieved from the store.

Components keep configuration and helper objects persistently, while solve-scoped MILP objects
(nodes/connections/connection fragments) are rebuilt per solve. Per-solve input preprocessing should
live in small private helper methods inside the component rather than in a separate resolution
object. For plan extraction they keep explicit typed solve-state objects in a `SolveStateStore`,
alongside the values needed later from the current applied inputs.

The primary machine-readable EMS export is a flat `components` map on `EmsPlanOutput`. The keys match
the flat logical `plant` registry directly. Each value is a typed component-plan union carrying
component-specific `series` only. Time series remain exported as `{time, value}` points, with no
ownership tree, merged hierarchy, or component-local intent layer introduced on top of the plant
model for now.

An inverter remains one logical component with one AC/DC link, but several `pv` and `battery`
entries may attach to the same inverter `connection`. In that case those components share the
inverter DC bus and still export one flat component plan per logical component.

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
switchboard island as the inverter they connect to.

## Testing

- Unit tests: `tests/energy_assistant/ems/test_*.py`
- Regression tests: `tests/energy_assistant/ems/test_fixture_baselines.py`
- Fixture assets: `tests/fixtures/ems/<fixture>/<scenario>/`

Useful commands:

- `uv run ruff check src custom_components tests`
- `uv run pyright`
- `uv run pytest`

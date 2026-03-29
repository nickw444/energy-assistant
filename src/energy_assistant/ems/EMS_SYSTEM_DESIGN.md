# EMS MILP System Design (v6)

This document describes the **current implementation** under `src/energy_assistant/ems/`.
For workflow notes, see `src/energy_assistant/ems/AGENTS.md`.

## Summary

The EMS architecture is split into:

- **Layer 1 (logical components):** Grid, Inverter, PV, Battery, EV, Base Load, Switchboard.
- **Layer 0 (hidden topology):** graph nodes, connections, connection policies, and graph fragments.

Key behavior in v6:

- Layer 1 components are **horizon-agnostic definitions**.
- At solve time, components are asked to **build their topology elements** for the specific horizon.
- Components resolve realtime/forecast values during that build step and pass **concrete scalars/series**
  into topology/link primitives.
- No deferred-value box layer is used in the MILP build path.

## Runtime Flow

`EmsMilpPlanner.generate_ems_plan(...)`:

1. Determine horizon length from forecast coverage.
2. Build `Horizon`.
3. Get persistent component definitions from `EmsSystemFactory`.
4. Call `EmsSystem.build_snapshot(horizon, resolver)`:
   - create a fresh `EnergyGraph`,
   - call each component's `build(...)` method (returns run-scoped topology elements),
   - add all returned elements through `EnergyGraph.add_elements(...)`,
   - collect fragment constraints/objective into `ModelSnapshot`.
5. Solve PuLP model.
6. Ask each component to iterate plan output from solved vars.

## Layer 1 Component Contract

Each component supports:

- `mark_for_hydration(resolver)`
- `forecast_coverage_intervals(now, interval_minutes, resolver)` when relevant
- `build(horizon, resolver, ...) -> list[GraphElement]` to create run-scoped topology primitives
- `iter_timestep_plan(snapshot)` for result extraction where applicable

Components keep configuration and helper objects persistently, while run-scoped MILP objects
(nodes/connections/connection fragments) are rebuilt per solve. For plan extraction they keep references
to the latest run-scoped topology objects only.

## Layer 0 Topology Model

### EnergyGraph

`EnergyGraph` contains run-scoped:

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
- the default segment transfer is passthrough
  (`flow_in_ab == flow_out_ab` and `flow_in_ba == flow_out_ba`).
- `DirectionalEfficiency` is lossy
  (`flow_out_ab = eta_a_to_b * flow_in_ab`, `flow_out_ba = eta_b_to_a * flow_in_ba`).
- multiple transfer-like policies compose by chaining segment outputs into the next segment inputs.

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
- `Passthrough` (optional explicit no-op; default transfer behavior is already lossless)
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

## Testing

- Unit tests: `tests/energy_assistant/ems/test_*.py`
- Regression tests: `tests/energy_assistant/ems/test_fixture_baselines.py`
- Fixture assets: `tests/fixtures/ems/<fixture>/<scenario>/`

Useful commands:

- `uv run ruff check src custom_components tests`
- `uv run pyright`
- `uv run pytest`

# EMS Architecture Overview

This document duplicates the current-state EMS architecture summary from
`docs/plans/2026-03-29-ems-refactor-program/README.md` so there is a stable
canonical home for the steady-state architecture.

Implementation and review history for the refactor work lives in PR
[#150](https://github.com/nickw444/energy-assistant/pull/150).

## Summary
The EMS moved from an original monolithic planner shape to a layered design.

The main goals were:

- separate physical topology from logical device behavior,
- separate data resolution from planning,
- make constraints and objective terms locally owned and composable,
- keep plan extraction aligned with the logical model,
- preserve historical scenario behavior closely enough to validate the refactor safely.

The final shape is not identical to the earliest proposal. Several ideas were
explored and then deliberately simplified. This document describes the
direction that actually landed.

## General Direction
The EMS is organized around four explicit concepts.

### 1. Hidden topology layer
The physical energy system is modeled as a graph.

That graph is responsible for:

- energy flow relationships,
- conservation and balance rules,
- storage state and terminal behavior,
- connection-local constraints and costs,
- cross-cutting optimization fragments.

The main simplification here was to avoid a large set of physical entity
types. Instead, the topology uses a generalized node model for ordinary
producers, consumers, buses, and prosumers, with storage remaining the special
case because state-of-charge behavior is fundamentally different from ordinary
flow-only entities.

### 2. Logical component layer
Logical devices are modeled separately from the physical graph.

This layer is responsible for:

- representing the user-facing electrical system concepts,
- owning device-specific configuration and input bindings,
- expanding logical components into physical graph elements for a solve,
- exposing plan output back in logical terms.

This allows the planner to reason in terms of grid, inverter, battery, PV, EV,
load, and switchboard behavior while still solving against a deeper physical
model.

Plan and forecast export should also stay aligned with this logical layer.
Public/exported EMS data should be shaped by logical plant components rather
than by raw solver variable names.

### 3. Input boundary
Data resolution is a separate concern from EMS modeling.

The resulting boundary is:

1. resolve raw values from external sources,
2. apply those values to the current planning horizon,
3. update logical components,
4. build the physical graph,
5. solve.

This means the EMS model no longer owns source hydration or asynchronous
external lookups internally. It consumes typed data instead.

### 4. Fixed-shape rolling planning
The planner no longer sizes itself from forecast coverage. It uses an
explicitly configured rolling horizon shape.

That change made the system more predictable and easier to validate.
Forecasts now need to cover the configured horizon rather than implicitly
determining the size of the optimization problem.

## What Changed In Practice

### Topology and constraints
Connections model directional source-side and sink-side power explicitly. This
allows connection-local behavior, such as efficiency and limits, to be
expressed as ordinary constraints over visible directional flow rather than as
hidden scaling behavior.

Connection-local policies are best understood as ordered segments in a
connection chain. The key ideas are:

- an empty connection defaults to passthrough behavior,
- additive policies preserve passthrough transport and add extra constraints,
- transforming policies define their own transport law.

This is one of the central abstractions of the current EMS design.

### Configuration
The EMS configuration is split into two flat registries:

- an input registry for reusable typed data definitions,
- a plant registry for logical electrical components and their connections.

This separates data acquisition concerns from topology and logical-device
concerns.

The configuration expresses wiring logically rather than physically. Attach
point details such as AC-side versus DC-side behavior are inferred from
component type instead of being spelled out by the user.

### Forecast handling
Forecast handling is split into two stages:

- raw resolved forecast data,
- horizon-applied forecast data.

That matters because forecast alignment, realtime slot replacement, coverage
validation, and price forecast extension are planner concerns, not generic
input concerns.

### Construction and dependency wiring
The refactor also depends on keeping object construction explicit.

Runtime objects should receive their required instance dependencies through
their constructors rather than constructing subdependencies internally. In
other words: `new` is glue.

That rule matters because hidden constructor-time wiring weakens the seams the
refactor was meant to introduce. If a component instantiates helpers
internally, tests can no longer provide explicit fakes or mocks at the real
boundary, and the code quietly reintroduces local service-location behavior
inside otherwise modular classes.

Automatic wiring is still allowed, but it should live at an outer composition
boundary:

- the existing system factory may assemble production dependencies,
- a class may expose a convenience `create(...)` classmethod that constructs
  default subdependencies,
- the primary `__init__` path should continue to accept those dependencies
  explicitly.

This keeps the runtime graph easy to inspect, keeps tests honest, and
preserves the refactor goal that data flow and ownership boundaries remain
visible in code.

### Fixture and regression workflow
Scenario fixtures are a first-class validation mechanism.

The fixture system supports:

- scenario-specific EMS configuration,
- replay from captured data or resolved planner inputs,
- stable scenario baselines used to validate refactor safety.

This is what allowed the refactor to proceed while preserving confidence that
the planner still behaves acceptably across historical scenarios.

## Important Outcomes
The refactor achieved the original goals in a practical way:

- the old monolithic planner shape was removed,
- the physical model is explicit and graph-based,
- logical behavior is encapsulated separately from physical flow,
- input resolution no longer leaks into the planner model,
- configuration is flatter and easier to validate,
- scenario replay is strong enough to act as a real regression gate.

Just as important, the refactor also clarified what the system is not doing:

- it does not use a separate domain-specific compiler layer,
- it does not depend on a large number of concrete physical node types,
- it does not currently reuse solve-scoped solver objects across planning cycles,
- it does not rely on a single central builder abstraction anymore.

## Current Conceptual Model
The EMS should be thought about in this order:

1. Inputs provide typed raw values and forecasts.
2. Horizon application turns those into data for the current solve window.
3. Logical components consume that data and expand themselves into a graph.
4. Topology owns the physical constraints, objective terms, and solver variables.
5. Plan extraction maps solved values back into logical outputs.

Those logical outputs include a flat component-keyed series export that mirrors
the flat `plant` registry directly. Time-varying values are exported as
ordered `{ time, value }` points rather than as a generic flattened
solver-variable map.

This is the core mental model future work should start from.

## Architecture Diagrams
The current code architecture and runtime data flow are captured in these
diagram sets:

- [High-level dependency graph](./high-level-dependency-graph.svg)
- [Plan-generation data flow](./plan-generation-data-flow.svg)
- [Plant composition](./plant-composition.svg)
- [Topology assembly](./topology-assembly.svg)

The DOT sources live alongside the rendered diagrams.

## What To Preserve
Future work should preserve these boundaries unless there is a compelling
reason to collapse them again:

- keep topology separate from logical component behavior,
- keep input resolution outside the planner model,
- keep constructor dependency wiring explicit; do not instantiate required
  collaborators inside `__init__`,
- keep connection-local flow behavior explicit,
- keep scenario baselines as a hard regression signal,
- keep configuration focused on logical wiring and typed inputs rather than
  leaking implementation details.

## Follow-up Opportunities
The refactor intentionally leaves room for future improvements:

- more topology policies for new physical or market behaviors,
- better long-term documentation of the input and plant schema,
- clearer fixture-format strategy once migration work fully settles,
- further optimization of solve-time object creation if it ever becomes necessary,
- future behavioral changes, such as export-reserve semantics, once they can
  be addressed independently of the structural refactor.

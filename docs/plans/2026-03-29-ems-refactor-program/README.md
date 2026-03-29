# EMS Refactor Program (2026-03-29)

## Summary
This document records the EMS refactor from the original monolithic planner shape to the current layered design.

Implementation and review history for this work lives in PR [#150](https://github.com/nickw444/energy-assistant/pull/150).

The original motivation was to replace a large, difficult-to-reason-about EMS builder with an architecture that is easier to extend safely. The main goals were:

- separate physical topology from logical device behavior,
- separate data resolution from planning,
- make constraints and objective terms locally owned and composable,
- keep plan extraction aligned with the logical model,
- preserve historical scenario behavior closely enough to validate the refactor safely.

The final shape is not identical to the earliest proposal. Several ideas were explored and then deliberately simplified. This document describes the direction that actually landed.

## General Direction of the Refactor
The refactor moved the EMS toward four explicit concepts.

### 1. Hidden topology layer
The physical energy system is now modeled as a graph.

That graph is responsible for:

- energy flow relationships,
- conservation and balance rules,
- storage state and terminal behavior,
- connection-local constraints and costs,
- cross-cutting optimization fragments.

The main simplification here was to avoid a large set of physical entity types. Instead, the topology uses a generalized node model for ordinary producers, consumers, buses, and prosumers, with storage remaining the special case because state-of-charge behavior is fundamentally different from ordinary flow-only entities.

### 2. Logical component layer
Logical devices are modeled separately from the physical graph.

This layer is responsible for:

- representing the user-facing electrical system concepts,
- owning device-specific configuration and input bindings,
- expanding logical components into physical graph elements for a solve,
- exposing plan output back in logical terms.

This allows the planner to reason in terms of grid, inverter, battery, PV, EV, load, and switchboard behavior while still solving against a deeper physical model.

### 3. Input boundary
Data resolution is now a separate concern from EMS modeling.

The resulting boundary is:

1. resolve raw values from external sources,
2. apply those values to the current planning horizon,
3. update logical components,
4. build the physical graph,
5. solve.

This means the EMS model no longer owns source hydration or asynchronous external lookups internally. It consumes typed data instead.

### 4. Fixed-shape rolling planning
The planner moved away from sizing itself from forecast coverage and instead uses an explicitly configured rolling horizon shape.

That change made the system more predictable and easier to validate. Forecasts now need to cover the configured horizon rather than implicitly determining the size of the optimization problem.

## What Changed in Practice
### Topology and constraints
Connections now model directional source-side and sink-side power explicitly. This allows connection-local behavior, such as efficiency and limits, to be expressed as ordinary constraints over visible directional flow rather than as hidden scaling behavior.

Connection-local policies are now best understood as ordered segments in a connection chain. The key ideas are:

- an empty connection defaults to passthrough behavior,
- additive policies preserve passthrough transport and add extra constraints,
- transforming policies define their own transport law.

This became one of the central abstractions of the refactor.

### Configuration
The EMS configuration was redesigned into two flat registries:

- an input registry for reusable typed data definitions,
- a plant registry for logical electrical components and their connections.

This separated data acquisition concerns from topology and logical-device concerns.

The configuration now expresses wiring logically rather than physically. Attach-point details such as AC-side versus DC-side behavior are inferred from component type instead of being spelled out by the user.

### Forecast handling
Forecast handling was split into two stages:

- raw resolved forecast data,
- horizon-applied forecast data.

That change was important because forecast alignment, realtime slot replacement, coverage validation, and price forecast extension are planner concerns, not generic input concerns.

### Fixture and regression workflow
Scenario fixtures became a first-class validation mechanism during the refactor.

The fixture system now supports:

- scenario-specific EMS configuration,
- replay from captured data or resolved planner inputs,
- stable scenario baselines used to validate refactor safety.

This is what allowed the refactor to proceed while preserving confidence that the planner still behaves acceptably across historical scenarios.

## Important Outcomes
The refactor achieved the original goals in a practical way:

- the old monolithic planner shape was removed,
- the physical model is now explicit and graph-based,
- logical behavior is encapsulated separately from physical flow,
- input resolution no longer leaks into the planner model,
- configuration is flatter and easier to validate,
- scenario replay is strong enough to act as a real regression gate.

Just as important, the refactor also clarified what the system is **not** doing:

- it does not use a separate domain-specific compiler layer,
- it does not depend on a large number of concrete physical node types,
- it does not currently reuse run-scoped solver objects across planning cycles,
- it does not rely on a single central builder abstraction anymore.

## Current Conceptual Model
The current EMS should be thought about in this order:

1. **Inputs** provide typed raw values and forecasts.
2. **Horizon application** turns those into data for the current solve window.
3. **Logical components** consume that data and expand themselves into a graph.
4. **Topology** owns the physical constraints, objective terms, and solver variables.
5. **Plan extraction** maps solved values back into logical outputs.

This is the core mental model future agent sessions should start from.

## What to Preserve Going Forward
Future work should preserve these boundaries unless there is a compelling reason to collapse them again:

- keep topology separate from logical component behavior,
- keep input resolution outside the planner model,
- keep connection-local flow behavior explicit,
- keep scenario baselines as a hard regression signal,
- keep configuration focused on logical wiring and typed inputs rather than leaking implementation details.

## Follow-up Opportunities
The refactor intentionally leaves room for future improvements:

- more topology policies for new physical or market behaviors,
- better long-term documentation of the input and plant schema,
- clearer fixture-format strategy once migration work fully settles,
- further optimization of solve-time object creation if it ever becomes necessary,
- future behavioral changes, such as export-reserve semantics, once they can be addressed independently of the structural refactor.

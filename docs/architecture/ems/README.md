# EMS Architecture

This directory documents the current EMS architecture as implemented under
`src/energy_assistant/ems/`.

These docs are intended to explain the steady-state system shape.

The content here is intentionally duplicated from
`docs/plans/2026-03-29-ems-refactor-program/` where that older plan material
still provides useful canonical background. The plan directory remains the
historical record; this directory is the stable architecture entry point.

## Core Docs

- [Overview](./overview.md)
  - Current conceptual model, architecture boundaries, and refactor outcomes.
- [Decision register](./decision-register.md)
  - Accepted architectural decisions that define the current EMS shape.
- [Follow-ups](./follow-ups.md)
  - Deferred architectural cleanup and future improvement notes.

## Diagrams

- [High-level dependency graph](./high-level-dependency-graph.svg)
  - Shows top-level package and runtime dependencies, including worker and CLI
    one-off paths.
- [Plan-generation data flow](./plan-generation-data-flow.svg)
  - Shows how a plan is produced in live and CLI-driven execution modes.
- [Plant composition](./plant-composition.svg)
  - Shows how the flat `plant` registry composes into persistent logical EMS
    components.
- [Topology assembly](./topology-assembly.svg)
  - Shows how logical components emit solve-scoped topology elements and how
    those become a PuLP model.

## Related material

- [EMS system design notes](../../../src/energy_assistant/ems/EMS_SYSTEM_DESIGN.md)
- [Refactor overview](../../plans/2026-03-29-ems-refactor-program/README.md)
- [Decision register](../../plans/2026-03-29-ems-refactor-program/DECISIONS.md)
- [Refactor follow-ups](../../plans/2026-03-29-ems-refactor-program/FOLLOW_UPS.md)

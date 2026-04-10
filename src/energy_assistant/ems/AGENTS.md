## EMS (MILP Planner)

Scope: `src/energy_assistant/ems/`.
Assumes repo-wide conventions in the repo-root `AGENTS.md`.

This package builds and solves a PuLP MILP and produces an `EmsPlanOutput` for
inspection and baseline regeneration. Canonical implementation notes live in
`src/energy_assistant/ems/EMS_SYSTEM_DESIGN.md`.

Key files:
- `src/energy_assistant/ems/planner.py` orchestrates build, solve, and plan extraction.
- `src/energy_assistant/ems/system/component.py`, `src/energy_assistant/ems/system/state.py`, and `src/energy_assistant/ems/system/topology.py` define the typed EMS component contract, solve-state store, and normalized attachment graph.
- `src/energy_assistant/ems/system/factory.py` wires the flat `plant` registry into a persistent `EmsSystem` plus the configured rolling `HorizonShape`.
- `src/energy_assistant/inputs/provider.py` resolves configured sources into per-solve raw planner datapoints.
- `src/energy_assistant/ems/inputs/application.py` applies raw forecast inputs to the current horizon and produces the aligned `AppliedInputRegistry` consumed by components.
- `src/energy_assistant/ems/system/system.py` updates component inputs from the applied registry, builds the solve-scoped MILP snapshot from the normalized topology, and merges per-component plans.
- `src/energy_assistant/ems/topology/*` contains Layer 0 topology primitives (nodes, connections, policies).
- `src/energy_assistant/ems/components/*` contains Layer 1 logical components (Grid, PV, Battery, EV, etc).
- `src/energy_assistant/ems/planning/horizon.py` defines the persistent rolling `HorizonShape` and per-solve `Horizon`.
- `src/energy_assistant/ems/inputs/alignment.py` aligns forecast intervals to horizon slots.
- `src/energy_assistant/ems/planning/pricing.py` applies price transforms used by the objective (bias, risk, etc).
- `src/energy_assistant/ems/intent.py` contains component-local intent helpers used while building typed component plans.
- `src/energy_assistant/ems/fixtures/harness.py` supports offline plan baselines and reports.
- `src/energy_assistant/inputs/fixtures.py` supports raw resolved-input fixture capture and replay.

Design rules:
- Keep constructor dependencies explicit. Persistent runtime classes should accept required collaborators through `__init__` rather than constructing them internally.
- Use `EmsSystemFactory.create(app_config)` as the EMS composition root for production wiring of components and helper services.
- Treat `connection: "<component_id>"` in plant config as a target component reference. Attachment side/port is inferred from the source and target component types, not from explicit port labels.
- Keep solve-scoped topology and MILP objects created inside per-solve methods; the explicit-DI rule is for persistent collaborators, not ephemeral solve artifacts.

Testing workflow:
- EMS tests live under `tests/energy_assistant/ems/`.
- Fixture baselines live under `tests/fixtures/ems/<fixture>/<scenario>/`.
- Record a new scenario: `uv run energy-assistant ems record-scenario --fixture <fixture> --name <scenario>`; this writes canonical resolved planner inputs to `input.json`.
- Replay a fixture: `uv run energy-assistant ems solve --fixture <fixture> --scenario <scenario>`
- Refresh baselines: `uv run energy-assistant ems refresh-baseline [--fixture ...] [--name ...]`
- Render a report: `uv run energy-assistant ems scenario-report [--fixture ...]`
- Fixture scenario outputs now include `ems_plan.json`, `ems_plan.jpeg`, and `ems_plan.hash`.

## Continuous learning
- Update this file when EMS developer workflows or the high-level mental model changes.
- Update `src/energy_assistant/ems/EMS_SYSTEM_DESIGN.md` when you change the modeled problem (variables, constraints, objective terms).
- Keep implementation quirks and edge cases as comments next to the relevant EMS code instead of expanding this file.

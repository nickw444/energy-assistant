## EMS (MILP Planner)

Scope: `src/energy_assistant/ems/`.
Assumes repo-wide conventions in the repo-root `AGENTS.md`.

Canonical package overview and EMS fixture commands live in `src/energy_assistant/ems/README.md`.
Keep this file limited to coding-agent guidance that is not useful to human readers.

Design rules:
- Keep constructor dependencies explicit. Persistent runtime classes should accept required collaborators through `__init__` rather than constructing them internally.
- Use `EmsSystemFactory.create().build(app_config)` to build the persistent `EmsSystem`; compose sibling planner dependencies such as `HorizonFactory` and `EmsInputApplicator` at the caller.
- Keep logical component references separate from physical `NodeId` values.
- Keep solve-state lookup and `ComponentPlan` normalization inside `EmsSystem`.
- Keep solve-scoped topology and MILP objects created inside per-solve methods; the explicit-DI rule is for persistent collaborators, not ephemeral solve artifacts.

## Continuous learning
- Update `src/energy_assistant/ems/README.md` when EMS developer workflows or the high-level mental model changes.
- Keep implementation quirks and edge cases as comments next to the relevant EMS code instead of expanding this file.

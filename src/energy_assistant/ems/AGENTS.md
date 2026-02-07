## EMS (MILP Planner)

Scope: `src/energy_assistant/ems/`.
Assumes repo-wide conventions in the repo-root `AGENTS.md`.

This package builds and solves a PuLP MILP and produces an `EmsPlanOutput` for
plotting and inspection. Canonical implementation notes live in
`src/energy_assistant/ems/EMS_SYSTEM_DESIGN.md`.

Key files:
- `src/energy_assistant/ems/builder.py` builds variables, constraints, and objective.
- `src/energy_assistant/ems/planner.py` orchestrates build, solve, and plan extraction.
- `src/energy_assistant/ems/horizon.py` handles time slotting (single and multi-resolution horizons).
- `src/energy_assistant/ems/forecast_alignment.py` aligns forecast intervals to horizon slots.
- `src/energy_assistant/ems/pricing.py` applies price transforms used by the objective (bias, risk, etc).
- `src/energy_assistant/ems/intent.py` maps a plan into a higher-level intent used by API consumers.
- `src/energy_assistant/ems/fixture_harness.py` supports offline fixture capture, replay, and baselines.

Testing workflow:
- EMS tests live under `tests/energy_assistant/ems/`.
- Fixture baselines live under `tests/fixtures/ems/<fixture>/<scenario>/`.
- Record a new scenario: `uv run energy-assistant ems record-scenario --fixture <fixture> --name <scenario>`
- Replay a fixture: `uv run energy-assistant ems solve --fixture <fixture> --scenario <scenario>`
- Refresh baselines: `uv run energy-assistant ems refresh-baseline [--fixture ...] [--scenario ...]`
- Render a report: `uv run energy-assistant ems scenario-report [--fixture ...]`

## Continuous learning
- Update this file when EMS developer workflows or the high-level mental model changes.
- Update `src/energy_assistant/ems/EMS_SYSTEM_DESIGN.md` when you change the modeled problem (variables, constraints, objective terms).
- Keep implementation quirks and edge cases as comments next to the relevant EMS code instead of expanding this file.

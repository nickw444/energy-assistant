## Inputs (Raw Planner Inputs)

Scope: `src/energy_assistant/inputs/`.
Assumes repo-wide conventions in the repo-root `AGENTS.md`.

This package owns upstream input retrieval and marshalling for the planner. It resolves
configured sources into raw datapoints and scalar values that EMS can consume without
performing any live re-resolution.

Rules:
- Keep this package focused on source-backed retrieval, raw point-map serialization, and
  forecast expansion that depends on live/history resolution.
- Do not move EMS horizon alignment, coverage validation, or price-filter policy here;
  those are planner semantics and stay under `src/energy_assistant/ems/`.
- Prefer deterministic DTO-style models for raw input payloads and keep fixture helpers
  format-stable.

## Continuous learning
- Update this file when the raw input boundary changes or new input workflows are added.

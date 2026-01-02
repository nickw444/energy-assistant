## Ways of working
- Use uv for dependency management and scripts. Keep tooling config in `pyproject.toml` and `pyrightconfig.json`.
- Default to built-in exceptions unless a distinct custom type is justified.
- Keep backend and worker logic modular; API is FastAPI, worker runs background planning tasks (scheduled every minute) and is wired from `cli` with explicit dependencies. Worker code lives in `hass_energy/worker/` so it can grow into multiple modules. Avoid tight coupling so the future frontend can live alongside the backend at the repo root.
- Persist config and runtime artifacts to the filesystem (`data_dir` from YAML config). The single YAML file (default `config.yaml`) stores server + Home Assistant + plant + energy settings; it is read once at startup and the API is read-only for config (no writes). Avoid destructive commands that would drop user data.
- Shared helpers (e.g., Home Assistant client) live under `hass_energy/lib/` to keep worker/API code lean.
- CLI accepts a YAML config (`--config`, default `config.yaml`) for static settings like host, port, and data_dir. Config is validated with Pydantic. Worker is always on; host/port flags were removed.
- Routes are split by domain under `hass_energy/api/routes/` (e.g., `plan`, `settings`). Settings endpoint surfaces runtime energy settings (read-only; user edits YAML).
- MILP logic lives under `hass_energy/worker/milp/` using PuLP; planner/compiler are placeholders awaiting real constraints.
- MILP v2 scaffolding lives under `src/hass_energy/milp_v2/` with a compile phase (config + `ValueResolver` -> `CompiledModel`) and an execute phase (solve -> `PlanResult`).
- CLI `hass-energy milp` now wires the MILP v2 planner (compiler + executor); it currently fails until those phases are implemented.
- MILP v2 slotting uses `EmsConfig.interval_duration` and `EmsConfig.num_intervals` to align forecast slots to the current block start.
- Plotting helpers live in `src/hass_energy/plotting/` and are shared by CLI.
- A lightweight plan checker lives at `hass_energy/worker/milp/checker.py` with pytest coverage in `tests/`.
- `hass_energy/worker/milp/ha_dump.py` now emits a single-battery stub in realtime inputs when `battery_soc` is available (capacity/limits are currently constants).
- `hass_energy/worker/milp/ha_dump.py` emits a simple EV stub when `ev_connected` is true (defaults for capacity, target SOC, max power, value-per-kWh, min power, and switch penalty).
- Tests should mirror the `src/hass_energy` package structure under `tests/` (e.g., `tests/hass_energy/ems/`).
- Planner now consumes a resolved payload (no source models). Resolved schemas live in `src/hass_energy/models/resolved.py`; resolution scaffolding/registry is under `src/hass_energy/lib/resolution/` for two-pass fetch→transform in the future.
- EMS-specific guidance lives in `src/hass_energy/ems/AGENTS.md`.
- EMS plan `EconomicsTimestepPlan` costs are grid import/export only and exclude other objective terms (EV incentives, penalties, curtailment tie-breaks, violation penalties, battery wear).
- `EmsPlanOutput` now includes `objective_value` with the solver objective (may be negative/None).
- `ConfigMapper` (`src/hass_energy/lib/resolver/__init__.py`) offers a recursive walk utility that calls a visitor for side effects and allows halting recursion by returning `False`.
- Home Assistant integration (POC) lives under `custom_components/hass_energy`.

## Continuous learning
- When you learn new project knowledge, coding style, or preferences during a session, update `AGENTS.md` (and `README.md` if it affects users) before finishing so the next agent benefits.

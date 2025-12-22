## Ways of working
- Use uv for dependency management and scripts. Keep tooling config in `pyproject.toml` and `pyrightconfig.json`.
- Default to built-in exceptions unless a distinct custom type is justified.
- Keep backend and worker logic modular; API is FastAPI, worker is a background thread. Worker code lives in `hass_energy/worker/` so it can grow into multiple modules. Avoid tight coupling so the future frontend can live alongside the backend at the repo root.
- Persist config and runtime artifacts to the filesystem (`data_dir` from YAML config). The single YAML file (default `config.yaml`) stores server + Home Assistant + plant + energy settings; it is read once at startup and the API is read-only for config (no writes). Avoid destructive commands that would drop user data.
- Shared helpers (e.g., Home Assistant client) live under `hass_energy/lib/` to keep worker/API code lean.
- CLI accepts a YAML config (`--config`, default `config.yaml`) for static settings like host, port, and data_dir. Config is validated with Pydantic. Worker is always on; host/port flags were removed.
- Routes are split by domain under `hass_energy/api/routes/` (e.g., `plan`, `settings`). Settings endpoint surfaces runtime energy settings (read-only; user edits YAML).
- MILP logic lives under `hass_energy/worker/milp/` using PuLP; planner/compiler are placeholders awaiting real constraints.
- The MILP planner currently covers core grid/PV/load balance plus battery SOC (charge/discharge limits, efficiencies, reserve bounds); EV/deferrable constraints are still deferred for incremental rebuilds.
- The MILP planner supports EV charging with availability masks, optional target energy caps, and optional value per kWh in the objective; deferrable loads still deferred.
- Inverter export throughput is capped (PV after curtailment + battery discharge) using `inverter_export_limit_kw` from realtime inputs, defaulting to 10 kW.
- Inverter AC↔DC efficiency defaults to 95% each way and is applied on top of battery charge/discharge efficiencies (override via `inverter_charge_efficiency` / `inverter_discharge_efficiency` in realtime inputs).
- Import/export are disabled above/below price limits (default cap 0.50 AUD/kWh for import and floor 0.20 AUD/kWh for export; override with `import_price_cap` / `export_price_floor` in realtime inputs).
- A lightweight plan checker lives at `hass_energy/worker/milp/checker.py` with pytest coverage in `tests/`.
- `hass_energy/worker/milp/ha_dump.py` now emits a single-battery stub in realtime inputs when `battery_soc` is available (capacity/limits are currently constants).
- `hass_energy/worker/milp/ha_dump.py` emits a simple EV stub when `ev_connected` is true (defaults for capacity, target SOC, max power, value-per-kWh, min power, and switch penalty).

## Continuous learning
- When you learn new project knowledge, coding style, or preferences during a session, update `AGENTS.md` (and `README.md` if it affects users) before finishing so the next agent benefits.

## Ways of working
- Use uv for dependency management and scripts. Keep tooling config in `pyproject.toml` and `pyrightconfig.json`.
- Default to built-in exceptions unless a distinct custom type is justified.
- Keep backend and worker logic modular; API is FastAPI, worker is a background thread. Worker code lives in `hass_energy/worker/` so it can grow into multiple modules. Avoid tight coupling so the future frontend can live alongside the backend at the repo root.
- Persist config and runtime artifacts to the filesystem (`data_dir` from YAML config). The single YAML file (default `config.yaml`) stores server + energy settings; it is read once at startup and the API is read-only for config (no writes). Avoid destructive commands that would drop user data.
- Shared helpers (e.g., Home Assistant client) live under `hass_energy/lib/` to keep worker/API code lean.
- CLI accepts a YAML config (`--config`, default `config.yaml`) for static settings like host, port, and data_dir. Config is validated with Pydantic. Worker is always on; host/port flags were removed.
- Routes are split by domain under `hass_energy/api/routes/` (e.g., `plan`, `settings`). Settings endpoint surfaces runtime energy settings (read-only; user edits YAML).
- MILP logic lives under `hass_energy/worker/milp/` using PuLP; planner/compiler are placeholders awaiting real constraints.

## Continuous learning
- When you learn new project knowledge, coding style, or preferences during a session, update `AGENTS.md` (and `README.md` if it affects users) before finishing so the next agent benefits.

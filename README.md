## hass-energy

Energy management system that pulls Home Assistant data, plans using MILP (stub for now), and exposes a FastAPI server with a background worker.

### What lives here
- `src/hass_energy/api`: FastAPI app and routes to manage config, trigger plans, and report health.
- `src/hass_energy/worker/`: Background loop package that fetches Home Assistant data and builds a plan.
- `src/hass_energy/worker/milp/`: PuLP-backed planner and compiler placeholder for MILP constraints.
- `src/hass_energy/config.py`: YAML-backed configuration models and store (server + energy settings).
- `src/hass_energy/lib/home_assistant.py`: Thin HTTP client wrapper for Home Assistant APIs.
- Frontend: not yet built; the repository root is kept flat so a `frontend/` or similar can be added later.

### Getting started
1) Install uv if you do not have it: `pip install uv`.
2) Install dependencies: `uv sync --all-extras --dev`.
3) Create a YAML config (default `config.yaml`, validated via Pydantic):
   ```yaml
   host: 0.0.0.0
   port: 8000
   data_dir: ./data
   energy:
     forecast_window_hours: 24
     poll_interval_seconds: 300
     home_assistant:
       base_url: ""
       token: null
       verify_tls: true
   ```
4) Run the API + worker (always on): `uv run hass-energy --config config.yaml`.

### API surface (initial)
- `GET /health` – readiness probe.
- `GET /settings` – retrieve runtime energy settings (read-only; edit YAML to change).
- `POST /plan/trigger` – run a one-shot plan; returns stub payload while MILP solver is not yet wired.

### Configuration and data
- A single YAML file (default `config.yaml`) holds server settings and energy configuration. It is read once at startup; the API does not write config—edit the YAML directly.
- Plans are written to `<data_dir>/plans/latest.json` from the config file.

### Development
- Format/lint: `uv run ruff check src`
- Type check: `uv run pyright`
- Entry point: `uv run hass-energy`

### Roadmap placeholders
- Swap stub planner with a real MILP model (e.g., Pyomo/OR-Tools) once constraints and devices are defined.
- Add persistence backed by a proper database when filesystem storage becomes a bottleneck.
- Introduce a web client (TypeScript/React) alongside the backend in a sibling directory at the repo root.

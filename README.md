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
   server:
     host: 0.0.0.0
     port: 6070
     data_dir: ./data
   homeassistant:
     base_url: ""
     token: null
     verify_tls: true
     timeout_seconds: 30
   ems:
     timestep_minutes: 60
     min_horizon_minutes: 1440
   plant:
     grid:
       max_import_kw: 0.0
       max_export_kw: 0.0
       realtime_grid_power:
         type: home_assistant
         entity: sensor.grid_power
       realtime_price_import:
         type: home_assistant
         entity: sensor.price_import
       realtime_price_export:
         type: home_assistant
         entity: sensor.price_export
       price_import_forecast:
         type: home_assistant
         platform: amberelectric
         entity: sensor.price_import_forecast
       price_export_forecast:
         type: home_assistant
         platform: amberelectric
         entity: sensor.price_export_forecast
     load:
       realtime_load_power:
         type: home_assistant
         entity: sensor.load_power
       forecast:
         type: home_assistant
         platform: historical_average
         entity: sensor.load_power_15m
         history_days: 3
         interval_duration: 60
         # Optional when the current state has unit_of_measurement set.
         unit: W
         # Optional: repeat the daily profile for longer horizons.
         # forecast_horizon_hours: 48
     inverters: []
   loads: []
   ```
   Optional multi-resolution horizon (higher resolution near-term, then coarser):
   ```yaml
   ems:
     min_horizon_minutes: 120
     timestep_minutes: 30
     high_res_timestep_minutes: 5
     high_res_horizon_minutes: 120
   # Coarser intervals snap to their natural clock boundaries (e.g., 30-min slots on :00/:30).
   ```
4) Run the API + worker (always on): `uv run hass-energy --config config.yaml`.
5) Run the MILP v2 CLI (wired but not implemented yet): `uv run hass-energy milp --config config.yaml`.
6) Inspect load forecast hydration: `uv run hass-energy --config config.yaml hydrate-load-forecast`.

### Docker
Build and run a containerized EMS instance, build with:
```bash
docker build -t hass-energy .
```

Run with
```bash
docker run --rm -p 6070:6070 \
  -v "$(pwd)/config.yaml:/config/config.yaml:ro" \
  -v "$(pwd)/data:/data" \
  hass-energy
```
or docker-compose
```bash
docker compose up -d
```

Notes:
- Ensure `server.host` is `0.0.0.0` in `config.yaml` so the API binds inside the container.
- `data_dir` should point to `/data` so plans are persisted on the host volume.

### API surface (initial)
- `GET /health` – readiness probe.
- `GET /settings` – retrieve runtime energy settings (read-only; edit YAML to change).
- `POST /plan/trigger` – run a one-shot plan; returns stub payload while MILP solver is not yet wired.

### Configuration and data
- A single YAML file (default `config.yaml`) holds server settings, Home Assistant settings, plant definition, and energy settings. It is read once at startup; the API does not write config—edit the YAML directly.
- Plans are written to `<data_dir>/plans/latest.json` from the config file.

### Development
- Format/lint: `uv run ruff check src custom_components tests`
- Type check: `uv run pyright`
- Entry point: `uv run hass-energy`

### Roadmap placeholders
- Swap stub planner with a real MILP model (e.g., Pyomo/OR-Tools) once constraints and devices are defined.
- Add persistence backed by a proper database when filesystem storage becomes a bottleneck.
- Introduce a web client (TypeScript/React) alongside the backend in a sibling directory at the repo root.

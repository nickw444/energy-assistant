## Architecture

### Repository layout
- `src/hass_energy/api`: FastAPI app and routes to manage config, trigger plans, and report health.
- `src/hass_energy/worker/`: Background loop package that fetches Home Assistant data and builds a plan.
- `src/hass_energy/worker/milp/`: PuLP-backed planner and compiler placeholder for MILP constraints.
- `src/hass_energy/config.py`: YAML-backed configuration models and store (server + energy settings).
- `src/hass_energy/lib/home_assistant.py`: Thin HTTP client wrapper for Home Assistant APIs.
- Frontend: not yet built; the repository root is kept flat so a `frontend/` or similar can be added later.

### Configuration
A single YAML file (default `config.yaml`) holds server settings, Home Assistant settings, plant definition, and energy settings. It is read once at startup; the API does not write config—edit the YAML directly.

Example:
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
      unit: W
  inverters: []
loads: []
```

Optional multi-resolution horizon:
```yaml
ems:
  min_horizon_minutes: 120
  timestep_minutes: 30
  high_res_timestep_minutes: 5
  high_res_horizon_minutes: 120
```

Plans are written to `<data_dir>/plans/latest.json` from the config file.

### API surface (initial)
- `GET /health` – readiness probe.
- `GET /settings` – retrieve runtime energy settings (read-only; edit YAML to change).
- `POST /plan/trigger` – run a one-shot plan; returns stub payload while MILP solver is not yet wired.

### Docker notes
- Ensure `server.host` is `0.0.0.0` in `config.yaml` so the API binds inside the container.
- `data_dir` should point to `/data` so plans are persisted on the host volume.

### Development
- Format/lint: `uv run ruff check src custom_components tests`
- Type check: `uv run pyright`
- Entry point: `uv run hass-energy`

### EMS fixture workflows
- Capture a new scenario: `uv run hass-energy ems record-scenario --name <scenario-name>`
- Replay a recorded fixture: `uv run hass-energy ems solve --scenario <name-or-path>`
- Refresh fixture baselines: `uv run hass-energy ems refresh-baseline [--name <name-or-path>]`
- Generate a single-page report of all fixtures: `uv run hass-energy ems scenario-report`

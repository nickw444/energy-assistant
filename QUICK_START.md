# Quick Start

This guide walks through installing dependencies, creating a configuration file, and running the API + worker locally or in Docker.

## Prerequisites
- A Home Assistant instance with the sensors/entities you want to plan against.
- [uv](https://github.com/astral-sh/uv) installed (`pip install uv`).

## 1) Install dependencies
```bash
uv sync --all-extras --dev
```

## 2) Create `config.yaml`
Energy Assistant reads a single YAML config at startup. By default it looks for `config.yaml`, then `config.dev.yaml` if no path is provided. The API is read-only for config, so edit the YAML directly when settings change.

Use this starter configuration and replace the Home Assistant URLs/tokens/entities with your own:

```yaml
server:
  host: 0.0.0.0
  port: 6070
  data_dir: ./data
homeassistant:
  base_url: "http://homeassistant.local:8123"
  token: "YOUR_LONG_LIVED_ACCESS_TOKEN"
  verify_tls: true
  timeout_seconds: 30
ems:
  timestep_minutes: 60
  min_horizon_minutes: 1440
plant:
  grid:
    max_import_kw: 10.0
    max_export_kw: 10.0
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
  inverters:
    - id: inverter_1
      name: Main Inverter
      peak_power_kw: 5.0
      pv:
        forecast:
          type: home_assistant
          platform: solcast
          entities:
            - sensor.solcast_forecast_today
            - sensor.solcast_forecast_tomorrow
      battery:
        capacity_kwh: 13.5
        storage_efficiency_pct: 92
        charge_cost_per_kwh: 0.0
        discharge_cost_per_kwh: 0.0
        min_soc_pct: 10
        max_soc_pct: 100
        reserve_soc_pct: 20
        max_charge_kw: 5.0
        max_discharge_kw: 5.0
        state_of_charge_pct:
          type: home_assistant
          entity: sensor.battery_soc
        realtime_power:
          type: home_assistant
          entity: sensor.battery_power
loads:
  - id: ev_charger
    name: EV Charger
    load_type: controlled_ev
    min_power_kw: 1.4
    max_power_kw: 7.2
    energy_kwh: 40
    connected:
      type: home_assistant
      entity: binary_sensor.ev_connected
    realtime_power:
      type: home_assistant
      entity: sensor.ev_charger_power
    state_of_charge_pct:
      type: home_assistant
      entity: sensor.ev_soc
    switch_penalty: 0.1
```

### Optional: multi-resolution horizon
```yaml
ems:
  min_horizon_minutes: 120
  timestep_minutes: 30
  high_res_timestep_minutes: 5
  high_res_horizon_minutes: 120
```

## 3) Run the API + worker
```bash
uv run hass-energy --config config.yaml
```

### Verify it is running
```bash
curl http://localhost:6070/health
```

### Trigger and inspect a plan
```bash
curl -X POST http://localhost:6070/plan/run
```

```bash
curl http://localhost:6070/plan/latest
```

## Docker
Build and run a containerized instance:
```bash
docker build -t hass-energy .
```

```bash
docker run --rm -p 6070:6070 \
  -v "$(pwd)/config.yaml:/config/config.yaml:ro" \
  -v "$(pwd)/data:/data" \
  hass-energy
```

Or with compose:
```bash
docker compose up -d
```

### Docker config notes
- Ensure `server.host` is `0.0.0.0` in `config.yaml` so the API binds inside the container.
- Set `data_dir` to `/data` so plans are persisted on the host volume.

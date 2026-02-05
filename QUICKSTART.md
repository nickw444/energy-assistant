# Quick Start

Energy Assistant runs a FastAPI service plus a background planner. Everything is configured through a
single YAML file.

## Requirements
- Python 3.13.2+
- A Home Assistant instance and a long-lived access token (current data connector)
- Entity IDs for the sensors you want to use

## Install
1. Install `uv`: `pip install uv`
2. Install dependencies: `uv sync --all-extras --dev`

## Configure
1. Create `config.yaml` in the repo root (or pass `--config` to point elsewhere).
2. Fill in the configuration below with your Home Assistant URL, token, and entity IDs.

Full example (covers all available config options):

```yaml
server:
  host: 0.0.0.0
  port: 6070
  data_dir: ./data

homeassistant:
  base_url: http://homeassistant.local:8123
  token: "YOUR_LONG_LIVED_ACCESS_TOKEN"
  verify_tls: true
  timeout_seconds: 30

ems:
  timestep_minutes: 30
  min_horizon_minutes: 1440
  high_res_timestep_minutes: 5
  high_res_horizon_minutes: 120
  terminal_soc:
    mode: adaptive
    penalty_per_kwh: median

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
      entity: sensor.amber_price_import_forecast
      price_forecast_mode: blend_mean
    price_export_forecast:
      type: home_assistant
      platform: amberelectric
      entity: sensor.amber_price_export_forecast
      price_forecast_mode: blend_mean
    grid_price_bias_pct: 5.0
    import_forbidden_periods:
      - start: "16:00"
        end: "21:00"
        months: [jan, feb, mar]
  load:
    realtime_load_power:
      type: home_assistant
      entity: sensor.house_load_power
    forecast:
      type: home_assistant
      platform: historical_average
      entity: sensor.house_load_power
      history_days: 7
      interval_duration: 30
      unit: W
      forecast_horizon_hours: 48
      realtime_window_minutes: 30
  inverters:
    - id: main_inverter
      name: Main Inverter
      peak_power_kw: 5.0
      curtailment: load-aware
      pv:
        realtime_power:
          type: home_assistant
          entity: sensor.pv_power
        forecast:
          type: home_assistant
          platform: solcast
          entities:
            - sensor.solcast_forecast_today
            - sensor.solcast_forecast_tomorrow
      battery:
        capacity_kwh: 13.5
        storage_efficiency_pct: 90
        charge_cost_per_kwh: 0.01
        discharge_cost_per_kwh: 0.02
        soc_value_per_kwh: 0.1
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
    name: Garage EV Charger
    load_type: controlled_ev
    min_power_kw: 1.4
    max_power_kw: 7.2
    energy_kwh: 40
    connected:
      type: home_assistant
      entity: binary_sensor.ev_connected
    can_connect:
      type: home_assistant
      entity: binary_sensor.ev_can_connect
    allowed_connect_times:
      - start: "22:00"
        end: "07:00"
        months: [apr, may, jun, jul, aug, sep]
    connect_grace_minutes: 15
    realtime_power:
      type: home_assistant
      entity: sensor.ev_charger_power
    state_of_charge_pct:
      type: home_assistant
      entity: sensor.ev_soc
    soc_incentives:
      - target_soc_pct: 80
        incentive: 5.0
    switch_penalty: 0.25
  - id: always_on
    name: Always-on Base Load
    load_type: nonvariable_load
```

## Run
1. Start the API + worker: `uv run energy-assistant --config config.yaml`
2. Trigger a plan run: `curl -X POST http://localhost:6070/plan/run`
3. Fetch the latest plan: `curl http://localhost:6070/plan/latest`

Notes:
- The worker runs immediately at startup, then at least every minute, and also after price changes.
- `months` must use 3-letter abbreviations (`jan`..`dec`).
- `homeassistant.base_url` should include `http://` or `https://`.
- Set `homeassistant.verify_tls: false` if you use a self-signed certificate.
- If you omit `--config`, the CLI looks for `config.yaml` and then `config.dev.yaml`.
- All sources use `type: home_assistant` today; `platform` selects forecast providers.
- If you do not have PV/batteries or flexible loads, set `inverters: []` and/or `loads: []`.
- `data_dir` is created automatically if it does not exist.

## Docker
1. Build the image: `docker build -t energy-assistant .`
2. Set `server.host: 0.0.0.0` and `server.data_dir: /data` in `config.yaml`.
3. Run the container:

```bash
docker run --rm -p 6070:6070 \
  -v "$(pwd)/config.yaml:/config/config.yaml:ro" \
  -v "$(pwd)/data:/data" \
  energy-assistant
```

Or with compose:

```bash
docker compose up -d
```

## Optional Home Assistant integration
A Home Assistant custom integration (early POC) lives in `custom_components/energy_assistant` and can
surface plans back into HA. It is optional and separate from the core service.

# Quick Start

Energy Assistant runs a FastAPI service plus a background planner. Everything is configured through a
single YAML file.

## Requirements
- Python 3.13.2+
- A Home Assistant instance and a long-lived access token
- Entity IDs for the sensors you want to use

## Install
1. Install `uv`: `pip install uv`
2. Install dependencies: `uv sync --dev`

## Configure
1. Create `config.yaml` in the repo root (or pass `--config` to point elsewhere).
2. Fill in the configuration below with your Home Assistant URL, token, and entity IDs.

Representative example:

```yaml
# API and worker runtime settings.
server:
  # Bind address for the FastAPI service.
  host: 0.0.0.0
  # Port for the FastAPI service.
  port: 6070
  # Directory for runtime artifacts such as plan JSON, plots, and reports.
  data_dir: ./data

# Home Assistant connection details.
homeassistant:
  # Base URL for Home Assistant (include http/https).
  base_url: http://homeassistant.local:8123
  # Long-lived access token.
  token: "YOUR_LONG_LIVED_ACCESS_TOKEN"
  # Set false for self-signed certs.
  verify_tls: true
  # Request timeout for HA calls.
  timeout_seconds: 30

# EMS planning configuration.
ems:
  # Horizon can be multi-resolution: use smaller slots near now for responsive control,
  # then switch to the base timestep to keep long horizons tractable.
  # The high-res window runs first and the transition snaps to the next base boundary
  # (e.g. 30-min slots on :00/:30) so coarse slots stay aligned to the clock.
  timestep_minutes: 30
  # Fixed rolling horizon length to plan for.
  horizon_minutes: 1440
  # Higher-resolution timestep at the start of the horizon.
  high_res_timestep_minutes: 5
  # Duration of the high-resolution window.
  high_res_horizon_minutes: 120

# EMS input registry. These are reusable typed inputs consumed by the flat
# plant registry below.
inputs:
  grid_price_import:
    # Input kinds:
    # - scalar: a single realtime value.
    # - forecast: a horizon-aligned series.
    type: forecast
    forecast:
      type: home_assistant
      # Price forecast providers: amberelectric | amber_express.
      platform: amber_express
      entity: sensor.price_import
    # Optional realtime scalar used to replace slot 0.
    realtime:
      type: home_assistant
      entity: sensor.price_import
    # Optional history-based tail extension for short provider forecasts.
    forecast_expansion:
      history_days: 7
      # Must evenly divide 60.
      interval_duration: 30

  grid_price_export:
    type: forecast
    forecast:
      type: home_assistant
      platform: amber_express
      entity: sensor.price_export
    realtime:
      type: home_assistant
      entity: sensor.price_export
    forecast_expansion:
      history_days: 7
      interval_duration: 30

  base_load_power:
    type: forecast
    forecast:
      type: home_assistant
      # Power forecast providers: historical_average | solcast.
      platform: historical_average
      entity: sensor.energy_assistant_load_power_uncontrolled_smoothed_15m
      history_days: 7
      interval_duration: 30
      unit: W
      forecast_horizon_hours: 49
      realtime_window_minutes: 30
    realtime:
      type: home_assistant
      entity: sensor.energy_assistant_load_power_uncontrolled_smoothed_1m

  pv_main_forecast:
    type: forecast
    forecast:
      type: home_assistant
      platform: solcast
      entities:
        - sensor.solcast_pv_forecast_forecast_today
        - sensor.solcast_pv_forecast_forecast_tomorrow
        - sensor.solcast_pv_forecast_forecast_day_3
    realtime:
      type: home_assistant
      entity: sensor.energy_assistant_pv_power_smoothed_1m

  battery_soc:
    type: scalar
    value_kind: percentage
    source:
      type: home_assistant
      entity: sensor.battery_soc

  battery_power:
    type: scalar
    value_kind: power
    source:
      type: home_assistant
      entity: sensor.energy_assistant_battery_power_smoothed_1m

  ev_connected:
    type: scalar
    value_kind: boolean
    source:
      type: home_assistant
      entity: binary_sensor.ev_connected

  ev_can_connect:
    type: scalar
    value_kind: boolean
    source:
      type: home_assistant
      entity: binary_sensor.ev_can_connect

  ev_power:
    type: scalar
    value_kind: power
    source:
      type: home_assistant
      entity: sensor.ev_charger_power

  ev_soc:
    type: scalar
    value_kind: percentage
    source:
      type: home_assistant
      entity: sensor.ev_soc

# Flat plant registry. Keys are component ids. `connection` points to another
# plant component id, and the component types define the attachment semantics:
# - grid/load/EV connect to switchboard AC
# - inverter connects AC to switchboard
# - battery/PV connect to inverter DC
plant:
  switchboard:
    type: switchboard

  grid:
    type: grid
    connection: switchboard
    constraints:
      max_import_kw: 10.0
      max_export_kw: 10.0
    price_import:
      source: inputs.grid_price_import
      filters:
        - type: bias
          bias_pct: 25.0
        - type: risk
          bias_pct: 25.0
          ramp_start_after_minutes: 30
          ramp_duration_minutes: 120
          curve: linear
          # import_price_floor: -0.05
    price_export:
      source: inputs.grid_price_export
      filters:
        - type: bias
          bias_pct: 25.0
        - type: risk
          bias_pct: 25.0
          ramp_start_after_minutes: 30
          ramp_duration_minutes: 120
          curve: linear
          export_price_ceiling: 10.0
    # Prefer exporting rather than curtailing when export price is effectively zero.
    zero_price_export_preference: export
    import_forbidden_periods:
      - start: "16:00"
        end: "21:00"
        months: [jan, feb, mar]

  base_load:
    type: load
    connection: switchboard
    name: Base Load
    power: inputs.base_load_power

  main_inverter:
    type: inverter
    connection: switchboard
    name: Main Inverter
    peak_power_kw: 5.0
    # Curtailment modes: binary | load-aware.
    curtailment: load-aware

  main_pv:
    type: pv
    connection: main_inverter
    name: Main PV
    forecast: inputs.pv_main_forecast
    # Optional pessimism factor applied to forecast only.
    forecast_multiplier: 1.0

  main_battery:
    type: battery
    connection: main_inverter
    name: Main Battery
    capacity_kwh: 13.5
    storage_efficiency_pct: 95
    charge_cost_per_kwh: 0.02
    discharge_cost_per_kwh: 0.02
    soc_value_per_kwh: 0.06
    min_soc_pct: 10
    max_soc_pct: 100
    reserve_soc_pct: 20
    # Terminal state-of-charge handling.
    # Keeps the optimizer from draining the battery at the end of the horizon and
    # assuming "tomorrow is free." Adaptive mode exists because horizons shorter
    # or longer than a day can make a hard end-SoC target unrealistic; it relaxes
    # toward reserve using a fixed 24h reference and prices any shortfall so energy
    # still has value.
    terminal_soc:
      # Mode options:
      # - hard: enforce end SoC >= start SoC.
      # - adaptive: relax toward reserve using the 24h reference scaling.
      mode: adaptive
      # Penalty applied per kWh of terminal SoC shortfall when adaptive slack is used.
      # The objective adds `penalty_per_kwh * shortfall_kwh`, scaled by the adaptive
      # horizon ratio, so missing energy is priced rather than ignored.
      # Options:
      # - "median": median import price (default).
      # - "mean": average import price.
      # - number: explicit $/kWh penalty.
      penalty_per_kwh: median
    max_charge_kw: 5.0
    max_discharge_kw: 5.0
    state_of_charge_pct: inputs.battery_soc
    realtime_power: inputs.battery_power

  ev_charger:
    type: load_controlled_ev
    connection: switchboard
    name: Garage EV Charger
    min_power_kw: 1.4
    max_power_kw: 7.2
    energy_kwh: 40
    connected: inputs.ev_connected
    can_connect: inputs.ev_can_connect
    allowed_connect_times:
      - start: "22:00"
        end: "07:00"
    connect_grace_minutes: 60
    realtime_power: inputs.ev_power
    state_of_charge_pct: inputs.ev_soc
    soc_incentives:
      - target_soc_pct: 40
        incentive: 0.20
      - target_soc_pct: 60
        incentive: 0.08
      - target_soc_pct: 80
        incentive: 0.04
      - target_soc_pct: 100
        incentive: 0.0
    switch_penalty: 0.02
```

## Run
1. Start the API + worker: `uv run energy-assistant --config config.yaml`
2. Trigger a plan run: `curl -X POST http://localhost:6070/plan/run`
3. Fetch the latest plan: `curl http://localhost:6070/plan/latest`

Notes:
- The worker runs immediately at startup, then on a roughly one-minute fallback schedule, and also after watched price changes.
- `months` must use 3-letter abbreviations (`jan`..`dec`).
- `homeassistant.base_url` should include `http://` or `https://`.
- Set `homeassistant.verify_tls: false` if you use a self-signed certificate.
- If you omit `--config`, the CLI looks for `config.yaml` and then `config.dev.yaml`.
- All sources use `type: home_assistant` today; `platform` selects forecast providers.
- `inputs` is the only place that defines data sources. `plant` only references `inputs.*`.
- String input references like `inputs.grid_price_import` are a shortcut for `{ source: inputs.grid_price_import }`.
- A typical plant has one switchboard, grid, and base load. The plant registry can also include multiple grids, PV arrays, batteries, and EV loads when they are wired to compatible parent components.
- `data_dir` is created automatically if it does not exist.

## Home Assistant Helpers
The plan is more stable when realtime power sensors are smoothed and when load
excludes controlled loads (EVs, etc). Below is an example set of template and
filter sensors that matches the naming used in the quickstart config above.
Adjust `entity_id` values to match your installation.

```yaml
# HASS Energy sensors for smoothing and controlled-load calculations.
template:
  - binary_sensor:
      - name: "Energy Assistant Tessie Can Connect"
        unique_id: energy_assistant_tessie_can_connect
        device_class: presence
        icon: mdi:car
        state: >-
          {{
            is_state('device_tracker.tessie', 'home')
            and is_state('group.all_people', 'home')
          }}
      - name: "Energy Assistant Tessie Connected at Home"
        unique_id: energy_assistant_tessie_connected_at_home
        device_class: connectivity
        icon: mdi:ev-station
        state: >-
          {{
            is_state('device_tracker.tessie', 'home')
            and is_state('binary_sensor.tesla_wall_connector_vehicle_connected', 'on')
          }}
  - sensor:
      - name: "energy_assistant_controlled_loads_power"
        unique_id: energy_assistant_controlled_loads_power
        device_class: power
        unit_of_measurement: W
        state_class: measurement
        availability: >-
          {{ states('sensor.tesla_wall_connector_power') not in ['unknown', 'unavailable', 'none'] }}
        state: >-
          {% set ev_power = states('sensor.tesla_wall_connector_power') | float(0) %}
          {{ ev_power }}
      - name: "energy_assistant_load_power_uncontrolled"
        unique_id: energy_assistant_load_power_uncontrolled
        device_class: power
        unit_of_measurement: W
        state_class: measurement
        availability: >-
          {{
            states('sensor.inverter_load_power') not in ['unknown', 'unavailable', 'none']
            and states('sensor.energy_assistant_controlled_loads_power') not in ['unknown', 'unavailable', 'none']
          }}
        state: >-
          {% set total = states('sensor.inverter_load_power') | float(0) %}
          {% set controlled = states('sensor.energy_assistant_controlled_loads_power') | float(0) %}
          {{ [total - controlled, 0] | max }}

sensor:
  - platform: filter
    name: "energy_assistant_grid_power_smoothed_1m"
    unique_id: "energy_assistant_grid_power_smoothed_1m"
    entity_id: sensor.inverter_grid_meter_power
    filters:
      - filter: time_simple_moving_average
        window_size: "00:01"
        precision: 2

  - platform: filter
    name: "energy_assistant_load_power_uncontrolled_smoothed_1m"
    unique_id: "energy_assistant_load_power_uncontrolled_smoothed_1m"
    entity_id: sensor.energy_assistant_load_power_uncontrolled
    filters:
      - filter: time_simple_moving_average
        window_size: "00:01"
        precision: 2

  - platform: filter
    name: "energy_assistant_load_power_uncontrolled_smoothed_15m"
    unique_id: "energy_assistant_load_power_uncontrolled_smoothed_15m"
    entity_id: sensor.energy_assistant_load_power_uncontrolled
    filters:
      - filter: time_simple_moving_average
        window_size: "00:15"
        precision: 2

  - platform: filter
    name: "energy_assistant_pv_power_smoothed_1m"
    unique_id: "energy_assistant_pv_power_smoothed_1m"
    entity_id: sensor.inverter_pv_total_power
    filters:
      - filter: time_simple_moving_average
        window_size: "00:01"
        precision: 2

  - platform: filter
    name: "energy_assistant_battery_power_smoothed_1m"
    unique_id: "energy_assistant_battery_power_smoothed_1m"
    entity_id: sensor.inverter_battery_power
    filters:
      - filter: time_simple_moving_average
        window_size: "00:01"
        precision: 2
```

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
docker compose -f docker-compose.example.yml up -d
```

## Optional Home Assistant integration
A Home Assistant custom integration (early POC) lives in `custom_components/energy_assistant` and can
surface plans back into HA. It also exposes a button entity that triggers `/plan/run`. It is optional
and separate from the core service.

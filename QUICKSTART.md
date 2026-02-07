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
# API + worker runtime settings.
server:
  # Bind address for the FastAPI service.
  host: 0.0.0.0
  # Port for the FastAPI service.
  port: 6070
  # Directory for runtime artifacts (plans, logs, etc).
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
  # Minimum horizon length to plan for.
  min_horizon_minutes: 1440
  # Higher-resolution timestep at the start of the horizon.
  high_res_timestep_minutes: 5
  # Duration of the high-resolution window.
  high_res_horizon_minutes: 120
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

# Plant topology and constraints.
plant:
  # Grid connection configuration.
  grid:
    # Import/export limits at the grid connection.
    max_import_kw: 10.0
    max_export_kw: 10.0
    # Realtime grid power should use a smoothed sensor (recommend 1m mean filter)
    # so short spikes do not thrash the plan.
    realtime_grid_power:
      type: home_assistant
      entity: sensor.energy_assistant_grid_power_smoothed_1m
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
      # Forecast post-processing mode options:
      # - spot, advanced, blend_min, blend_max, blend_mean (or omit for provider default).
      price_forecast_mode: spot
    # amber-express example:
    # realtime_price_import:
    #   type: home_assistant
    #   entity: sensor.price_import
    # price_import_forecast:
    #   type: home_assistant
    #   platform: amber_express
    #   entity: sensor.price_import
    price_export_forecast:
      type: home_assistant
      platform: amberelectric
      entity: sensor.amber_price_export_forecast
      # Forecast post-processing mode options:
      # - spot, advanced, blend_min, blend_max, blend_mean (or omit for provider default).
      price_forecast_mode: spot
    # amber-express example:
    # realtime_price_export:
    #   type: home_assistant
    #   entity: sensor.price_export
    # price_export_forecast:
    #   type: home_assistant
    #   platform: amber_express
    #   entity: sensor.price_export
    # Bias grid prices to prefer self-sufficiency by making imports more expensive
    # and exports less attractive by the same margin (e.g. 25% turns $0.20 import
    # into $0.25 and $0.10 export into $0.075).
    grid_price_bias_pct: 25.0
    # Forecast price risk configuration.
    # Price forecasts get less reliable the further out you plan, so this can
    # ramp in a risk bias after a short delay to avoid over-optimizing on
    # distant spikes.
    grid_price_risk:
      # Forecast risk bias applied over the horizon (%).
      bias_pct: 25.0
      # Delay before the risk ramp begins.
      ramp_start_after_minutes: 30
      # Duration of the ramp to full risk bias.
      ramp_duration_minutes: 120
      # Curve options: linear (only option today).
      curve: linear
      # Optional clamp on import prices before scaling (useful to treat extreme
      # forecast spikes as "good enough" rather than waiting for ever-higher
      # prices that may not materialize because wholesale bidding often flattens
      # those peaks).
      # import_price_floor: -0.05
      # Optional clamp on export prices before scaling (prevents the planner
      # from delaying exports in hopes of higher forecast peaks that never
      # arrive due to wholesale bidding volatility).
      export_price_ceiling: 10.0
    # Windows where grid import is disallowed.
    # Useful for demand-tariff windows where any import can trigger a high
    # monthly charge that is difficult to model directly in the objective.
    import_forbidden_periods:
      - start: "16:00"
        end: "21:00"
        # Omit months to apply year-round.
        months: [jan, feb, mar]
  # Site load configuration.
  load:
    # Realtime load should be an "uncontrolled" sensor (exclude controlled loads
    # defined below) and smoothed (recommend 1m mean filter sensor).
    realtime_load_power:
      type: home_assistant
      entity: sensor.energy_assistant_load_power_uncontrolled_smoothed_1m
    # Load forecast configuration (historical_average computes a time-of-day
    # profile from the last `history_days`, then repeats it across the horizon).
    # Use a smoothed history sensor (recommend 15m mean) of the uncontrolled load.
    forecast:
      type: home_assistant
      platform: historical_average
      entity: sensor.energy_assistant_load_power_uncontrolled_smoothed_15m
      history_days: 7
      interval_duration: 30
      unit: W
      forecast_horizon_hours: 48
      # Blend the current realtime load into the next N minutes so recent spikes
      # are reflected in the near-term forecast (only increases the forecast).
      realtime_window_minutes: 30
  # Inverter configuration.
  inverters:
    - id: main_inverter
      name: Main Inverter
      peak_power_kw: 5.0
      # Curtailment options:
      # - null: no curtailment, PV must follow forecast.
      # - binary: PV is either fully on or fully off each slot.
      # - load-aware: PV can be reduced to serve load and export is blocked when curtailing.
      curtailment: load-aware
      # PV configuration.
      pv:
        realtime_power:
          type: home_assistant
          entity: sensor.energy_assistant_pv_power_smoothed_1m
        # Optional provider-agnostic scaling applied to PV forecast kW values (pessimism factor).
        # Default: 1.0 (unchanged). Example: 0.90 = 10% derate.
        # Applies to forecast only (slot-0 realtime override not scaled).
        # forecast_multiplier: 0.90
        forecast:
          type: home_assistant
          platform: solcast
          entities:
            - sensor.solcast_pv_forecast_forecast_today
            - sensor.solcast_pv_forecast_forecast_tomorrow
            - sensor.solcast_pv_forecast_forecast_day_3
      battery:
        capacity_kwh: 13.5
        storage_efficiency_pct: 95
        charge_cost_per_kwh: 0.02
        discharge_cost_per_kwh: 0.02
        # Terminal value of stored energy (reward for ending with more SoC).
        # Added in PR #103 to nudge charging when export prices are low; this is
        # a *reward* for extra energy, unlike `ems.terminal_soc` which penalizes
        # falling short of a return target.
        soc_value_per_kwh: 0.06
        min_soc_pct: 10
        max_soc_pct: 100
        # Reserve SoC below which grid export from the battery is blocked
        # (self-consumption can still discharge to min_soc_pct). Use this to
        # keep headroom for uncertainty, price spikes, or outages.
        reserve_soc_pct: 20
        # Max charge rate (kW). Set lower for battery limits or higher than the
        # inverter AC rating when PV is DC-coupled and can charge faster than AC export.
        max_charge_kw: 5.0
        max_discharge_kw: 5.0
        state_of_charge_pct:
          type: home_assistant
          entity: sensor.battery_soc
        realtime_power:
          type: home_assistant
          entity: sensor.energy_assistant_battery_power_smoothed_1m

# Controllable and non-variable loads.
loads:
  - id: ev_charger
    name: Garage EV Charger
    # Load type options: controlled_ev | nonvariable_load.
    load_type: controlled_ev
    # Charging power bounds and target energy.
    min_power_kw: 1.4
    max_power_kw: 7.2
    energy_kwh: 40
    connected:
      type: home_assistant
      entity: binary_sensor.ev_connected
    can_connect:
      type: home_assistant
      entity: binary_sensor.ev_can_connect
    # Allowed connection windows for when the EV *can be plugged in*; if the car
    # is not currently connected (and `can_connect` is true), the planner can
    # still schedule charging inside these windows so a human can plug it in.
    # If omitted, the EV is allowed to connect any time after the grace period.
    allowed_connect_times:
      # Example overnight window.
      - start: "22:00"
        end: "07:00"
    # Grace period before assuming the EV could be connected (minutes).
    # With 60, the earliest planned charge is 60 minutes from now (120 => 2 hours).
    connect_grace_minutes: 60
    realtime_power:
      type: home_assistant
      entity: sensor.ev_charger_power
    state_of_charge_pct:
      type: home_assistant
      entity: sensor.ev_soc
    # Target SoC incentives ($/kWh) are piecewise rewards: each target defines a
    # segment of energy above the current SoC, and the reward applies per kWh
    # charged within that segment (higher targets can have lower rewards).
    soc_incentives:
      - target_soc_pct: 40
        incentive: 0.20
      - target_soc_pct: 60
        incentive: 0.08
      - target_soc_pct: 80
        incentive: 0.04
      - target_soc_pct: 100
        incentive: 0.0
    # Penalty for switching the load on/off.
    switch_penalty: 0.02
    # Optional: soft SoC target by a specific deadline datetime.
    # If the deadline is not reachable, the solver will do its best (it will not
    # fail the plan).
    #
    # `max_cost_per_kwh` is a willingness-to-pay cap expressed as a penalty per
    # kWh of shortfall at the deadline. When set, the solver will generally
    # avoid paying more than this (in objective terms) to reduce shortfall.
    deadline_target:
      at: "2026-02-07T07:30:00-08:00"
      target_soc_pct: 80
      max_cost_per_kwh: 0.25
  # Non-variable loads are not fully supported yet; placeholder:
  # - id: always_on
  #   name: Always-on Base Load
  #   load_type: nonvariable_load
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
docker compose up -d
```

## Optional Home Assistant integration
A Home Assistant custom integration (early POC) lives in `custom_components/energy_assistant` and can
surface plans back into HA. It is optional and separate from the core service.

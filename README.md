# hass-energy

Base uv-managed CLI skeleton for the `hass-energy` tool. The CLI is powered by Click and ready for additional modes (e.g., daemonised run, training, etc.).

## Getting started

```bash
uv sync
uv run hass-energy --help
uv run hass-energy --config path/to/config.yaml validate-config
uv run hass-energy --config path/to/config.yaml test-connection
uv run hass-energy --config path/to/config.yaml hass list-entities
uv run hass-energy --config path/to/config.yaml hass get-states sensor.power_lounge light.kitchen
```

## Configuration

Create a YAML config file (for future CLI modes) with Home Assistant details:

```yaml
home_assistant:
  base_url: "https://your-ha.local"
  token: "env:HASS_TOKEN"  # or inline the token string
  verify_ssl: true
  ws_max_size: 8388608  # optional websocket frame size (bytes); null means no limit
mapper:
  module: "example_mapper:ExampleMapper"  # importable module on PYTHONPATH/current dir
  # attribute: "get_mapper"  # optional attribute name (defaults: get_mapper, mapper, Mapper)
datalogger:
  triggers:
    - "sensor.inverter_meter_power"
    - "sensor.amber_general_price"
# Or point at a local file:
# mapper:
#   module: "./example_mapper.py:ExampleMapper"
```

See `hass-energy.example.config.yaml` for a template. The parser is Pydantic-based and supports `env:` prefixes for secrets. Use `uv run hass-energy validate-config path/to/config.yaml` to validate configs, or `hass_energy.config.load_config(path)` in code before connecting to Home Assistant.
Provide `--config` at the CLI root so subcommands share the loaded config: `uv run hass-energy --config path/to/config.yaml <command>`. The CLI loads and validates the config up front; `validate-config` simply confirms the loaded file.

### Connection test

`uv run hass-energy --config path/to/config.yaml test-connection` will establish a WebSocket connection to Home Assistant, authenticate using the provided token, and report the detected Home Assistant version.

The websocket client (`hass_energy.ha_client.HomeAssistantWebSocketClient`) is stateful and will be extended for subscriptions and state queries.

### Home Assistant data helpers

- `uv run hass-energy --config path/to/config.yaml hass list-entities` – list all entity IDs.
- `uv run hass-energy --config path/to/config.yaml hass get-states <entity_id...>` – print states for provided entities.

### Datalogger

Continuously log snapshots for a set of entities whenever any trigger entity changes:

```bash
uv run hass-energy --config path/to/config.yaml datalogger \
  --entity sensor.power_lounge --entity light.kitchen \
  --trigger binary_sensor.motion_hallway \
  --output-dir ./logs/datalogger \
  --debounce 2.5
```

- Trigger entity changes kick off a snapshot of all `--entity` targets after the debounce period (default 2 seconds).
- Each trigger creates a JSON file in the output directory containing `captured_at`, `entities`, `trigger`, and `states`.
- Set `--debounce 0` to log immediately on the first trigger event.
- If `--trigger` is omitted, the datalogger falls back to `datalogger.triggers` in the config.
- If `--entity` is omitted and a mapper is configured, the datalogger will default to the mapper's `required_entities`.

### Mapper

Mappers convert Home Assistant states into a flattened structure for downstream processing. Configure the mapper module in your YAML (relative paths resolve from the config file directory). The mapper module must expose an object (instance, class, or factory) implementing the `HassEnergyMapper` protocol (`required_entities() -> list[str]`, `map(states: dict) -> dict`).

Example mapper reference in config:

```yaml
mapper:
  module: "example_mapper:ExampleMapper"
  # attribute: "get_mapper"  # optional; defaults to get_mapper, mapper, or Mapper
```

An example implementation lives at `example_mapper.py` and maps `sensor.inverter_meter_power` and `sensor.amber_general_price` into a small flattened dict. Use `hass_energy.mapper.load_mapper(mapper_config, config_path)` to load the mapper from code.
The mapper section is required; `run-mapper` and `datalogger` rely on it when `--entity` is omitted.

Run the configured mapper once and print the output:

```bash
uv run hass-energy --config path/to/config.yaml run-mapper
```

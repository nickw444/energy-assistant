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
```

See `hass-energy.example.config.yaml` for a template. The parser is Pydantic-based and supports `env:` prefixes for secrets. Use `uv run hass-energy validate-config path/to/config.yaml` to validate configs, or `hass_energy.config.load_config(path)` in code before connecting to Home Assistant.
Provide `--config` at the CLI root so subcommands share the loaded config: `uv run hass-energy --config path/to/config.yaml <command>`. The CLI loads and validates the config up front; `validate-config` simply confirms the loaded file.

### Connection test

`uv run hass-energy --config path/to/config.yaml test-connection` will establish a WebSocket connection to Home Assistant, authenticate using the provided token, and report the detected Home Assistant version.

The websocket client (`hass_energy.ha_client.HomeAssistantWebSocketClient`) is stateful and will be extended for subscriptions and state queries.

### Home Assistant data helpers

- `uv run hass-energy --config path/to/config.yaml hass list-entities` – list all entity IDs.
- `uv run hass-energy --config path/to/config.yaml hass get-states <entity_id...>` – print states for provided entities.

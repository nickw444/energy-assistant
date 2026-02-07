## Home Assistant Integration (custom_components)

Scope: `custom_components/energy_assistant/`.
Assumes repo-wide conventions in the repo-root `AGENTS.md`.

Architecture:
- Config entry setup is the only supported path.
- `entry.runtime_data` stores a shared `EnergyAssistantApiClient`, coordinator, and `base_url`.
- Coordinator uses long-polling (`/plan/await`) to receive plan updates quickly. The `DataUpdateCoordinator` `update_interval` fetches `/plan/latest` as a safety-net when long-polling times out or is failing.
- Device model is a root "Plant" plus per-inverter and per-load subdevices keyed by stable IDs.

Conventions:
- Keep HTTP/API details inside `custom_components/energy_assistant/energy_assistant_client/` and `coordinator.py`.
- Prefer typed model access over dynamic field traversal in entities.
- Use `_unrecorded_attributes` for large plan arrays to avoid bloating the recorder database.
- If the FastAPI contract changes, update `custom_components/energy_assistant/energy_assistant_client/models.py` to match.

Key files:
- `custom_components/energy_assistant/coordinator.py` plan polling and plan-series helpers.
- `custom_components/energy_assistant/energy_assistant_client/client.py` aiohttp API client.
- `custom_components/energy_assistant/device.py` device registry helpers.

## Continuous learning
- Update this file when integration architecture or patterns change (coordinator strategy, device model, client layering).
- Put entity-specific quirks and HA platform nuances next to the relevant code (for example, `sensor.py`) as comments instead of expanding this doc.

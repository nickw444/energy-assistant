## Home Assistant integration notes
- Config entry setup is the only supported path
- `entry.runtime_data` stores a shared `EnergyAssistantApiClient`, `EnergyAssistantCoordinator`, and
  `base_url`.
- Device identifiers include the server URL so multiple config entries stay distinct.
- Inverter/battery sensors attach to per-inverter subdevices keyed by inverter ID.
- Load sensors (for EVs) attach to per-load subdevices keyed by load ID.
- Root device is named "Plant" to represent the site-level controller.
- Curtailment is exposed as a binary sensor and includes plan series attributes.
- Shared device registry helpers live in `custom_components/energy_assistant/device.py`.
- Shared plan helpers (timestep lookup, plan series) live in `custom_components/energy_assistant/coordinator.py`.
- Coordinator uses long-polling (`/plan/await`) to receive plan updates immediately when prices change; falls back to `get_latest_plan()` on timeout or error.
- Prefer typed model access; avoid dynamic field traversal in sensors.
- Use `_unrecorded_attributes` for large plan arrays to keep them out of the recorder DB.

![Hass Energy Logo](docs/assets/logo-text.png)

## Energy Assistant

Home energy management that connects to your sensors, builds a plan, and exposes a small API so you can automate how your home imports, exports, and stores energy. Home Assistant is the only connected platform today.

### What it does
- Pulls live and forecast data from Home Assistant (current integration).
- Builds a plan for grid import/export and device usage.
- Runs a lightweight API for health checks and plan triggers.

![Example Plan](docs/assets/example-plan.png)

### Home Assistant integration
Energy Assistant includes a Home Assistant integration (early POC) that surfaces plans as entities so you can automate with HA. It lets you view plan outputs and trigger new plans directly from your HA dashboard.

### Status
This is early, unreleased software. The planner is wired but still evolving, so outputs should be treated as experimental.

### Quickstart
1. Install uv: `pip install uv`.
2. Install dependencies: `uv sync --all-extras --dev`.
3. Create a `config.yaml` (see the example in `README_DEV.md`).
4. Run the API + worker: `uv run hass-energy --config config.yaml`.

### Docker
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

### Documentation
Architecture, configuration schema, and developer workflows live in `README_DEV.md`.

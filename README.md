![Hass Energy Logo](docs/assets/logo-text.png)

## Energy Assistant

Energy Assistant is an experimental home energy management service. It connects to your energy data sources, generates an energy plan, and exposes a small API so you can automate grid import/export and device usage. Home Assistant is the only supported connector today, but the long-term goal is platform-agnostic data ingestion.

### What it does
- Pulls realtime and forecast data from connected sources (Home Assistant today).
- Builds a plan for grid import/export and device usage.
- Runs a lightweight API for health checks, settings, and plan triggers.

![Example Plan](docs/assets/example-plan.png)

### Home Assistant integration
Energy Assistant includes a Home Assistant integration (early POC) that surfaces plans as entities so you can automate from your HA dashboard. It lets you view plan outputs and trigger new plans directly from Home Assistant while the broader connector ecosystem evolves.

### Similar projects
- [EMHASS](https://github.com/davidusb-geek/emhass) – Home Assistant-focused energy management and optimization.

### Status
This is early, unreleased software. The planner is wired but still evolving, so outputs should be treated as experimental.

### Quickstart
See [QUICK_START.md](QUICK_START.md) for setup steps, configuration examples, and how to run the API + worker.

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
Architecture, configuration schema details, and developer workflows live in `README_DEV.md`.

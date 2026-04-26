## Architecture

### Repository layout
- `src/energy_assistant/api`: FastAPI app and routes.
- `src/energy_assistant/worker/`: Background planning loop.
- `src/energy_assistant/ems/`: PuLP-backed EMS MILP planner.
- `src/energy_assistant/inputs/`: Source-backed planner input resolution.
- `src/energy_assistant/models/`: Pydantic config models (server, Home Assistant, EMS, inputs, and plant).
- `src/energy_assistant/config.py`: YAML config loader.
- `src/energy_assistant/lib/home_assistant.py`: Thin HTTP client wrapper for Home Assistant APIs.
- `src/energy_assistant/lib/source_resolver/`: Home Assistant source hydration and mapping.
- `src/energy_assistant/plotting/`: Plan and fixture report rendering.
- `custom_components/energy_assistant`: Home Assistant custom integration (POC) for surfacing plans as entities.
- `tests/energy_assistant/`: Unit and integration tests mirroring the source package layout.

### Configuration
A single YAML file (default `config.yaml`) holds server settings, Home Assistant settings, EMS settings, the typed `inputs` registry, and the flat `plant` registry. It is read once at startup; the API does not write config - edit the YAML directly.
See `QUICKSTART.md` for a representative current configuration example.

### API surface
- `GET /settings` - retrieve the `ems:` config section (read-only; edit YAML to change).
- `POST /settings` - returns 501 while YAML remains the source of truth.
- `POST /plan/run` - trigger a plan run (202).
- `GET /plan/latest` - fetch the most recent plan (404 if none).
- `GET /plan/await` - wait for the next plan (204 if the long-poll times out).

### Docker notes
- Ensure `server.host` is `0.0.0.0` in `config.yaml` so the API binds inside the container.
- `data_dir` should point to `/data` if you want CLI outputs (e.g., `ems solve`) on the host volume.

### Development
- Format/lint: `uv run ruff check src custom_components tests`
- Type check: `uv run pyright`
- Entry point: `uv run energy-assistant`

### EMS fixture workflows
See `src/energy_assistant/ems/README.md` for EMS fixture layout and commands.

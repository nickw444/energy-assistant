## Architecture

### Repository layout
- `src/energy_assistant/api`: FastAPI app and routes to manage config plus trigger/retrieve plans.
- `src/energy_assistant/worker/`: Background loop package that fetches Home Assistant data and builds a plan.
- `src/energy_assistant/worker/milp/`: PuLP-backed planner and compiler placeholder for MILP constraints.
- `src/energy_assistant/config.py`: YAML-backed configuration models and store (server + energy settings).
- `src/energy_assistant/lib/home_assistant.py`: Thin HTTP client wrapper for Home Assistant APIs.
- Frontend: not yet built; the repository root is kept flat so a `frontend/` or similar can be added later.

### Configuration
A single YAML file (default `config.yaml`) holds server settings, Home Assistant settings, plant definition, and energy settings. It is read once at startup; the API does not write config—edit the YAML directly.
See `QUICKSTART.md` for a full, current configuration example.

### API surface (initial)
- `GET /settings` – retrieve runtime energy settings (read-only; edit YAML to change).
- `POST /plan/run` – trigger a plan run.
- `GET /plan/latest` – fetch the most recent plan (404 if none).
- `GET /plan/await` – wait for the next plan (long-poll).

### Docker notes
- Ensure `server.host` is `0.0.0.0` in `config.yaml` so the API binds inside the container.
- `data_dir` should point to `/data` if you want CLI outputs (e.g., `ems solve`) on the host volume.

### Development
- Format/lint: `uv run ruff check src custom_components tests`
- Type check: `uv run pyright`
- Entry point: `uv run energy-assistant`

### EMS fixture workflows
- Capture a new scenario: `uv run energy-assistant ems record-scenario --name <scenario-name>`
- Replay a recorded fixture: `uv run energy-assistant ems solve --scenario <name-or-path>`
- Refresh fixture baselines: `uv run energy-assistant ems refresh-baseline [--name <name-or-path>]`
- Generate a single-page report of all fixtures: `uv run energy-assistant ems scenario-report`

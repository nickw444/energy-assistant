## API (FastAPI)

Scope: `src/energy_assistant/api/`.
Assumes repo-wide conventions in the repo-root `AGENTS.md`.

Key files:
- `src/energy_assistant/api/server.py` wires the FastAPI app and mounts routers.
- `src/energy_assistant/api/dependencies.py` defines typed global app-state and FastAPI dependency getters.
- `src/energy_assistant/api/routes/` contains domain routers and DTOs.

Conventions:
- Keep routes split by domain (for example, `plan`, `settings`). Prefer adding a new router over growing a single file.
- Route handlers should use `energy_assistant.api.dependencies` getters instead of reading `request.app.state.*` directly.
- Treat `AppConfig` as read-only runtime input. Do not write YAML config from API handlers.
- Keep planning logic in the worker/EMS packages. Route handlers should trigger or read worker state, not solve directly.
- If you change API response models or endpoints, update the Home Assistant client in `custom_components/energy_assistant/energy_assistant_client/` to match.

Testing:
- Add tests under `tests/energy_assistant/api/` when adding new API behavior (create the package if needed).

## Continuous learning
- Update this file when API boundaries or patterns change (routing layout, dependency injection, error/response conventions).
- Put endpoint-specific edge cases next to the handler/DTO code as comments instead of growing this doc.

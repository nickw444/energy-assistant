## Worker (Background Planning)

Scope: `src/energy_assistant/worker/`.
Assumes repo-wide conventions in the repo-root `AGENTS.md`.

What it does:
- Runs EMS planning on a fallback schedule (every minute).
- Reacts to Home Assistant price entity updates via WebSocket subscriptions with a short debounce (0.75s) to coalesce rapid import/export updates.
- Keeps the latest plan and run state in memory for the API to serve.

Key files:
- `src/energy_assistant/worker/service.py` implements `Worker`.
- `src/energy_assistant/lib/home_assistant_ws.py` implements a reconnecting HA WebSocket subscription client.
- `src/energy_assistant/lib/source_resolver/` hydrates and resolves HA sources used by planning.

Design rules:
- Keep dependencies explicit. The CLI constructs `Worker(app_config=..., resolver=..., ha_ws_client=...)` and passes it into the API.
- Do not couple worker code to FastAPI. The API reads worker state via `energy_assistant.api.dependencies.get_worker` (backed by `app.state.dependencies`).
- Be careful with concurrency and superseding runs. Price-change triggers may start a new run while another is in progress; stale results must not publish as the latest plan.

Testing:
- Worker tests live under `tests/energy_assistant/worker/`.

## Continuous learning
- Update this file when the worker's triggering model, concurrency/cancellation semantics, or dependency boundaries change.
- Document implementation quirks as comments in `src/energy_assistant/worker/service.py` rather than adding more bullets here.

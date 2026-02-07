## Agent Guide (Repository)

This file covers repo-wide conventions for coding agents. For domain-specific guidance, see:
- `src/energy_assistant/api/AGENTS.md` (FastAPI API)
- `src/energy_assistant/worker/AGENTS.md` (background planning loop)
- `src/energy_assistant/ems/AGENTS.md` (EMS MILP solver)
- `src/energy_assistant/lib/source_resolver/AGENTS.md` (Home Assistant data hydration + sources)
- `custom_components/energy_assistant/AGENTS.md` (Home Assistant custom integration)

## Work Like A Human (Scope First)
- Start by identifying the task scope (API vs worker vs EMS vs resolver vs HA integration). Do not try to understand the entire repo up front.
- Work in the relevant domain subtree first, then expand outward only as needed (follow imports, call sites, and tests).
- Prefer reading the closest domain `AGENTS.md` plus the immediate code you are changing, then consult `README_DEV.md` / system design docs only if the task needs it.

## Tooling and quality gates
- Use `uv` for dependency management and running scripts. Keep tooling config in `pyproject.toml` and `pyrightconfig.json`.
- Lint: `uv run ruff check src custom_components tests`
- Type check: `uv run pyright`
- Tests: `uv run pytest`
- Tests should mirror the `src/energy_assistant` package structure under `tests/energy_assistant/`.

## Architecture boundaries
- Keep API and worker logic modular and loosely coupled. Dependencies are wired explicitly in `src/energy_assistant/cli.py`.
- Shared helpers (Home Assistant clients, WebSocket subscriptions, source resolver) live under `src/energy_assistant/lib/`.

## Configuration and persistence
- Config is a single YAML file (`--config`, defaults to `config.yaml`, then `config.dev.yaml`) parsed into Pydantic models.
- Config is read once at startup; the API is read-only for config (no YAML writes).
- Persist runtime artifacts to the filesystem under `server.data_dir` (plans, plots, reports). Avoid destructive changes that would drop user data.
- `TimeWindow.months` only supports 3-letter abbreviations (`jan`..`dec`); numeric months are invalid.

## Repo hygiene
- Default to built-in exceptions unless a distinct custom type is justified.
- Track work items in GitHub Issues (avoid a checked-in TODO list).
- GitHub uses squash merges; when cleaning up worktrees, rely on merged PR status or deleted remote branches rather than `git branch --merged`.
- When updating PR descriptions via `gh`, prefer `gh pr edit --body-file <path>` to preserve markdown formatting.

## Deployment note
- Systemd units should use an absolute path to `uv` in `ExecStart` when `uv` is installed under a user-local path (systemd searches a limited PATH).

## Worktree workflow (when requested)
- For self-contained tasks, use a git worktree under `.worktrees/<meaningful-name>`.
- Run `uv sync --all-extras --dev` inside the worktree.
- Provide a `config.yaml` (copy your local one if present, or follow `QUICKSTART.md`).

## Continuous learning
- When you learn or change repo-level concepts (architecture boundaries, workflows, coding style), update this file and the relevant domain `AGENTS.md` (and `README*` if it affects users).
- Keep `AGENTS.md` focused on concepts and agent workflows. Document implementation quirks and edge-cases as comments next to the relevant code instead of expanding these files.

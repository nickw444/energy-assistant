# Coding Agent Guide

## General
- Read this file and `README.md` before making changes; keep context from both in mind during the session.
- Prefer uv for environment management; use existing scripts and entry points when available.
- Keep CLI structure under `hass_energy` package; add new commands under `hass_energy/cli.py` or submodules.
- Use `ruff` and `pyright` for linting/type checks (`uv run ruff check hass_energy`, `uv run pyright`); keep configs in `pyproject.toml` / `pyrightconfig.json` up to date.
- CI runs in `.github/workflows/ci.yml` using uv; ensure ruff/pyright commands stay in sync with local usage.
- Prefer built-in exception types (e.g., `ValueError`, `FileNotFoundError`) instead of custom exceptions unless distinct error types are truly needed.
- Avoid destructive git commands unless explicitly requested; do not revert user changes.

## Configuration
- Use `hass_energy.config.load_config(path)` (Pydantic-based) to parse YAML; it expects a `home_assistant` mapping with `base_url`, `token` (supports `env:` prefix), `verify_ssl`, and `ws_max_size` (bytes, default 8 MiB; set to `null` for unlimited).
- Example config lives at `hass-energy.example.config.yaml` for reference.
- Root-level `--config` is required and shared by subcommands; CLI loads the config at startup and makes it available via `ctx.obj["config"]`.
- When adding new config fields, update `hass-energy.example.config.yaml`, `hass-energy.config.yaml` (if present), and README/cli guidance as needed so users and agents stay aligned.
- `datalogger.triggers` (list) can be set in config; the `datalogger` CLI defaults to these when `--trigger` is omitted.

## Debugging helpers (Home Assistant)
- Commands are namespaced under `hass`: `hass list-entities` (all entity IDs) and `hass get-states <entity_id...>` (print states).
- Use root `--config` to point at the HA YAML; commands rely on the stateful websocket client.
- `ws_max_size` defaults to 8 MiB; set to a larger value or `null` in config for very large instances to avoid payload limits.
- `datalogger` is a root command (not under `hass`): listens for state changes on `--trigger` entities and, after an optional `--debounce` (default 2s), writes JSON snapshots of the specified `--entity` list into an `--output-dir` directory (`captured_at`, `entities`, `trigger`, `states`). If `--entity` is omitted and a mapper is configured, it uses the mapper's `required_entities`. The logic lives in `hass_energy.datalogger.DataLogger` for reuse.
- Config requires a `mapper` section: `module` (importable/dotted; assumes module is on PYTHONPATH/CWD; `.py` file paths also allowed) and optional `attribute` (defaults to `get_mapper`/`mapper`/`Mapper`). The mapper must implement `HassEnergyMapper` (`required_entities`, `map`). Loader helper: `hass_energy.mapper.load_mapper(mapper_config)`. Example implementation: `example_mapper.py` at repo root. The `run-mapper` CLI command loads the configured mapper, fetches the required entities once, maps them, and prints JSON.

## Continuous learning
- When you learn new project knowledge, coding style, or preferences during a session, update `AGENTS.md` (and `README.md` if it affects users) before finishing so the next agent benefits.

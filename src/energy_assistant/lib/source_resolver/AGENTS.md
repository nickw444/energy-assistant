## Source Resolver (Home Assistant Data Hydration)

Scope: `src/energy_assistant/lib/source_resolver/`.
Assumes repo-wide conventions in the repo-root `AGENTS.md`.

Concepts:
- Config models embed typed `EntitySource[...]` values.
- `ValueResolver.mark_for_hydration(app_config)` recursively walks the config and marks required entities/history to fetch.
- Hydration happens via `HassDataProvider` (`fetch_states()` and `fetch_history()`).
- `ValueResolver.resolve(source)` maps raw Home Assistant state/history to typed values or forecast interval sequences.

Rules:
- Keep mapper functions deterministic and unit-aware (normalize units, parse timestamps, validate required fields).
- Prefer expressing new data needs as a new `EntitySource` implementation rather than reaching into HA clients directly from EMS/worker code.
- When adding a new `EntitySource` implementation:
  - Add support in `ValueResolverImpl.resolve(...)` and `ValueResolverImpl.mark(...)`.
  - Add tests under `tests/energy_assistant/lib/source_resolver/`.
- Keep fixture tooling using `FixtureHassDataProvider` so EMS and worker logic can be tested offline.

## Continuous learning
- Update this file when the resolver's dataflow contracts (mark, hydrate, resolve), source patterns, or testing approach change.
- Put source-specific parsing edge cases next to the mapper implementation (for example, in `hass_source.py`) as comments instead of growing this doc.

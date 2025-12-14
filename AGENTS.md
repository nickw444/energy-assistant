# Coding Agent Guide

- Read this file and `README.md` before making changes; keep context from both in mind during the session.
- Prefer uv for environment management; use existing scripts and entry points when available.
- Keep CLI structure under `hass_energy` package; add new commands under `hass_energy/cli.py` or submodules.
- When you learn new project knowledge, coding style, or preferences during a session, update `AGENTS.md` (and `README.md` if it affects users) before finishing so the next agent benefits.
- Avoid destructive git commands unless explicitly requested; do not revert user changes.

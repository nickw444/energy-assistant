# EMS Refactor Follow-ups

## Component config colocation
- Consider colocating 1:1 component config models alongside their EMS component area rather than keeping all plant config models centralized in `src/energy_assistant/models/plant.py`.
- Prefer package-level colocation, not putting runtime component classes and Pydantic config models in the exact same module.
- Preserve one-way dependencies:
  - config parsing and schema validation may import component config modules,
  - EMS runtime modules may import their colocated config modules,
  - config modules must not import EMS runtime modules.
- Keep a small central aggregator for the discriminated `PlantComponentConfig` union and any cross-component schema validation that needs to type-switch across the full plant registry.
- Main reason this is deferred: moving config classes directly into runtime component modules would likely create import cycles with `models.config` and EMS components, and the repo explicitly prefers avoiding `TYPE_CHECKING`-based cycle workarounds.

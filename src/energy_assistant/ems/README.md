# EMS Package

The EMS package turns a configured plant and resolved input data into a solved energy plan.

At a high level:
- `planner.py` orchestrates a solve.
- `system/factory.py` builds persistent logical components from the flat `plant` registry.
- `system/system.py` builds a fresh physical graph for each solve and extracts component plans.
- `components/` contains logical component behavior such as grid, PV, battery, EV, inverter, load, and switchboard.
- `topology/` contains the solve-scoped graph primitives: nodes, storage nodes, connections, policies, and fragments.
- `inputs/` aligns resolved source data to the current planning horizon.

The important split is persistent logical components versus per-solve topology. Config `connection`
fields link plant components by id; the factory resolves those ids into component references. During
a solve, components emit physical graph elements, optional late-bound fragments, and typed plan
payloads. The exported plan remains a flat `components` map keyed by plant component id.

## Fixture Workflows

EMS tests live under `tests/energy_assistant/ems/`. Fixture scenario assets live under
`tests/fixtures/ems/<fixture>/<scenario>/` and contain `config.yaml`, `input.json`, `output.json`,
`output.svg`, plus a `vis/` directory containing `logical_component_graph.svg` and
`topological_energy_graph.svg`.

- Record a scenario: `uv run energy-assistant ems record-scenario --fixture <fixture> --name <scenario>`
- Replay a scenario: `uv run energy-assistant ems solve --fixture <fixture> --scenario <scenario>`
- Refresh baselines: `uv run energy-assistant ems refresh-baseline [--fixture <fixture>] [--name <scenario>]`
- Render a report: `uv run energy-assistant ems scenario-report [--fixture <fixture>]`

Fixture SVG artifacts use Graphviz for the component/topology graphs, so the `dot` executable must
be available when refreshing or validating fixture artifacts.

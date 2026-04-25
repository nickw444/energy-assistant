# EMS Logical Component Graph Refactor Follow-Ups

## Purpose

This file captures the remaining work needed after the first implementation pass of
`ems-logical-component-graph-refactor.md`.

The refactor is mostly in the intended shape: logical components are wired with direct object
references, `system/topology.py` has been removed, graph construction now uses
`create_graph_elements(...)`, and battery reserve/export behavior is emitted through a component
fragment hook. The follow-ups below close the remaining gaps so the codebase consistently matches
the canonical end state.

## 1. Make `NodeId` The Strict Topology Node Boundary

`NodeId` exists, but several topology primitives still accept `NodeId | str` and coerce internally.
That keeps the old loose string boundary alive.

Change the topology APIs to require branded topology-layer node ids:

```python
class Node:
    def __init__(self, id: NodeId, ...): ...


class StorageNode:
    def __init__(self, id: NodeId, ...): ...


class Connection:
    def __init__(self, a_node_id: NodeId, b_node_id: NodeId, ...): ...

    def flow_out_of_node(self, node_id: NodeId) -> dict[int, pulp.LpVariable]: ...

    def flow_into_node(self, node_id: NodeId) -> dict[int, pulp.LpVariable]: ...
```

Then update all tests and helpers that construct topology primitives to explicitly use `NodeId(...)`.
This should include direct uses in storage, bus balance, PV curtailment, EV switch penalty, grid
behavior, and policy tests.

Canonical test construction:

```python
from energy_assistant.ems.topology.ids import NodeId

node = Node(id=NodeId("grid"), horizon=horizon, ...)
connection = Connection(
    id="grid_link",
    horizon=horizon,
    a_node_id=NodeId("grid"),
    b_node_id=NodeId("switchboard"),
    policies={...},
)
```

## 2. Remove The `build_topology(...)` Compatibility Shim

`EmsComponent` still exposes `build_topology(...)` as a wrapper around
`create_graph_elements(...)`. The final interface should use the new names only.

Delete `EmsComponent.build_topology(...)` and update any remaining callers or tests. After removal,
the component protocol should expose:

```python
class EmsComponent(Protocol):
    component_type: ComponentType

    def create_graph_elements(...): ...

    def create_graph_fragments(...): ...

    def extract_plan(...): ...
```

## 3. Settle Wiring Validation Ownership

The refactor plan puts object-reference resolution in `EmsSystemFactory`. The current implementation
also performs connection target validation in the Pydantic config model.

Choose and document the final split:

- Pydantic config validates schema-level wiring rules, such as `battery.connection` pointing at an
  inverter and `grid.connection` pointing at a switchboard.
- `EmsSystemFactory` resolves already-valid ids into direct logical component references and raises
  construction errors for impossible or missing references.

Add tests that cover both layers so this split is explicit rather than accidental.

## 4. Clean Up EMS Agent Documentation

`src/energy_assistant/ems/AGENTS.md` has a stale line saying components should only expose
`extract_plan(...)`, followed by the new guidance that components expose
`create_graph_elements(...)`, `create_graph_fragments(...)`, and `extract_plan(...)`.

Rewrite that section so it describes the canonical component surface once:

```text
Components create solve-scoped physical graph elements in create_graph_elements(...), optionally
emit late-bound graph fragments in create_graph_fragments(...), and extract plans in extract_plan(...).
EmsSystem owns solve-state lookup and ComponentPlan normalization.
```

## 5. Review Battery Fragment Late Binding

`BatteryComponent.create_graph_fragments(...)` now owns battery reserve/export behavior, which is the
right component ownership boundary. It currently binds the grid import price into the battery storage
solve state after the `StorageNode` has already been created.

Make this late binding explicit and easier to reason about. One acceptable end state is:

```python
class BatteryComponent:
    def create_graph_fragments(...):
        grid = self._grid_for_switchboard(build_ctx)
        battery_state = solve_states.component(self.id, BatterySolveState)
        battery_state.storage.set_terminal_import_price(grid_state.price_import_raw)
        return [BatteryExportReservePolicy(...)]
```

If mutation remains the mechanism, give it a named method on `StorageNode` instead of assigning
`price_import_raw` directly. If the data can be supplied earlier without rebuilding a topology index,
prefer passing it at `StorageNode` construction time.

## 6. Rename Context Tests To Match The New Layering

Done. Former `test_topology_context.py` is split into `test_graph_build_context.py` and
`test_plan_context.py`.

## 7. Remove Or Relocate `TODO.md`

The repository guide says not to keep checked-in TODO lists. There is currently an untracked
`TODO.md` at the repo root.

Move any useful content into this plan file or into GitHub issues, then delete `TODO.md`.

**Migrated from `TODO.md` (deferred ideas, not part of the graph refactor):**

- Consider modeling `EvChargeControl` as a more primitive `ConnectionPolicy`, with inputs adapted
  to it.
- Move passthrough-related constraint helpers off the base `ConnectionPolicy` into a dedicated
  passthrough policy; types that need passthrough semantics can inherit or compose it.
- Evaluate using a class-level `component_type` (or similar) for components where the dynamic
  `@property` is redundant.

## 8. Run The Full Quality Gates

After the follow-ups are complete, run:

```bash
uv run ruff check src custom_components tests
uv run pyright
uv run pytest
```

Also run the EMS-focused suite while iterating:

```bash
uv run pytest tests/energy_assistant/ems
```

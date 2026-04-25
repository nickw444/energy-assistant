# EMS Logical Component Graph Refactor Plan

## Objective

Refactor EMS into two explicit layers:

1. Logical component graph: persistent Python component objects wired together by direct references.
2. Physical topology graph: solve-scoped `EnergyGraph` objects made of `Node`, `StorageNode`, `Connection`, policies, and fragments.

The implementation should remove the persistent logical topology index in `src/energy_assistant/ems/system/topology.py`. Logical wiring should be resolved eagerly by `EmsSystemFactory`; physical topology should live under `src/energy_assistant/ems/topology/`.

## Canonical End State

### Logical Component Graph

Config still uses component keys and `connection` fields:

```yaml
plant:
  switchboard:
    type: switchboard

  grid:
    type: grid
    connection: switchboard

  inverter:
    type: inverter
    connection: switchboard

  pv:
    type: pv
    connection: inverter

  battery:
    type: battery
    connection: inverter
```

`EmsSystemFactory` resolves those strings into object references:

```python
switchboard = SwitchboardComponent(component_id="switchboard")

grid = GridComponent(
    component_id="grid",
    switchboard=switchboard,
    grid=grid_config,
    ...
)

inverter = InverterComponent(
    component_id="inverter",
    switchboard=switchboard,
    inverter=inverter_config,
)

pv = PvComponent(
    component_id="pv",
    inverter=inverter,
    pv=pv_config,
)

battery = BatteryComponent(
    component_id="battery",
    inverter=inverter,
    battery=battery_config,
)
```

The logical graph is ordinary object references:

```text
GridComponent.switchboard      -> SwitchboardComponent
InverterComponent.switchboard  -> SwitchboardComponent
PvComponent.inverter           -> InverterComponent
BatteryComponent.inverter      -> InverterComponent
EvComponent.switchboard        -> SwitchboardComponent
```

Components remain peers in the system. `EmsSystem` stores them in config order and builds each component without a topological ordering layer.

### Physical Topology Node Ids

Components expose branded ids for physical topology-layer nodes. These are the ids used by
`src/energy_assistant/ems/topology` nodes and connections. There is one node id brand because AC
buses, DC buses, storage nodes, producers, and consumers are all graph nodes at this layer.

Create:

```text
src/energy_assistant/ems/topology/ids.py
```

Canonical contents:

```python
from typing import NewType

NodeId = NewType("NodeId", str)
```

Component ids and connection ids remain plain strings:

```text
component.id: stable config/export/diagnostic label
connection.id: stable solve graph label
node ids: branded physical topology addresses
```

Canonical component fields:

```python
class SwitchboardComponent:
    id: str
    bus_id: NodeId


class InverterComponent:
    id: str
    switchboard: SwitchboardComponent
    dc_bus_id: NodeId


class BatteryComponent:
    id: str
    inverter: InverterComponent
    node_id: NodeId
```

Topology primitives accept branded node ids:

```python
class Node:
    id: NodeId


class StorageNode:
    id: NodeId


class Connection:
    a_node_id: NodeId
    b_node_id: NodeId
```

Canonical topology construction:

```python
class InverterComponent:
    def create_graph_elements(...):
        dc_bus = Node(
            id=self.dc_bus_id,
            name=f"DC Bus {self.id}",
            node_role="bus",
            horizon=horizon,
        )
        link = Connection(
            id=self.inverter_link_id,
            horizon=horizon,
            a_node_id=self.dc_bus_id,
            b_node_id=self.switchboard.bus_id,
            policies={...},
        )
        return [dc_bus, link], InverterSolveState(inverter_connection=link)
```

### Physical Graph Emitted At Solve Time

For a switchboard, grid, inverter, PV, and battery setup, local component builders produce:

```text
grid -- grid_link -- switchboard -- inverter_link -- inverter_dc -- battery
                                                    \
                                                     pv
```

PV connects to the inverter DC bus:

```python
Connection(
    id=self.connection_id,
    horizon=horizon,
    a_node_id=self.node_id,
    b_node_id=self.inverter.dc_bus_id,
    policies={...},
)
```

Battery connects to the inverter DC bus:

```python
Connection(
    id=self.connection_id,
    horizon=horizon,
    a_node_id=self.inverter.dc_bus_id,
    b_node_id=self.node_id,
    policies={...},
)
```

## Delta By Area

### 1. Shared Component Types

Create:

```text
src/energy_assistant/ems/system/types.py
```

Move `ComponentType` there:

```python
from typing import Literal

ComponentType = Literal[
    "switchboard",
    "grid",
    "load",
    "load_controlled_ev",
    "inverter",
    "battery",
    "pv",
]
```

Update all imports to use `energy_assistant.ems.system.types.ComponentType`.

### 2. Build And Plan Contexts

Create:

```text
src/energy_assistant/ems/system/context.py
```

Move `GraphBuildContext` and `PlanContext` into this file.

Canonical `GraphBuildContext`:

```python
class GraphBuildContext:
    def __init__(
        self,
        *,
        components: Mapping[str, object],
        solve_states: SolveStateStore,
    ) -> None:
        self.components = components
        self.solve_states = solve_states
        self._connections_by_component_id: dict[str, list[Connection]] = {}

    def register(self, component_id: str, elements: list[GraphElement]) -> None: ...
    def component(self, component_id: str, component_type: type[T]) -> T: ...
    def components_of_type(self, component_type: type[T]) -> tuple[T, ...]: ...
    def connection(self, component_id: str) -> Connection | None: ...
    def connections(self, component_id: str) -> tuple[Connection, ...]: ...
    def connections_of_type(self, component_type: ComponentType) -> list[Connection]: ...
```

Canonical `PlanContext`:

```python
class PlanContext:
    def __init__(
        self,
        *,
        components: Mapping[str, object],
        solve_states: SolveStateStore,
    ) -> None:
        self.components = components
        self.solve_states = solve_states

    def component(self, component_id: str, component_type: type[T]) -> T: ...
    def components_of_type(self, component_type: type[T]) -> tuple[T, ...]: ...
```

These contexts are solve/build helpers. They do not own logical wiring.

### 3. Component Contract

Update:

```text
src/energy_assistant/ems/system/component.py
```

Canonical contract:

```python
class EmsComponent[TSolveState, TPlanExport](ABC, SupportsSolveState[TSolveState]):
    id: str

    @property
    @abstractmethod
    def component_type(self) -> ComponentType:
        """Stable logical component type."""

    @abstractmethod
    def create_graph_elements(
        self,
        *,
        horizon: Horizon,
        inputs: AppliedInputRegistry,
        build_ctx: GraphBuildContext,
    ) -> tuple[list[GraphElement], TSolveState]: ...

    @abstractmethod
    def extract_plan(
        self,
        snapshot: ModelSnapshot,
        *,
        solve_state: TSolveState,
        plan_ctx: PlanContext,
    ) -> TPlanExport: ...
```

Each concrete component implements `component_type` as a property:

```python
@property
def component_type(self) -> ComponentType:
    return "battery"
```

Method naming is final:

```python
def create_graph_elements(...): ...
def extract_plan(...): ...
```

`create_graph_elements(...)` creates solve-scoped physical `EnergyGraph` elements. `extract_plan(...)`
reads solved values from the snapshot and component solve state.

### 4. Component Constructors And Fields

Update component constructors to accept direct references.

Switchboard:

```python
class SwitchboardComponent:
    def __init__(self, *, component_id: str) -> None:
        self.id = component_id
        self.bus_id = NodeId(component_id)
```

Grid:

```python
class GridComponent:
    def __init__(
        self,
        *,
        component_id: str,
        switchboard: SwitchboardComponent,
        grid: GridComponentConfig,
        ...
    ) -> None:
        self.id = component_id
        self.switchboard = switchboard
        self.node_id = NodeId(component_id)
        self.connection_id = f"{component_id}_link"
```

Inverter:

```python
class InverterComponent:
    def __init__(
        self,
        *,
        component_id: str,
        switchboard: SwitchboardComponent,
        inverter: InverterComponentConfig,
    ) -> None:
        self.id = component_id
        self.switchboard = switchboard
        self.dc_bus_id = NodeId(f"{component_id}_dc")
        self.inverter_link_id = f"{component_id}_acdc"
```

PV:

```python
class PvComponent:
    def __init__(
        self,
        *,
        component_id: str,
        inverter: InverterComponent,
        pv: PvComponentConfig,
    ) -> None:
        self.id = component_id
        self.inverter = inverter
        self.node_id = NodeId(component_id)
```

Battery:

```python
class BatteryComponent:
    def __init__(
        self,
        *,
        component_id: str,
        inverter: InverterComponent,
        battery: BatteryComponentConfig,
        grid_max_export_kw: float,
    ) -> None:
        self.id = component_id
        self.inverter = inverter
        self.node_id = NodeId(component_id)
```

EV:

```python
class EvComponent:
    def __init__(
        self,
        *,
        component_id: str,
        switchboard: SwitchboardComponent,
        load: ControlledEvComponentConfig,
        ...
    ) -> None:
        self.id = component_id
        self.switchboard = switchboard
        self.node_id = NodeId(component_id)
```

### 5. Factory Wiring

Update:

```text
src/energy_assistant/ems/system/factory.py
```

`EmsSystemFactory` resolves config `connection` eagerly.

Canonical build flow:

```python
switchboards = {
    key: SwitchboardComponent(component_id=key)
    for key, _ in cls._components(app_config.plant, SwitchboardComponentConfig)
}

grids = {
    key: GridComponent(
        component_id=key,
        switchboard=cls._switchboard(
            switchboards,
            component_key=key,
            target_key=component.connection,
        ),
        grid=component,
        ...
    )
    for key, component in grid_cfgs.items()
}

inverters = {
    key: InverterComponent(
        component_id=key,
        switchboard=cls._switchboard(...),
        inverter=component,
    )
    for key, component in inverter_cfgs.items()
}

pvs = {
    key: PvComponent(
        component_id=key,
        inverter=cls._inverter(
            inverters,
            component_key=key,
            target_key=component.connection,
        ),
        pv=component,
    )
    for key, component in pv_cfgs.items()
}
```

Factory helper methods return component objects, not ids:

```python
def _switchboard(...) -> SwitchboardComponent: ...
def _inverter(...) -> InverterComponent: ...
```

The final `EmsSystem` receives both a map and an ordered tuple:

```python
components: dict[str, EmsComponent[Any, Any]] = {...}
ordered_components = tuple(components[key] for key in app_config.plant if key in components)
return EmsSystem(components=components, ordered_components=ordered_components)
```

### 6. System Build Flow

Update:

```text
src/energy_assistant/ems/system/system.py
```

Canonical storage:

```python
class EmsSystem:
    def __init__(
        self,
        *,
        components: dict[str, EmsComponent[Any, Any]],
        ordered_components: tuple[EmsComponent[Any, Any], ...],
    ) -> None:
        self.components = dict(components)
        self.ordered_components = ordered_components
```

Canonical snapshot build:

```python
def build_snapshot(self, *, horizon: Horizon, inputs: AppliedInputRegistry):
    graph = EnergyGraph()
    solve_states = SolveStateStore()
    build_ctx = GraphBuildContext(
        components=self.components,
        solve_states=solve_states,
    )

    for component in self.ordered_components:
        elements, component_solve_state = component.create_graph_elements(
            horizon=horizon,
            inputs=inputs,
            build_ctx=build_ctx,
        )
        graph.add_elements(elements)
        solve_states.put(component, component_solve_state)
        build_ctx.register(component.id, elements)

    for component in self.ordered_components:
        extra_elements = component.create_graph_fragments(
            graph=graph,
            build_ctx=build_ctx,
            solve_states=solve_states,
        )
        graph.add_elements(extra_elements)

    return ModelSnapshot(ctx=ModelContext(horizon=horizon), graph=graph), solve_states
```

Plan extraction also walks `ordered_components`.

### 7. Component-Owned Cross-Component Fragments

Battery reserve/export constraints move out of `BatteryComponent.create_graph_elements()` and into a
follow-up fragment hook on the component protocol. This keeps the behavior coupled to battery
configuration while making it run after all local graph elements have been built.

Canonical battery local build:

```text
BatteryComponent.create_graph_elements emits:
  StorageNode(id=battery.node_id)
  Connection(a_node_id=battery.inverter.dc_bus_id, b_node_id=battery.node_id)
  local battery policies
  BatterySolveState
```

Add an optional/default component protocol hook:

```python
class EmsComponent(...):
    def create_graph_fragments(
        self,
        *,
        graph: EnergyGraph,
        build_ctx: GraphBuildContext,
        solve_states: SolveStateStore,
    ) -> list[GraphElement]:
        return []
```

`BatteryComponent` overrides that hook:

```python
def create_graph_fragments(
    self,
    *,
    graph: EnergyGraph,
    build_ctx: GraphBuildContext,
    solve_states: SolveStateStore,
) -> list[GraphElement]:
    grids = build_ctx.components_of_type(GridComponent)
    same_switchboard_grids = [
        grid
        for grid in grids
        if grid.switchboard is self.inverter.switchboard
    ]
    grid_connections = [
        connection
        for grid in same_switchboard_grids
        for connection in build_ctx.connections(grid.id)
    ]
    battery_state = solve_states.get(self)
    return [
        BatteryReserveExportFragment(
            battery=battery_state.connection,
            storage=battery_state.storage,
            grid_connections=grid_connections,
            ...
        )
    ]
```

The concrete fragment class can reuse the existing battery reserve policy implementation or extract it from `battery.py` into a clearer graph-fragment class.

### 8. Delete Logical Topology Layer

Delete:

```text
src/energy_assistant/ems/system/topology.py
```

Relocate the useful pieces:

```text
ComponentType -> src/energy_assistant/ems/system/types.py
GraphBuildContext -> src/energy_assistant/ems/system/context.py
PlanContext -> src/energy_assistant/ems/system/context.py
logical wiring validation -> src/energy_assistant/ems/system/factory.py
physical node id brands -> src/energy_assistant/ems/topology/ids.py
physical graph primitives stay in src/energy_assistant/ems/topology/*
```

After this delta, no imports should reference:

```text
energy_assistant.ems.system.topology
```

### 9. Tests

Update or add focused tests for the canonical behavior:

```text
factory resolves grid.connection to GridComponent.switchboard
factory resolves inverter.connection to InverterComponent.switchboard
factory resolves pv.connection to PvComponent.inverter
factory resolves battery.connection to BatteryComponent.inverter
factory preserves config order in EmsSystem.ordered_components
grid/load/EV connections use switchboard.bus_id
inverter connection uses inverter.dc_bus_id and switchboard.bus_id
PV connection uses pv.node_id and inverter.dc_bus_id
battery connection uses inverter.dc_bus_id and battery.node_id
battery reserve/export fragment uses grids whose switchboard is battery.inverter.switchboard
GraphBuildContext preserves multiple connections per component
```

Remove or rewrite tests whose only purpose is to validate `PlantTopology`.

### 10. Docs

Update:

```text
src/energy_assistant/ems/AGENTS.md
src/energy_assistant/ems/EMS_SYSTEM_DESIGN.md
```

Canonical wording:

```text
Logical components are wired eagerly by EmsSystemFactory using direct component references.
Components emit local physical graph elements.
Components can emit follow-up graph fragments after all local graph elements are built.
Physical topology is represented by EnergyGraph, Node, StorageNode, Connection, and policies.
There is no persistent logical topology layer.
```

## Execution Order

```text
1. Add topology/ids.py with NodeId.
2. Add system/types.py and move ComponentType.
3. Add system/context.py and move GraphBuildContext / PlanContext.
4. Update topology primitives to accept branded NodeId values.
5. Update EmsComponent to import ComponentType from system/types.py.
6. Update all component constructors to direct references.
7. Update all component physical id fields to branded node ids.
8. Update component graph-element creation methods to use referenced component node ids.
9. Update EmsSystemFactory to resolve config connections into direct references.
10. Update EmsSystem to store ordered_components and remove PlantTopology.
11. Move battery reserve/export behavior into a component-owned post-build fragment hook.
12. Delete system/topology.py and remove all imports.
13. Update tests and docs.
14. Run quality gates.
```

## Quality Gates

```text
uv run ruff check src custom_components tests
uv run pyright
uv run pytest tests/energy_assistant/ems
```

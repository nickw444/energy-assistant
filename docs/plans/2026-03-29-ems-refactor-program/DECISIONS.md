# EMS Refactor Decision Register (2026-03-29)

This register captures the major architectural decisions made during the EMS refactor so future work can start from the current shape rather than from the original monolithic planner.

## D001 — Replace the monolithic planner with layered responsibilities
- Status: accepted and implemented
- Decision: split orchestration, logical system modeling, physical topology modeling, and input handling into separate responsibilities.
- Why: the prior shape concentrated too much behavior into one planner surface.
- Consequence: the system now has clearer seams for extension and testing.

## D002 — Model the physical system as a hidden graph
- Status: accepted and implemented
- Decision: represent physical energy relationships through a graph-based topology layer.
- Why: physical constraints, storage behavior, and connection-local objective terms are easier to compose when they are expressed as graph fragments.
- Consequence: the optimization model is now built from local graph-owned fragments rather than from one central builder routine.

## D003 — Keep logical devices separate from the physical graph
- Status: accepted and implemented
- Decision: logical electrical components expand into the physical model rather than being identical to it.
- Why: user-facing concepts and physical solver concepts have different responsibilities.
- Consequence: the logical layer owns configuration and plan extraction, while the topology layer owns physical constraints.

## D004 — Use object-oriented encapsulation rather than a functional builder pipeline
- Status: accepted and implemented
- Decision: use classes to encapsulate logical behavior, topology behavior, and data flow boundaries.
- Why: the goal of the refactor was maintainability through clearer local ownership.
- Consequence: new planner behavior can be added by extending smaller abstractions rather than enlarging one builder.

## D005 — Generalize most physical entities, keep storage as the special case
- Status: accepted and implemented
- Decision: avoid a large set of physical node types and instead generalize ordinary energy entities, while keeping storage behavior separate.
- Why: most entities differ only by role in flow balance, while storage has fundamentally different stateful behavior.
- Consequence: the physical model has fewer concrete concepts and a smaller surface area.

## D006 — Treat neutral balance nodes as buses
- Status: accepted and implemented
- Decision: use bus-oriented semantics for energy-balance-only nodes.
- Why: this is clearer in the electrical-system domain and aligned with the desired mental model.
- Consequence: switchboards and inverter connection points map cleanly onto the topology.

## D007 — Keep domain-specific wiring rules out of the topology layer
- Status: accepted and implemented
- Decision: leave attach-side semantics such as AC/DC behavior to the logical layer and configuration validation.
- Why: topology should stay focused on flow behavior rather than component taxonomy.
- Consequence: the physical layer is smaller and more reusable.

## D008 — Track directional source-side and sink-side power explicitly
- Status: accepted and implemented
- Decision: connections model flow entering and leaving each side explicitly rather than hiding losses or transformations in factors.
- Why: connection-local behaviors such as efficiency need visible flow variables to remain comprehensible.
- Consequence: connection-local laws are now written as ordinary constraints over explicit directional flow.

## D009 — Keep connection-local behavior conceptually segment-based
- Status: accepted and implemented
- Decision: preserve the existing terminology while treating connection-local behaviors as ordered segments in a chain.
- Why: the important improvement was semantic clarity rather than renaming.
- Consequence: connection-local logic is now best understood as ordered pieces of a connection path.

## D010 — Use a single public connection-local constraint hook
- Status: accepted and implemented
- Decision: remove the separate transfer-specific hook and let all connection-local behavior flow through a single constraint interface.
- Why: the old split was unnecessary once connection-local behaviors were treated as segments.
- Consequence: every connection-local behavior now participates through the same abstraction.

## D011 — Make passthrough the default connection-segment behavior
- Status: accepted and implemented
- Decision: the baseline behavior of a connection-local segment is passthrough transport.
- Why: additive connection behavior needs a simple default transport rule.
- Consequence: empty connections still work, and additive behaviors have an explicit baseline to preserve.

## D012 — Distinguish additive and transforming connection behavior by implementation pattern, not by extra type layers
- Status: accepted and implemented
- Decision: treat connection-local behaviors as either passthrough-plus or transport-transforming without adding separate kinds.
- Why: the distinction matters, but another type layer would add complexity without much value.
- Consequence: the model stays simple while still capturing the real difference in behavior.

## D013 — Auto-inject passthrough only for empty connections
- Status: accepted and implemented
- Decision: create a default passthrough behavior only when a connection has no declared connection-local behaviors.
- Why: explicit connection chains should define their own transport behavior.
- Consequence: connections remain minimal while empty connections still behave sensibly.

## D014 — Keep named lookup for connection-local behavior
- Status: accepted and implemented
- Decision: retain named lookup from the logical layer into connection-local behaviors.
- Why: logical devices still need to inspect or expose some connection-local details after solving.
- Consequence: the logical layer can safely query connection-local internals where needed.

## D015 — Move to an explicitly configured fixed-shape rolling horizon
- Status: accepted and implemented
- Decision: configure the planner horizon directly and treat forecast sufficiency as validation.
- Why: forecast-driven horizon sizing made the planner dynamic in the wrong place.
- Consequence: solve shape is predictable and easier to test.

## D016 — Separate horizon shape from the current solve window
- Status: accepted and implemented
- Decision: treat the rolling horizon pattern as persistent and the current solve window as per-run.
- Why: the planner needs a stable relative layout but a moving absolute anchor.
- Consequence: rolling solves no longer require rediscovering the structural horizon contract.

## D017 — Keep run-scoped solver objects for now
- Status: accepted and implemented
- Decision: do not attempt aggressive reuse of solver objects or in-place model mutation yet.
- Why: correctness and clarity matter more than premature reuse of optimization internals.
- Consequence: the persistent reuse point is the logical system definition and horizon shape, not the low-level solve objects.

## D018 — Replace the old EMS config with separate input and plant registries
- Status: accepted and implemented
- Decision: redesign configuration around typed inputs and a flat logical system registry.
- Why: the earlier schema mixed resolution, topology, and logical concerns too heavily.
- Consequence: the configuration is flatter, more declarative, and more strongly validated.

## D019 — Keep the logical system registry keyed, not list-based
- Status: accepted and implemented
- Decision: logical components are identified by stable keys in a mapping.
- Why: connections and validation are simpler when identities are explicit.
- Consequence: logical wiring is clearer and less error-prone.

## D020 — Keep the EMS input model intentionally small
- Status: accepted and implemented
- Decision: support only scalar inputs and forecast inputs at the EMS boundary.
- Why: this is sufficient for the planner’s needs without proliferating special cases.
- Consequence: input complexity is kept low, with composition handled inside the forecast path.

## D021 — Allow both shorthand and structured input references
- Status: accepted and implemented
- Decision: permit concise references for simple cases while retaining a structured form for extensibility.
- Why: usability and future flexibility both matter.
- Consequence: the schema is concise without becoming rigid.

## D022 — Keep wiring logical and infer attach-side semantics
- Status: accepted and implemented
- Decision: users connect logical components to each other, and the implementation infers the physical attach side.
- Why: the user should describe the electrical system logically, not in low-level attach-point detail.
- Consequence: the configuration remains easier to read and write.

## D023 — Move source resolution out of the EMS component layer
- Status: accepted and implemented
- Decision: logical components no longer own data hydration or external source access.
- Why: source resolution and planner modeling are different responsibilities.
- Consequence: components now consume typed planner inputs rather than resolver objects.

## D024 — Keep forecast alignment inside EMS
- Status: accepted and implemented
- Decision: resolve raw forecast data first, then apply it to the current horizon inside the planner.
- Why: alignment, slot replacement, coverage checks, and extension logic are planner policy decisions.
- Consequence: the data boundary is cleaner and forecast behavior remains testable at the planner layer.

## D025 — Add historical extension for short price forecasts
- Status: accepted and implemented
- Decision: allow price forecasts to be extended from recent realtime history when provider coverage is too short.
- Why: fixed-shape horizons exposed cases where provider forecasts ended too early.
- Consequence: the planner can maintain a stable horizon without shrinking to match external forecast length.

## D026 — Use scenario fixtures as the main regression safety mechanism
- Status: accepted and implemented
- Decision: rely on recorded scenarios and baseline comparisons to validate planner behavior through the refactor.
- Why: small structural changes in an optimization system can create non-obvious behavioral drift.
- Consequence: scenario replay is a core acceptance signal, not an optional convenience.

## D027 — Allow scenario-specific fixture configuration
- Status: accepted and implemented
- Decision: fixture scenarios may carry their own planner configuration rather than sharing one fixture-wide setup.
- Why: recorded scenarios do not all have the same forecast coverage or horizon needs.
- Consequence: offline replay is more faithful and deterministic.

## D028 — Support both raw-capture and resolved-input fixture formats during migration
- Status: accepted and implemented
- Decision: fixture replay can load either original captured data or resolved planner input payloads.
- Why: this made the migration safer without losing the original source-of-truth capture path.
- Consequence: fixture tooling can convert formats while keeping replay stable.

## D029 — Regenerate resolved fixtures from original captures, not synthetic expansions
- Status: accepted and implemented
- Decision: when converting fixtures, derive resolved inputs from the original captured data rather than manufacturing them from already-aligned series.
- Why: synthetic reconstruction changed scenario fidelity.
- Consequence: current resolved fixtures better reflect real captured inputs.

## D030 — Reduce stored fixture precision only where safe
- Status: accepted and implemented
- Decision: reduce storage precision for non-power values, but preserve full precision for power forecasts.
- Why: blanket rounding changed solved plans in some scenarios.
- Consequence: fixture storage is less noisy without damaging scenario parity.

## D031 — Preserve reserve-related export behavior during the refactor
- Status: accepted and implemented
- Decision: retain the historical reserve/export behavior rather than changing it during the structural rewrite.
- Why: behavior changes and structural changes should not be mixed unless necessary.
- Consequence: the refactor stayed focused on architecture and maintainability rather than altering planner semantics.

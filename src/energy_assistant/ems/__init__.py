"""EMS MILP (v4) implementation.

The solver models the electrical system as a reusable topology template (Layer 0) composed
by logical components (Layer 1). Each planning run binds that template to a per-run
`ModelContext` to allocate PuLP vars and return query-only MILP fragments (constraints and
objective terms), which are assembled into a PuLP `LpProblem` and solved.
"""

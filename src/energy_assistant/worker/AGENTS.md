## Worker

Keep worker code independent of FastAPI; the API should observe worker state through
dependency wiring.

Concurrency and superseding-run semantics are part of the worker contract. Document
non-obvious state transitions beside the implementation.

from __future__ import annotations

from energy_assistant.ems.topology.policies.connection_policy import (
    ConnectionPolicy,
)


class Passthrough(ConnectionPolicy):
    """Explicit no-op policy; equivalent to the default segment transfer behavior."""

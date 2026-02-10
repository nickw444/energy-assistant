from __future__ import annotations

from energy_assistant.ems.topology.link_components.base import (
    FlowDirection,
    LinkComponent,
    validate_eta,
)


class TransportEfficiency(LinkComponent):
    """Directional transport efficiency applied by the receiving Bus balance."""

    def __init__(self, *, eta_a_to_b: float, eta_b_to_a: float) -> None:
        self.eta_a_to_b = validate_eta("eta_a_to_b", float(eta_a_to_b))
        self.eta_b_to_a = validate_eta("eta_b_to_a", float(eta_b_to_a))

    def transport_efficiency(self, direction: FlowDirection) -> float:
        return float(self.eta_a_to_b) if direction == "a_to_b" else float(self.eta_b_to_a)


class StorageEfficiency(LinkComponent):
    """Directional conversion efficiency applied by Storage SoC dynamics."""

    def __init__(self, *, eta_a_to_b: float, eta_b_to_a: float) -> None:
        self.eta_a_to_b = validate_eta("eta_a_to_b", float(eta_a_to_b))
        self.eta_b_to_a = validate_eta("eta_b_to_a", float(eta_b_to_a))

    def storage_efficiency(self, direction: FlowDirection) -> float:
        return float(self.eta_a_to_b) if direction == "a_to_b" else float(self.eta_b_to_a)

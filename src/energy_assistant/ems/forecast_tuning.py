from __future__ import annotations

import math
from collections.abc import Sequence


class ForecastMultiplier:
    """Apply a multiplier to an aligned forecast series.

    This is intentionally generic so it can be reused for any aligned series
    (power, prices, etc). Callers decide which slots to scale.
    """

    multiplier: float

    def __init__(self, multiplier: float) -> None:
        value = float(multiplier)
        if not math.isfinite(value):
            raise ValueError("multiplier must be finite")
        if value < 0.0:
            raise ValueError("multiplier must be >= 0")
        self.multiplier = value

    def apply(self, series: Sequence[float], *, skip_first_slot: bool = False) -> list[float]:
        if not series:
            return []
        if self.multiplier == 1.0:
            return list(series)
        if not skip_first_slot:
            return [float(value) * self.multiplier for value in series]

        first = float(series[0])
        rest = [float(value) * self.multiplier for value in series[1:]]
        return [first, *rest]

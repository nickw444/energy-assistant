from __future__ import annotations

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

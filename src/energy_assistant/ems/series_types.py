from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class EmsSeriesPoint(BaseModel):
    time: datetime
    value: float | bool

    model_config = ConfigDict(extra="forbid")

from __future__ import annotations

from energy_assistant.ems.horizon import Horizon


class Deferred[T]:
    """Mutable box for a value that is resolved per planning run.

    Components own and update these boxes; topology fragments read them when binding/building a
    per-run MILP snapshot.
    """

    def __init__(self, *, name: str, initial: T | None = None) -> None:
        self.name = str(name)
        self._value: T | None = initial

    def set(self, value: T) -> None:
        self._value = value

    def is_set(self) -> bool:
        return self._value is not None

    def get(self) -> T:
        if self._value is None:
            raise ValueError(f"Deferred value {self.name!r} is not set for this run")
        return self._value


class DeferredSeries[T]:
    """Mutable box for a per-slot series resolved per planning run."""

    def __init__(self, *, name: str, initial: list[T] | None = None) -> None:
        self.name = str(name)
        self._values: list[T] | None = None if initial is None else list(initial)

    def set(self, values: list[T]) -> None:
        self._values = list(values)

    def is_set(self) -> bool:
        return self._values is not None

    def get(self) -> list[T]:
        if self._values is None:
            raise ValueError(f"Deferred series {self.name!r} is not set for this run")
        return list(self._values)

    def get_for_horizon(self, horizon: Horizon) -> list[T]:
        values = self.get()
        if len(values) != int(horizon.num_intervals):
            raise ValueError(
                f"Deferred series {self.name!r} length {len(values)} "
                f"!= num_intervals={int(horizon.num_intervals)}"
            )
        return values

    def get_for_len(self, n: int) -> list[T]:
        values = self.get()
        if len(values) != int(n):
            raise ValueError(
                f"Deferred series {self.name!r} length {len(values)} != n={int(n)}"
            )
        return values

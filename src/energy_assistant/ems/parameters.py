from __future__ import annotations

from collections.abc import Sequence


class ScalarParameter[T]:
    def __init__(self, name: str) -> None:
        self.name = str(name)
        self._value: T | None = None

    def set(self, value: T) -> None:
        self._value = value

    def get(self) -> T:
        if self._value is None:
            raise ValueError(f"Scalar parameter {self.name!r} has not been set")
        return self._value


class SeriesParameter[T]:
    def __init__(self, name: str) -> None:
        self.name = str(name)
        self._values: list[T] | None = None

    def set(self, values: Sequence[T]) -> None:
        self._values = list(values)

    def get(self) -> list[T]:
        if self._values is None:
            raise ValueError(f"Series parameter {self.name!r} has not been set")
        return list(self._values)

from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T")
U = TypeVar("U")


class EntitySource[T, U](BaseModel):
    def mapper(self, state: T) -> U:
        raise NotImplementedError()    

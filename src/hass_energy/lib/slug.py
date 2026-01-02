from __future__ import annotations

import re
from collections.abc import Iterable


_slug_pattern = re.compile(r"[^a-zA-Z0-9]+")


def slugify(value: str) -> str:
    slug = _slug_pattern.sub("_", value.strip().lower())
    slug = slug.strip("_")
    return slug or "item"


def slug_map(names: Iterable[str], *, fallback: str = "item") -> dict[str, str]:
    mapping: dict[str, str] = {}
    used: set[str] = set()
    for name in sorted({str(name) for name in names}):
        base = slugify(name)
        if base == "item":
            base = fallback
        slug = base
        counter = 2
        while slug in used:
            slug = f"{base}_{counter}"
            counter += 1
        mapping[name] = slug
        used.add(slug)
    return mapping

---
paths:
  - "**/*.py"
  - "**/*.pyi"
---
<!-- Vendored from ECC (github.com/affaan-m/ECC) @ ceca28852e5b31edbbf66ebccc8fd163dd14208e :: rules/python/coding-style.md. MIT (c) Affaan Mustafa. -->

# Python Coding Style

> This file extends [common/coding-style.md](../common/coding-style.md) with Python specific content.

## Standards

- Follow **PEP 8** conventions
- Use **type annotations** on all function signatures

## Immutability

Prefer immutable data structures:

```python
from dataclasses import dataclass

@dataclass(frozen=True)
class User:
    name: str
    email: str

from typing import NamedTuple

class Point(NamedTuple):
    x: float
    y: float
```

## Formatting

> Project override: this repo uses **ruff alone** (`ruff format` + `ruff check`,
> which subsume black and isort) and **pyright** for typing. Do not install black
> or isort here — see `.claude/rules/python/coding-standards.md`.

## Reference

See skill: `python-patterns` for comprehensive Python idioms and patterns.

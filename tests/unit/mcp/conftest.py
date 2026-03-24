"""conftest.py for tests/unit/mcp — re-exports helpers for pytest discovery."""
from __future__ import annotations

# All shared helpers live in _helpers.py and can be imported directly.
# This file is intentionally minimal; it exists so pytest recognises the
# package and any future fixtures can be added here.

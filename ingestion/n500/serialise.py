"""One way to write JSON for the browser, so the NaN trap is closed once.

Python's `json.dumps` emits a bare `NaN` for a float nan. That is not valid
JSON: `JSON.parse` rejects the entire document, so a single missing number
takes the whole file down and the page renders empty behind a caught exception.

It happened twice — once in the screener snapshot, then again in the positions
file, because the fix lived in the job rather than in a shared helper. Writing
JSON for the front end goes through here now, and `allow_nan=False` makes an
unclean value fail loudly at write time instead of silently in the browser.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any


def clean(value: Any) -> Any:
    """Recursively replace NaN and infinities with None."""
    if isinstance(value, float):
        return None if math.isnan(value) or math.isinf(value) else value
    if isinstance(value, dict):
        return {k: clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    # pandas and numpy scalars answer to float() but are not float instances.
    if hasattr(value, "item"):
        try:
            return clean(value.item())
        except (ValueError, AttributeError):
            return value
    return value


def dumps(payload: Any, **kwargs: Any) -> str:
    return json.dumps(clean(payload), allow_nan=False, default=str, **kwargs)


def write(path: Path, payload: Any, **kwargs: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dumps(payload, **kwargs))
    return path

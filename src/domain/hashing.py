"""Deterministic identifiers. Replay must not depend on insertion order or UUID4."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from domain.timeutil import dump_ts


def _default(value: Any) -> str:
    if isinstance(value, datetime):
        return dump_ts(value)
    raise TypeError(f"cannot hash value of type {type(value).__name__}")


def stable_hash(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=_default)
    return hashlib.sha256(encoded.encode()).hexdigest()


def stable_id(*parts: Any) -> str:
    return stable_hash(list(parts))[:24]

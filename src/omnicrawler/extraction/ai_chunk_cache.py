"""Bounded in-memory reuse of validated extraction responses."""
from __future__ import annotations

import copy
import json
from collections import OrderedDict
from typing import Any


class ChunkCache:
    def __init__(self, entries: int, maximum_bytes: int = 8 * 1024 * 1024) -> None:
        if type(entries) is not int or not 0 <= entries <= 1024:
            raise ValueError("cache_entries must be between 0 and 1024")
        self.entries = entries
        self.maximum_bytes = maximum_bytes
        self._values: OrderedDict[str, tuple[dict[str, Any], int]] = OrderedDict()
        self._size = 0

    def get(self, key: str) -> dict[str, Any] | None:
        value = self._values.get(key)
        if value is None:
            return None
        self._values.move_to_end(key)
        result = copy.deepcopy(value[0])
        result.pop("accounting", None)
        result["cache_hit"] = True
        return result

    def put(self, key: str, value: dict[str, Any]) -> None:
        if not self.entries:
            return
        size = len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode())
        if size > self.maximum_bytes:
            return
        previous = self._values.pop(key, None)
        if previous:
            self._size -= previous[1]
        self._values[key] = (copy.deepcopy(value), size)
        self._size += size
        while len(self._values) > self.entries or self._size > self.maximum_bytes:
            _key, (_value, removed) = self._values.popitem(last=False)
            self._size -= removed

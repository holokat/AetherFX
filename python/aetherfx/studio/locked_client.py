"""A Client proxy that serialises every engine call through one lock.

The engine is a single JSON-RPC subprocess, so all callers in one process
(studio HTTP handlers, the MCP endpoint, a generator) must take turns. The
proxy rebinds the Client's typed wrapper methods to itself so that even
``proxy.create_effect(...)`` goes through the locked ``call``.
"""

from __future__ import annotations

import threading
from typing import Any

from ..client import Client


class LockedClient:
    def __init__(self, inner: Client, lock: threading.Lock | threading.RLock) -> None:
        self._inner = inner
        self._lock = lock

    def call(self, tool_name: str, /, **args: Any) -> Any:
        with self._lock:
            return self._inner.call(tool_name, **args)

    def call_with(self, name: str, args: dict[str, Any] | None = None) -> Any:
        with self._lock:
            return self._inner.call_with(name, args)

    def tools(self, *a: Any, **k: Any) -> Any:
        with self._lock:
            return self._inner.tools(*a, **k)

    def close(self) -> None:
        with self._lock:
            self._inner.close()

    def __enter__(self) -> "LockedClient":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def __getattr__(self, name: str) -> Any:
        attr = getattr(type(self._inner), name, None)
        if callable(attr) and not name.startswith("_"):
            return attr.__get__(self)  # bind the Client method to the proxy -> uses the locked call()
        return getattr(self._inner, name)

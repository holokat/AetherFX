import threading

from aetherfx.client import Client
from aetherfx.jsonrpc import InMemoryTransport
from aetherfx.studio.locked_client import LockedClient


def test_locked_client_routes_typed_wrappers_through_the_lock():
    calls: list[str] = []
    lock = threading.RLock()

    def ping(params):
        return {"ok": True}

    def create_effect(params):
        assert lock._is_owned()  # noqa: SLF001 - verify the lock is held during the engine call
        calls.append(params.get("name"))
        return {"effect_id": "fx_1", "effect": {"name": params.get("name")}}

    transport = InMemoryTransport({"ping": ping, "create_effect": create_effect,
                                   "tools/list": lambda p: {"tools": [{"name": "create_effect", "description": "x", "input_schema": {}}]}})
    inner = Client(transport=transport)
    proxy = LockedClient(inner, lock)
    proxy.create_effect(name="A")          # typed wrapper, rebound to the proxy
    proxy.call("create_effect", name="B")  # generic call
    proxy.call_with("create_effect", {"name": "C"})
    assert calls == ["A", "B", "C"]
    assert [t.name for t in proxy.tools()] == ["create_effect"]

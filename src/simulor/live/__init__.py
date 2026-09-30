"""Public live-trading integrations."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from simulor.execution.live.connectors import LongbridgeConnector
    from simulor.execution.live.longbridge import Longbridge

__all__: list[str] = []

_LIVE_EXPORTS = {
    "Longbridge": ("simulor.execution.live.longbridge", "Longbridge"),
    "LongbridgeConnector": ("simulor.execution.live.connectors", "LongbridgeConnector"),
}


def __getattr__(name: str) -> Any:
    try:
        module_name, attr_name = _LIVE_EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(f"module 'simulor.live' has no attribute {name!r}") from exc

    module = import_module(module_name)
    value = getattr(module, attr_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(list(globals().keys()) + __all__)

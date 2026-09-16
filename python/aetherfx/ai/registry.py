"""Pick a :class:`~aetherfx.ai.base.ReferenceAnalyzer` by name.

The AI layer is optional (docs/ARCHITECTURE.md section 9), so the default is
the offline :class:`~aetherfx.ai.mock.MockAnalyzer`: importing this module never
requires an API key or the ``anthropic`` package.
"""

from __future__ import annotations

import os
from typing import Any, Callable

from .base import ReferenceAnalyzer
from .mock import MockAnalyzer

__all__ = ["ANALYZER_ENV_VAR", "DEFAULT_ANALYZER", "available_analyzers", "get_analyzer"]

#: Environment variable consulted when ``get_analyzer`` is called with no name.
ANALYZER_ENV_VAR = "AETHERFX_ANALYZER"

#: The offline analyzer used when nothing is configured.
DEFAULT_ANALYZER = "mock"


def _build_claude(**kwargs: Any) -> ReferenceAnalyzer:
    """Import and construct the Claude adapter lazily."""
    from .claude import ClaudeAnalyzer  # noqa: PLC0415 - keeps `anthropic` optional

    return ClaudeAnalyzer(**kwargs)


_FACTORIES: dict[str, Callable[..., ReferenceAnalyzer]] = {
    "mock": lambda **kwargs: MockAnalyzer(**kwargs),
    "claude": _build_claude,
}


def available_analyzers() -> list[str]:
    """Return the registered analyzer names, sorted."""
    return sorted(_FACTORIES)


def get_analyzer(name: str | None = None, **kwargs: Any) -> ReferenceAnalyzer:
    """Return an analyzer by name.

    ``name`` defaults to ``$AETHERFX_ANALYZER`` and then to
    :data:`DEFAULT_ANALYZER`.  Extra keyword arguments go to the adapter's
    constructor, e.g. ``get_analyzer("claude", model="claude-opus-5")``.
    Raises :class:`ValueError` for an unknown name.
    """
    key = (name or os.environ.get(ANALYZER_ENV_VAR) or DEFAULT_ANALYZER).strip().lower()
    factory = _FACTORIES.get(key)
    if factory is None:
        raise ValueError(f"unknown analyzer {key!r}; available: {', '.join(available_analyzers())}")
    return factory(**kwargs)

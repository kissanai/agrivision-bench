"""Adapter registry: turn a string into a running diagnosis system.

Two ways to name an adapter, and the CLI accepts either interchangeably.

**A built-in name** -- ``echo``, ``openai``, ``http``::

    python -m agribench run --adapter openai

**An import path**, ``package.module:ClassName``, for anything else::

    python -m agribench run --adapter mycompany.bench:MyAdapter

The second form is the one that matters. It means benchmarking a system this
repository has never heard of requires no change to this repository: implement
:class:`~agribench.adapters.base.Adapter` anywhere on the Python path and name
it on the command line. No plugin manifest, no registration call, no fork.

Built-ins are imported lazily, so an ``import agribench.adapters`` costs
nothing and a broken optional adapter cannot take down the CLI.
"""

from __future__ import annotations

import importlib
from typing import Any

from agribench.adapters.base import (
    Adapter,
    AdapterConfigError,
    AdapterError,
    AdapterResponseError,
    Case,
    Prediction,
    TransientAdapterError,
    is_answered_category,
)

__all__ = [
    "BUILTIN_ADAPTERS",
    "Adapter",
    "AdapterConfigError",
    "AdapterError",
    "AdapterResponseError",
    "Case",
    "Prediction",
    "TransientAdapterError",
    "available_adapters",
    "is_answered_category",
    "load_adapter",
    "resolve_adapter",
]


#: Short name -> ``"module:Class"``. Aliases are intentional: people reach for
#: ``openai`` when they mean "the OpenAI wire format", whoever is serving it.
BUILTIN_ADAPTERS: dict[str, str] = {
    "echo": "agribench.adapters.echo:EchoAdapter",
    "openai": "agribench.adapters.openai_compatible:OpenAICompatibleAdapter",
    "openai-compatible": "agribench.adapters.openai_compatible:OpenAICompatibleAdapter",
    "openrouter": "agribench.adapters.openai_compatible:OpenAICompatibleAdapter",
    "vllm": "agribench.adapters.openai_compatible:OpenAICompatibleAdapter",
    "ollama": "agribench.adapters.openai_compatible:OpenAICompatibleAdapter",
    "http": "agribench.adapters.http_endpoint:HTTPEndpointAdapter",
    "http-endpoint": "agribench.adapters.http_endpoint:HTTPEndpointAdapter",
}

#: Names shown in ``--help``; the rest are aliases of these.
_PRIMARY = ("echo", "openai", "http")


def available_adapters() -> list[str]:
    """Built-in names, primary ones first, then aliases alphabetically."""
    aliases = sorted(set(BUILTIN_ADAPTERS) - set(_PRIMARY))
    return [*_PRIMARY, *aliases]


def resolve_adapter(spec: str) -> type[Adapter]:
    """Resolve a built-in name or ``"module:Class"`` path to an adapter class.

    Args:
        spec: A key of :data:`BUILTIN_ADAPTERS`, or a dotted import path and
            class name separated by ``:``. A dotted path with a ``.`` and no
            ``:`` is also accepted, splitting on the final dot, since that is a
            common thing to type.

    Returns:
        The adapter class, not an instance.

    Raises:
        AdapterConfigError: If the name is unknown, the module will not import,
            the attribute is missing, or the target is not an
            :class:`~agribench.adapters.base.Adapter` subclass. The message
            always names the fix.
    """
    spec = (spec or "").strip()
    if not spec:
        raise AdapterConfigError(
            f"no adapter given; try one of {', '.join(available_adapters())} "
            f"or an import path like mypkg.bench:MyAdapter"
        )

    target = BUILTIN_ADAPTERS.get(spec, spec)
    if ":" in target:
        module_name, _, class_name = target.partition(":")
    elif "." in target:
        module_name, _, class_name = target.rpartition(".")
    else:
        raise AdapterConfigError(
            f"unknown adapter {spec!r}. Built-ins: "
            f"{', '.join(available_adapters())}. For your own system, pass an "
            f"import path like mypkg.bench:MyAdapter"
        )

    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        raise AdapterConfigError(
            f"could not import {module_name!r} for adapter {spec!r}: {exc}. Is it "
            f"installed and on PYTHONPATH?"
        ) from exc

    try:
        candidate = getattr(module, class_name)
    except AttributeError as exc:
        raise AdapterConfigError(
            f"module {module_name!r} has no attribute {class_name!r}"
        ) from exc

    if not (isinstance(candidate, type) and issubclass(candidate, Adapter)):
        raise AdapterConfigError(
            f"{target} is not an agribench Adapter subclass. Subclass "
            f"agribench.adapters.base.Adapter and implement predict()."
        )
    return candidate


def load_adapter(spec: str, **options: Any) -> Adapter:
    """Resolve and construct an adapter.

    Args:
        spec: As :func:`resolve_adapter`.
        **options: Forwarded to the class's ``from_options``, which by default
            forwards to ``__init__``. These come from the CLI's repeatable
            ``--adapter-option key=value``.

    Returns:
        A constructed, not-yet-``setup()`` adapter.

    Raises:
        AdapterConfigError: If the spec will not resolve or the options are not
            accepted by the class.
    """
    return resolve_adapter(spec).from_options(**options)

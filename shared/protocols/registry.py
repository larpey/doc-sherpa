"""Plugin discovery registry.

A `classifier.yaml` says things like `destination: filesystem` — the config
loader needs a way to turn that name into a concrete plugin instance. That's
this module.

Implementation is deliberately tiny: a dict-of-dicts keyed by
`(category, plugin_name)`. Real plugin systems use entry points or
namespace packages; for the POC, an explicit registration call from each
plugin module is plenty. When we have ten plugins we'll switch to entry
points; today we have one or two.
"""

from __future__ import annotations

from typing import Any


class PluginRegistry:
    """Maps `(category, name)` -> a plugin factory (callable returning the plugin).

    Categories are strings like `"document_destination"`, `"ai_classifier"`,
    `"notification_sink"`, `"document_ingest"` — one per Protocol module.
    The registry does not validate that registered factories conform to
    their Protocol; that's an `isinstance(obj, Protocol)` check at the
    construction site, which gets a clearer error than the registry could.
    """

    def __init__(self) -> None:
        self._factories: dict[tuple[str, str], Any] = {}

    def register(self, category: str, name: str, factory: Any) -> None:
        """Register `factory` as the implementation of `name` in `category`.

        Raises `ValueError` on duplicate registration — silent overwrite is
        almost always a bug (two plugin modules picking the same name).
        """
        key = (category, name)
        if key in self._factories:
            raise ValueError(f"Plugin already registered: category={category!r} name={name!r}")
        self._factories[key] = factory

    def get(self, category: str, name: str) -> Any:
        """Look up a registered factory. Raises `KeyError` if missing.

        The error message includes the available names in the category to
        speed up the "typo in classifier.yaml" debugging case.
        """
        key = (category, name)
        if key not in self._factories:
            available = sorted(n for c, n in self._factories if c == category) or ["(none)"]
            raise KeyError(
                f"No plugin registered for category={category!r} name={name!r}. "
                f"Available: {', '.join(available)}"
            )
        return self._factories[key]

    def names(self, category: str) -> list[str]:
        """Return the registered plugin names in a category. Used by the CLI."""
        return sorted(n for c, n in self._factories if c == category)


registry = PluginRegistry()
"""Process-global registry. Plugins call `registry.register(...)` at import."""

"""Add entities for printers as they show up in the add-on."""

from __future__ import annotations

from collections.abc import Callable

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import SenderCoordinator


def add_per_printer(
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
    factory: Callable[[SenderCoordinator, str], list[Entity]],
) -> None:
    """Create entities now and for every printer that appears later (no reload)."""
    coordinator: SenderCoordinator = entry.runtime_data
    known: set[str] = set()

    def _check() -> None:
        new = set(coordinator.data.printers) - known
        if new:
            known.update(new)
            async_add_entities([e for s in sorted(new) for e in factory(coordinator, s)])

    _check()
    entry.async_on_unload(coordinator.async_add_listener(_check))

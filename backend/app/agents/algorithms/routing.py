from __future__ import annotations

import math
from typing import Protocol, TypeVar


class HasCoordinates(Protocol):
    latitude: float | None
    longitude: float | None


TCoordinate = TypeVar("TCoordinate", bound=HasCoordinates)


def haversine_km(first: HasCoordinates, second: HasCoordinates) -> float:
    """Return approximate great-circle distance between two coordinate objects."""
    if (
        first.latitude is None
        or first.longitude is None
        or second.latitude is None
        or second.longitude is None
    ):
        return 0.0 if first is second else 1_000_000.0

    radius_km = 6371.0
    lat1 = math.radians(first.latitude)
    lat2 = math.radians(second.latitude)
    delta_lat = math.radians(second.latitude - first.latitude)
    delta_lon = math.radians(second.longitude - first.longitude)

    value = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lon / 2) ** 2
    )
    return radius_km * 2 * math.atan2(math.sqrt(value), math.sqrt(1 - value))


def nearest_neighbor_order(items: list[TCoordinate]) -> list[TCoordinate]:
    """Order items by repeatedly selecting the nearest unvisited neighbor."""
    if len(items) <= 1:
        return list(items)

    ordered = [items[0]]
    remaining = list(items[1:])

    while remaining:
        current = ordered[-1]
        next_index, next_item = min(
            enumerate(remaining),
            key=lambda pair: haversine_km(current, pair[1]),
        )
        ordered.append(next_item)
        remaining.pop(next_index)

    return ordered

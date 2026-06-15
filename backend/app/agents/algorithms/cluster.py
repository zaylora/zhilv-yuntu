from __future__ import annotations

from app.agents.state import GeoPoint, SpotCandidate


def _candidate_sort_key(candidate: SpotCandidate) -> tuple[float, float, str]:
    latitude = candidate.latitude if candidate.latitude is not None else 0.0
    longitude = candidate.longitude if candidate.longitude is not None else 0.0
    return latitude, longitude, candidate.name


def _centroid(candidates: list[SpotCandidate]) -> GeoPoint:
    located = [
        candidate
        for candidate in candidates
        if candidate.latitude is not None and candidate.longitude is not None
    ]
    if not located:
        return GeoPoint(latitude=0.0, longitude=0.0)

    return GeoPoint(
        latitude=sum(candidate.latitude or 0.0 for candidate in located) / len(located),
        longitude=sum(candidate.longitude or 0.0 for candidate in located) / len(located),
    )


def cluster_spots_by_day(
    candidates: list[SpotCandidate],
    day_count: int,
) -> tuple[list[list[SpotCandidate]], list[GeoPoint]]:
    """
    Deterministically split geographically sorted candidates into day clusters.

    This is intentionally lightweight for the first LangGraph slice: it produces
    stable, testable clusters without introducing a numerical dependency.
    """
    safe_day_count = max(day_count, 1)
    clusters: list[list[SpotCandidate]] = [[] for _ in range(safe_day_count)]
    if not candidates:
        return clusters, [_centroid(cluster) for cluster in clusters]

    sorted_candidates = sorted(candidates, key=_candidate_sort_key)
    for index, candidate in enumerate(sorted_candidates):
        cluster_index = min(index * safe_day_count // len(sorted_candidates), safe_day_count - 1)
        clusters[cluster_index].append(candidate)

    return clusters, [_centroid(cluster) for cluster in clusters]

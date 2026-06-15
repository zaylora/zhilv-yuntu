from pathlib import Path
import sys


CURRENT_FILE = Path(__file__).resolve()
BACKEND_DIR = CURRENT_FILE.parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.agents.algorithms.cluster import cluster_spots_by_day  # noqa: E402
from app.agents.algorithms.routing import nearest_neighbor_order  # noqa: E402
from app.agents.state import SpotCandidate  # noqa: E402


def build_spot(name: str, latitude: float, longitude: float) -> SpotCandidate:
    return SpotCandidate(
        name=name,
        latitude=latitude,
        longitude=longitude,
        category="景点",
        is_indoor=False,
        ticket_estimate=20.0,
    )


def test_cluster_spots_by_day_keeps_nearby_candidates_together() -> None:
    """测试地理聚类会把相近候选分到同一天。"""
    candidates = [
        build_spot("古城北门", 25.7000, 100.1600),
        build_spot("古城南门", 25.7010, 100.1610),
        build_spot("洱海码头", 25.8500, 100.2800),
        build_spot("双廊观景台", 25.8510, 100.2810),
    ]

    clusters, centroids = cluster_spots_by_day(candidates, day_count=2)

    assert len(clusters) == 2
    assert len(centroids) == 2
    grouped_names = [set(candidate.name for candidate in group) for group in clusters]
    assert {"古城北门", "古城南门"} in grouped_names
    assert {"洱海码头", "双廊观景台"} in grouped_names


def test_nearest_neighbor_order_prefers_the_next_closest_spot() -> None:
    """测试顺路排序会按最近邻选择下一个景点。"""
    first = build_spot("起点", 25.7000, 100.1600)
    farthest = build_spot("远点", 25.9000, 100.4000)
    middle = build_spot("中点", 25.7200, 100.1800)

    ordered = nearest_neighbor_order([first, farthest, middle])

    assert [spot.name for spot in ordered] == ["起点", "中点", "远点"]

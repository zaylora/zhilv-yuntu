"""map_service 单元测试。

使用 monkeypatch 隔离真实 HTTP 请求和 Redis 缓存。
"""
from pathlib import Path
import sys

# 允许测试文件直接导入 backend/app 下的模块。
CURRENT_FILE = Path(__file__).resolve()
BACKEND_DIR = CURRENT_FILE.parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import pytest  # noqa: E402
import app.services.map_service as map_service  # noqa: E402
from app.services.map_service import search_places  # noqa: E402


# ---------------------------------------------------------------------------
# 辅助 fixtures
# ---------------------------------------------------------------------------

def _make_amap_poi_payload(pois: list[dict]) -> dict:
    """构造高德搜索 API 的标准返回结构。"""
    return {"status": "1", "pois": pois}


def _make_poi_with_biz_ext(
    name: str = "测试餐厅",
    rating: str = "4.5",
    cost: str = "88",
) -> dict:
    """构造带 biz_ext 的 POI 数据。"""
    return {
        "name": name,
        "address": "测试路1号",
        "cityname": "北京市",
        "adname": "朝阳区",
        "type": "050101",
        "id": "poi_001",
        "location": "116.397128,39.916527",
        "photos": [],
        "biz_ext": {"rating": rating, "cost": cost},
    }


# ---------------------------------------------------------------------------
# 测试: search_places 新增 types / citylimit / biz_ext 解析
# ---------------------------------------------------------------------------

def test_search_places_includes_types_citylimit_and_rating_cost(monkeypatch):
    """
    验证:
    1. 传入 types="050000" 时，请求 params 包含 types 和 citylimit。
    2. 返回结果包含正确解析的 rating / avg_cost。
    3. 缓存 key 因 types 不同而不同（set_cached_json 被调用时的 key 含 types）。
    """
    captured_params: list[dict] = []
    captured_cache_sets: list[tuple] = []

    def fake_request_amap(path: str, params: dict) -> dict:
        captured_params.append(dict(params))
        poi = _make_poi_with_biz_ext(rating="4.5", cost="88")
        return _make_amap_poi_payload([poi])

    def fake_get_cached_json(key: str):
        return None  # 始终 cache miss

    def fake_set_cached_json(key: str, value, expire_seconds=None):
        captured_cache_sets.append((key, value))

    monkeypatch.setattr(map_service, "_request_amap", fake_request_amap)
    monkeypatch.setattr(map_service, "get_cached_json", fake_get_cached_json)
    monkeypatch.setattr(map_service, "set_cached_json", fake_set_cached_json)

    # 调用带 types 的 search_places
    results = search_places(keyword="火锅", city="北京", page_size=3, types="050000")

    # 1. 请求 params 应包含 types 和 citylimit
    assert len(captured_params) == 1, "应发起 1 次 _request_amap 调用"
    req_params = captured_params[0]
    assert "types" in req_params, "请求 params 缺少 types 字段"
    assert req_params["types"] == "050000"
    assert "citylimit" in req_params, "请求 params 缺少 citylimit 字段"
    assert req_params["citylimit"] == "true"

    # 2. 返回结果包含 rating / avg_cost
    assert len(results) == 1
    result = results[0]
    assert "rating" in result, "结果缺少 rating 字段"
    assert "avg_cost" in result, "结果缺少 avg_cost 字段"
    assert result["rating"] == pytest.approx(4.5)
    assert result["avg_cost"] == pytest.approx(88.0)

    # 3. 缓存 key 中应包含 types 信息（"050000"），
    #    再调用一次不同 types 时，cache key 不同不会串用
    assert len(captured_cache_sets) == 1
    cache_key_with_types = captured_cache_sets[0][0]
    assert "050000" in cache_key_with_types, f"缓存 key 未含 types: {cache_key_with_types}"

    # 重置 captured 数据，用不同 types 调用，验证 key 不同
    captured_params.clear()
    captured_cache_sets.clear()
    results2 = search_places(keyword="火锅", city="北京", page_size=3, types="110000")
    cache_key_different_types = captured_cache_sets[0][0]
    assert cache_key_with_types != cache_key_different_types, (
        "types 不同时缓存 key 应不同，否则会串味"
    )


def test_search_places_biz_ext_list_fallback(monkeypatch):
    """验证 biz_ext 为空列表时 rating/avg_cost 返回 None 不报错。"""
    def fake_request_amap(path: str, params: dict) -> dict:
        poi = {
            "name": "测试地点",
            "address": "某路",
            "cityname": "上海市",
            "adname": "浦东新区",
            "type": "110101",
            "id": "poi_002",
            "location": "121.4737,31.2304",
            "photos": [],
            "biz_ext": [],  # 高德有时返回空列表
        }
        return _make_amap_poi_payload([poi])

    monkeypatch.setattr(map_service, "_request_amap", fake_request_amap)
    monkeypatch.setattr(map_service, "get_cached_json", lambda k: None)
    monkeypatch.setattr(map_service, "set_cached_json", lambda k, v, expire_seconds=None: None)

    results = search_places(keyword="外滩", city="上海", types="110000")
    assert len(results) == 1
    assert results[0]["rating"] is None
    assert results[0]["avg_cost"] is None


def test_search_places_backward_compat_no_types(monkeypatch):
    """验证不传 types/citylimit 时，现有调用方不受影响（向后兼容）。"""
    captured_params: list[dict] = []

    def fake_request_amap(path: str, params: dict) -> dict:
        captured_params.append(dict(params))
        return _make_amap_poi_payload([])

    monkeypatch.setattr(map_service, "_request_amap", fake_request_amap)
    monkeypatch.setattr(map_service, "get_cached_json", lambda k: None)
    monkeypatch.setattr(map_service, "set_cached_json", lambda k, v, expire_seconds=None: None)

    # 旧调用方式：不传 types 参数
    results = search_places(keyword="故宫", city="北京", page_size=1)

    assert isinstance(results, list)
    # types 不在 params 里（或不存在），不应崩溃
    req_params = captured_params[0]
    assert "types" not in req_params, "不传 types 时，请求 params 不应含 types 字段"

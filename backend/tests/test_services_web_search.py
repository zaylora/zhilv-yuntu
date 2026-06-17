"""web_search_service 单元测试。

使用 monkeypatch 隔离 BOCHA_ENABLED 标志、httpx 网络请求和 Redis 缓存。
绝不调用真实 API 或 Redis。
"""
from pathlib import Path
import sys

# 允许测试文件直接导入 backend/app 下的模块。
CURRENT_FILE = Path(__file__).resolve()
BACKEND_DIR = CURRENT_FILE.parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import pytest  # noqa: E402
import httpx  # noqa: E402


# ---------------------------------------------------------------------------
# 辅助: 构造假博查响应
# ---------------------------------------------------------------------------

def _make_bocha_response(results: list[dict]) -> dict:
    """构造博查 API 的标准响应结构。"""
    return {
        "data": {
            "webPages": {
                "value": results
            }
        }
    }


def _make_bocha_result(name: str, url: str, snippet: str) -> dict:
    return {"name": name, "url": url, "snippet": snippet}


# ---------------------------------------------------------------------------
# 测试: 禁用时返回空列表
# ---------------------------------------------------------------------------

def test_web_search_returns_empty_when_disabled(monkeypatch):
    """BOCHA_ENABLED=False 时，search_web 应直接返回 []，不发起任何 HTTP 请求。"""
    import app.services.web_search_service as web_search_service

    monkeypatch.setattr(web_search_service, "BOCHA_ENABLED", False)
    monkeypatch.setattr(web_search_service, "get_cached_json", lambda k: None)
    monkeypatch.setattr(web_search_service, "set_cached_json", lambda k, v, expire_seconds=None: None)

    result = web_search_service.search_web("北京美食推荐")
    assert result == [], f"禁用时应返回 [], 实际返回: {result}"


def test_web_search_returns_empty_when_no_api_key(monkeypatch):
    """有 BOCHA_ENABLED=True 但无 API key 时，search_web 应返回 []。"""
    import app.services.web_search_service as web_search_service

    monkeypatch.setattr(web_search_service, "BOCHA_ENABLED", True)
    monkeypatch.setattr(web_search_service, "BOCHA_API_KEY", "")
    monkeypatch.setattr(web_search_service, "get_cached_json", lambda k: None)
    monkeypatch.setattr(web_search_service, "set_cached_json", lambda k, v, expire_seconds=None: None)

    result = web_search_service.search_web("北京美食推荐")
    assert result == []


# ---------------------------------------------------------------------------
# 测试: 启用时正常路径返回标准化片段
# ---------------------------------------------------------------------------

def test_web_search_returns_normalized_results_when_enabled(monkeypatch):
    """BOCHA_ENABLED=True 且 API key 存在，httpx 返回正常响应时，返回标准化片段。"""
    import app.services.web_search_service as web_search_service

    fake_results = [
        _make_bocha_result("北京十大必吃美食", "https://example.com/1", "推荐北京烤鸭..."),
        _make_bocha_result("北京老字号餐厅", "https://example.com/2", "全聚德、便宜坊..."),
    ]
    bocha_payload = _make_bocha_response(fake_results)

    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return bocha_payload

    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def post(self, url, **kwargs):
            return FakeResponse()

    monkeypatch.setattr(web_search_service, "BOCHA_ENABLED", True)
    monkeypatch.setattr(web_search_service, "BOCHA_API_KEY", "test-key-123")
    monkeypatch.setattr(web_search_service, "get_cached_json", lambda k: None)
    monkeypatch.setattr(web_search_service, "set_cached_json", lambda k, v, expire_seconds=None: None)
    monkeypatch.setattr(web_search_service.httpx, "Client", lambda **kwargs: FakeClient())

    results = web_search_service.search_web("北京美食", count=2)

    assert len(results) == 2
    assert results[0]["title"] == "北京十大必吃美食"
    assert results[0]["url"] == "https://example.com/1"
    assert results[0]["snippet"] == "推荐北京烤鸭..."
    assert results[1]["title"] == "北京老字号餐厅"


# ---------------------------------------------------------------------------
# 测试: 失败降级 - httpx 抛异常
# ---------------------------------------------------------------------------

def test_web_search_returns_empty_on_http_exception(monkeypatch):
    """httpx 抛出网络异常时，search_web 应降级返回 []，不传播异常。"""
    import app.services.web_search_service as web_search_service

    class BrokenClient:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def post(self, url, **kwargs):
            raise httpx.TimeoutException("连接超时")

    monkeypatch.setattr(web_search_service, "BOCHA_ENABLED", True)
    monkeypatch.setattr(web_search_service, "BOCHA_API_KEY", "test-key-123")
    monkeypatch.setattr(web_search_service, "get_cached_json", lambda k: None)
    monkeypatch.setattr(web_search_service, "set_cached_json", lambda k, v, expire_seconds=None: None)
    monkeypatch.setattr(web_search_service.httpx, "Client", lambda **kwargs: BrokenClient())

    result = web_search_service.search_web("北京美食")
    assert result == [], "HTTP 异常时应返回 []，不抛异常"


def test_web_search_returns_empty_on_non_2xx(monkeypatch):
    """HTTP 返回 5xx 时，search_web 应降级返回 []。"""
    import app.services.web_search_service as web_search_service

    class ServerErrorResponse:
        status_code = 500

        def raise_for_status(self):
            raise httpx.HTTPStatusError(
                "Server Error",
                request=None,
                response=None,
            )

        def json(self):
            return {}

    class ServerErrorClient:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def post(self, url, **kwargs):
            return ServerErrorResponse()

    monkeypatch.setattr(web_search_service, "BOCHA_ENABLED", True)
    monkeypatch.setattr(web_search_service, "BOCHA_API_KEY", "test-key-123")
    monkeypatch.setattr(web_search_service, "get_cached_json", lambda k: None)
    monkeypatch.setattr(web_search_service, "set_cached_json", lambda k, v, expire_seconds=None: None)
    monkeypatch.setattr(web_search_service.httpx, "Client", lambda **kwargs: ServerErrorClient())

    result = web_search_service.search_web("北京美食")
    assert result == []


# ---------------------------------------------------------------------------
# 测试: 缓存命中直接返回
# ---------------------------------------------------------------------------

def test_web_search_returns_cached_result(monkeypatch):
    """缓存命中时应直接返回缓存结果，不发起 HTTP 请求。"""
    import app.services.web_search_service as web_search_service

    cached = [{"title": "缓存结果", "url": "https://cache.example.com", "snippet": "来自缓存"}]
    http_called = []

    class ShouldNotBeCalledClient:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def post(self, url, **kwargs):
            http_called.append(True)
            raise RuntimeError("不应该发起真实 HTTP 请求")

    monkeypatch.setattr(web_search_service, "BOCHA_ENABLED", True)
    monkeypatch.setattr(web_search_service, "BOCHA_API_KEY", "test-key-123")
    monkeypatch.setattr(web_search_service, "get_cached_json", lambda k: cached)
    monkeypatch.setattr(web_search_service, "set_cached_json", lambda k, v, expire_seconds=None: None)
    monkeypatch.setattr(web_search_service.httpx, "Client", lambda **kwargs: ShouldNotBeCalledClient())

    result = web_search_service.search_web("北京美食")
    assert result == cached
    assert not http_called, "缓存命中时不应发起 HTTP 请求"


# ---------------------------------------------------------------------------
# 测试: 失败降级 - 响应 JSON 解析失败
# ---------------------------------------------------------------------------

def test_web_search_returns_empty_on_invalid_json(monkeypatch):
    """响应 JSON 解析失败时，search_web 应降级返回 []，不传播异常。"""
    import app.services.web_search_service as web_search_service

    class BadJsonResponse:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            raise ValueError("Invalid JSON: Expecting value")

    class BadJsonClient:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def post(self, url, **kwargs):
            return BadJsonResponse()

    monkeypatch.setattr(web_search_service, "BOCHA_ENABLED", True)
    monkeypatch.setattr(web_search_service, "BOCHA_API_KEY", "test-key-123")
    monkeypatch.setattr(web_search_service, "get_cached_json", lambda k: None)
    monkeypatch.setattr(web_search_service, "set_cached_json", lambda k, v, expire_seconds=None: None)
    monkeypatch.setattr(web_search_service.httpx, "Client", lambda **kwargs: BadJsonClient())

    result = web_search_service.search_web("北京美食")
    assert result == [], "JSON 解析失败时应返回 []，不抛异常"

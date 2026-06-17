"""博查（Bocha）网页搜索服务。

默认关闭（BOCHA_ENABLED=false）。任何失败路径均返回 []，绝不传播异常，
保证行程生成流程不受干扰。

对外唯一接口：
    search_web(query: str, count: int = 5) -> list[dict[str, str]]

返回片段列表，每项含 title / url / snippet 三个 str 键。
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import (
    BOCHA_API_KEY,
    BOCHA_BASE_URL,
    BOCHA_ENABLED,
    BOCHA_TIMEOUT_SECONDS,
    REDIS_DEFAULT_TTL_SECONDS,
)
from app.services.cache_service import get_cached_json, set_cached_json


logger = logging.getLogger(__name__)

# 博查搜索结果的缓存 TTL，复用 Redis 默认 TTL
_BOCHA_CACHE_TTL = REDIS_DEFAULT_TTL_SECONDS


def _normalize_query(query: str) -> str:
    """对查询文本做简单标准化，用于构造缓存 key。"""
    return query.strip().lower()


def search_web(query: str, count: int = 5) -> list[dict[str, str]]:
    """使用博查 Web Search API 搜索网页，返回标准化片段列表。

    Args:
        query: 搜索查询文本。
        count: 期望返回的结果条数，默认 5。

    Returns:
        list[dict[str, str]]: 每项含 title / url / snippet 三个键。
        任何失败情况下返回 []。
    """
    # 降级铁律 1：功能开关未启用或无 API key，直接返回 []
    if not BOCHA_ENABLED or not BOCHA_API_KEY:
        logger.debug("bocha web search disabled or no api key, skipping")
        return []

    # 检查缓存
    cache_key = f"web:search:{_normalize_query(query)}:{count}"
    cached = get_cached_json(cache_key)
    if cached is not None:
        logger.info("bocha web search cache hit: query=%s", query)
        return cached

    logger.info("bocha web search cache miss: query=%s", query)

    try:
        results = _do_search(query=query, count=count)
    except Exception as exc:
        # 降级铁律 2：任何异常都不传播
        logger.warning("bocha web search failed, returning []: %s", exc)
        return []

    # 写缓存（写缓存失败也不影响返回值）
    try:
        set_cached_json(cache_key, results, expire_seconds=_BOCHA_CACHE_TTL)
    except Exception as exc:
        logger.warning("bocha web search set cache failed: %s", exc)

    return results


def _do_search(query: str, count: int) -> list[dict[str, str]]:
    """实际调用博查 API 并返回标准化结果。

    任何非预期结构都做宽容处理（字段缺失则跳过该条）。
    该函数可抛异常，由 search_web 兜住。
    """
    url = f"{BOCHA_BASE_URL}/web-search"
    headers = {
        "Authorization": f"Bearer {BOCHA_API_KEY}",
        "Content-Type": "application/json",
    }
    body: dict[str, Any] = {"query": query, "count": count}

    with httpx.Client(timeout=BOCHA_TIMEOUT_SECONDS) as client:
        response = client.post(url, headers=headers, json=body)
        response.raise_for_status()
        payload = response.json()

    # 解析响应：data.webPages.value 为结果数组
    data = payload.get("data") or {}
    web_pages = data.get("webPages") or {}
    raw_items = web_pages.get("value")

    if not isinstance(raw_items, list):
        logger.warning("bocha response missing data.webPages.value list")
        return []

    results: list[dict[str, str]] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        item_url = item.get("url")
        snippet = item.get("snippet")
        # 任何字段缺失则跳过该条
        if not name or not item_url or not snippet:
            continue
        results.append(
            {
                "title": str(name),
                "url": str(item_url),
                "snippet": str(snippet),
            }
        )

    return results

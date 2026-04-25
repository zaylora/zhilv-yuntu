import logging
import re

from app.config import REDIS_RAG_TTL_SECONDS
from app.rag.vector_db import search_guide_chunks
from app.services.cache_service import get_cached_json, set_cached_json


logger = logging.getLogger(__name__)


def _normalize_cache_text(value: str) -> str:
    """把检索 query 做简单标准化，避免大小写和空格造成重复 key。"""
    return " ".join(value.strip().lower().split())


def _extract_query_keywords(query: str) -> list[str]:
    """从 query 中切出用于轻量重排序的关键词。"""
    raw_parts = re.split(r"[\s,，。；;、]+", query)
    return [part.strip() for part in raw_parts if part.strip()]


def _contains_any(text: str, keywords: list[str]) -> bool:
    return any(keyword in text for keyword in keywords)


def _score_chunk_for_rerank(query: str, chunk: dict[str, str]) -> int:
    """根据 query 关键词对召回片段做轻量打分。"""
    title = chunk.get("title", "")
    text = chunk.get("text", "")
    combined_text = f"{title}\n{text}"
    reasons: list[str] = []

    score = 0
    for keyword in _extract_query_keywords(query):
        if keyword in title:
            score += 3
            reasons.append(f"title+3:{keyword}")
        if keyword in text:
            score += 1
            reasons.append(f"text+1:{keyword}")

    # 文档开头通常是低信息量噪声片段。
    if title == "文档开头":
        score -= 8
        reasons.append("noise-8:文档开头")

    # 行程类片段更适合承接“景点 / 行程 / 推荐”类请求。
    if "行程" in title:
        score += 4
        reasons.append("domain+4:行程标题")

    # 餐饮/预算类片段在“日落/拍照/轻松”这类主目标下通常不是最优候选。
    if _contains_any(title, ["餐饮", "预算"]) and not _contains_any(
        combined_text,
        ["日落", "傍晚", "拍照", "摄影", "出片", "洱海", "双廊", "慢节奏"],
    ):
        score -= 3
        reasons.append("domain-3:餐饮预算弱相关")

    chunk["rerank_reasons"] = reasons
    return score


def rerank_guide_chunks(
    query: str,
    matched_chunks: list[dict[str, str]],
    top_k: int,
) -> list[dict[str, str]]:
    """对召回候选做轻量重排序，并裁剪到最终 top_k。"""
    scored_chunks: list[tuple[int, int, dict[str, str]]] = []
    for index, chunk in enumerate(matched_chunks):
        enriched_chunk = dict(chunk)
        score = _score_chunk_for_rerank(query, enriched_chunk)
        enriched_chunk["rerank_score"] = score
        scored_chunks.append((score, -index, enriched_chunk))

    scored_chunks.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [chunk for _, _, chunk in scored_chunks[:top_k]]


def retrieve_travel_guide_chunks(query: str, top_k: int = 3) -> list[dict[str, str]]:
    """返回带轻量 rerank 的原始攻略片段，便于调试和上层复用。"""
    candidate_k = max(top_k * 2, 6)
    matched_chunks = search_guide_chunks(query=query, top_k=candidate_k)
    return rerank_guide_chunks(query=query, matched_chunks=matched_chunks, top_k=top_k)


def retrieve_travel_guide(query: str, top_k: int = 3) -> list[str]:
    """返回最相关的攻略片段，供上层组装上下文。"""
    cache_key = f"rag:guide:{_normalize_cache_text(query)}:{top_k}"
    cached_value = get_cached_json(cache_key)
    if cached_value is not None:
        logger.info("rag cache hit: query=%s top_k=%s", query, top_k)
        return [str(item) for item in cached_value]
    logger.info("rag cache miss: query=%s top_k=%s", query, top_k)

    matched_chunks = retrieve_travel_guide_chunks(query=query, top_k=top_k)

    results: list[str] = []
    for chunk in matched_chunks:
        results.append(
            f"[来源: {chunk['source']} | 标题: {chunk['title']}]\n{chunk['text']}"
        )

    set_cached_json(cache_key, results, expire_seconds=REDIS_RAG_TTL_SECONDS)
    return results

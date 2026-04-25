from app.rag.retriever import retrieve_travel_guide


# rag_tool.py 自己不直接检索，
# 它只负责把“旅行规划语义”转成“检索查询”。
def _append_unique(parts: list[str], value: str) -> None:
    normalized = value.strip()
    if not normalized:
        return
    if normalized not in parts:
        parts.append(normalized)


def _extract_note_keywords(special_notes: str | None) -> list[str]:
    """从用户备注里提炼更适合检索的关键词，而不是直接拼整句。"""
    if not special_notes:
        return []

    keywords: list[str] = []
    note = special_notes.strip()

    rule_keywords = [
        (("日落", "傍晚"), ["日落", "傍晚", "洱海", "双廊"]),
        (("日出", "清晨"), ["日出", "才村", "龙龛"]),
        (("拍照", "出片", "摄影"), ["拍照", "摄影", "出片"]),
        (("美食", "小吃", "吃"), ["美食", "小吃"]),
        (("轻松", "慢节奏", "休闲"), ["轻松", "慢节奏", "休闲"]),
        (("不想太早起床", "睡到自然醒"), ["轻松", "慢节奏"]),
        (("古镇",), ["古镇", "大理古城", "喜洲古镇"]),
        (("骑行",), ["骑行", "洱海生态廊道"]),
    ]

    for triggers, values in rule_keywords:
        if any(trigger in note for trigger in triggers):
            for value in values:
                _append_unique(keywords, value)

    return keywords


def build_destination_query(
    destination: str,
    preferences: list[str] | None = None,
    pace: str | None = None,
    special_notes: str | None = None,
) -> str:
    """把目的地、偏好、节奏和备注改写成更贴近检索场景的 query。"""
    parts: list[str] = [destination]

    if preferences:
        for preference in preferences:
            _append_unique(parts, preference)

    if pace:
        _append_unique(parts, pace)

    for keyword in _extract_note_keywords(special_notes):
        _append_unique(parts, keyword)

    # 为向量检索补一些更稳定的旅游语义词，帮助召回景点、行程、攻略等片段。
    for stable_term in ["景点", "行程", "攻略", "推荐"]:
        _append_unique(parts, stable_term)

    return " ".join(part for part in parts if part).strip()


def _build_destination_query(
    destination: str,
    preferences: list[str] | None = None,
    pace: str | None = None,
    special_notes: str | None = None,
) -> str:
    """兼容旧调用，内部转到公开的 query 构造函数。"""
    return build_destination_query(
        destination=destination,
        preferences=preferences,
        pace=pace,
        special_notes=special_notes,
    )


def get_destination_guide_context(
    destination: str,
    preferences: list[str] | None = None,
    pace: str | None = None,
    special_notes: str | None = None,
    top_k: int = 5,
) -> list[str]:
    """根据目的地和偏好返回本地攻略里的相关片段。"""
    query = build_destination_query(
        destination=destination,
        preferences=preferences,
        pace=pace,
        special_notes=special_notes,
    )
    return retrieve_travel_guide(query=query, top_k=top_k)

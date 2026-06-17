from __future__ import annotations

from datetime import date as DateType, datetime

from pydantic import BaseModel, Field, field_validator


class TripRequest(BaseModel):
    """用于生成新行程的请求体。"""

    destination: str = Field(..., description="目的地，例如大理")
    start_date: DateType = Field(..., description="出行开始日期")
    end_date: DateType = Field(..., description="出行结束日期")
    travelers: int = Field(..., ge=1, description="出行人数")
    budget: float = Field(..., ge=0, description="总预算")
    preferences: list[str] = Field(default_factory=list, description="旅行偏好标签")
    pace: str | None = Field(default=None, description="旅行节奏，例如轻松、适中、紧凑")
    dietary_preferences: list[str] = Field(
        default_factory=list,
        description="饮食偏好或忌口",
    )
    hotel_level: str | None = Field(default=None, description="酒店档次偏好")
    special_notes: str | None = Field(default=None, description="额外要求")


class TripEditRequest(BaseModel):
    """用于修改已有行程的请求体。"""

    trip_id: str = Field(..., description="需要编辑的行程 ID")
    current_itinerary: "Itinerary" = Field(..., description="当前完整 itinerary")
    user_instruction: str = Field(..., description="用户新的修改要求")
    edit_scope: str | None = Field(default=None, description="编辑范围")
    preserve_constraints: list[str] = Field(
        default_factory=list,
        description="需要尽量保留的条件",
    )


class TripSaveRequest(BaseModel):
    """用于保存当前 itinerary 的请求体。"""

    trip_id: str = Field(..., description="需要保存的行程 ID")
    itinerary: "Itinerary" = Field(..., description="完整行程数据")
    user_id: str | None = Field(default=None, description="用户 ID，当前版本可留空")


class SpotItem(BaseModel):
    """单个景点安排。"""

    name: str = Field(..., description="景点名称")
    start_time: str | None = Field(default=None, description="开始时间")
    end_time: str | None = Field(default=None, description="结束时间")
    description: str | None = Field(default=None, description="景点安排说明")
    estimated_cost: float = Field(default=0.0, ge=0, description="预估花费")
    location: str | None = Field(default=None, description="景点位置描述")
    image_url: str | None = Field(default=None, description="景点图片地址")
    address: str | None = Field(default=None, description="景点详细地址")
    latitude: float | None = Field(default=None, description="景点纬度")
    longitude: float | None = Field(default=None, description="景点经度")
    poi_id: str | None = Field(default=None, description="地图服务返回的 POI 标识")
    is_indoor: bool | None = Field(default=None, description="是否更适合雨天或室内安排")


class MealItem(BaseModel):
    """单个餐饮安排。"""

    name: str = Field(..., description="餐厅或餐饮建议名称")
    meal_type: str = Field(..., description="早餐、午餐、晚餐等")
    estimated_cost: float = Field(default=0.0, ge=0, description="预估花费")
    notes: str | None = Field(default=None, description="补充说明")


class HotelItem(BaseModel):
    """单个住宿安排。"""

    name: str = Field(..., description="酒店名称")
    level: str | None = Field(default=None, description="酒店档次")
    estimated_cost: float = Field(default=0.0, ge=0, description="预估花费")
    location: str | None = Field(default=None, description="酒店位置")
    address: str | None = Field(default=None, description="酒店详细地址")
    latitude: float | None = Field(default=None, description="酒店纬度")
    longitude: float | None = Field(default=None, description="酒店经度")


class TransportItem(BaseModel):
    """单段交通安排。"""

    mode: str = Field(..., description="交通方式，例如步行、打车、公交")
    from_place: str | None = Field(default=None, description="出发地")
    to_place: str | None = Field(default=None, description="目的地")
    estimated_cost: float = Field(default=0.0, ge=0, description="预估花费")
    duration: str | None = Field(default=None, description="预计耗时")
    distance_km: float | None = Field(default=None, ge=0, description="预计距离，单位公里")
    estimated_minutes: int | None = Field(default=None, ge=0, description="预计耗时，单位分钟")


class BudgetBreakdown(BaseModel):
    """预算拆分。"""

    transport: float = Field(default=0.0, ge=0, description="交通预算")
    hotel: float = Field(default=0.0, ge=0, description="住宿预算")
    meals: float = Field(default=0.0, ge=0, description="餐饮预算")
    tickets: float = Field(default=0.0, ge=0, description="门票预算")
    other: float = Field(default=0.0, ge=0, description="其他预算")
    total: float = Field(default=0.0, ge=0, description="预算总计")


class DayPlan(BaseModel):
    """单日行程安排。"""

    day_index: int = Field(..., ge=1, description="第几天")
    date: DateType | None = Field(default=None, description="当天日期")
    theme: str | None = Field(default=None, description="当天主题")
    spots: list[SpotItem] = Field(default_factory=list, description="景点安排")
    meals: list[MealItem] = Field(default_factory=list, description="餐饮安排")
    hotel: HotelItem | None = Field(default=None, description="住宿安排")
    transport: list[TransportItem] = Field(default_factory=list, description="交通安排")
    notes: list[str] = Field(default_factory=list, description="补充说明")


class TokenUsage(BaseModel):
    """LLM 调用的 token 消耗统计。"""

    rewrite_prompt_tokens: int = Field(default=0, ge=0, description="Query Rewrite 输入 token")
    rewrite_completion_tokens: int = Field(default=0, ge=0, description="Query Rewrite 输出 token")
    # Historical compatibility fields. The LangGraph planner keeps these at 0.
    embedding_prompt_tokens: int = Field(default=0, ge=0, description="历史兼容输入 token")
    embedding_completion_tokens: int = Field(default=0, ge=0, description="历史兼容输出 token")
    planner_prompt_tokens: int = Field(default=0, ge=0, description="行程生成输入 token")
    planner_completion_tokens: int = Field(default=0, ge=0, description="行程生成输出 token")
    rerank_prompt_tokens: int = Field(default=0, ge=0, description="历史兼容输入 token")
    rerank_completion_tokens: int = Field(default=0, ge=0, description="历史兼容输出 token")

    @property
    def total_prompt_tokens(self) -> int:
        return (
            self.rewrite_prompt_tokens
            + self.embedding_prompt_tokens
            + self.planner_prompt_tokens
            + self.rerank_prompt_tokens
        )

    @property
    def total_completion_tokens(self) -> int:
        return (
            self.rewrite_completion_tokens
            + self.embedding_completion_tokens
            + self.planner_completion_tokens
            + self.rerank_completion_tokens
        )

    @property
    def total_tokens(self) -> int:
        return self.total_prompt_tokens + self.total_completion_tokens


class NarratorResponse(BaseModel):
    """Structured response produced by the LLM narrator agent."""

    summary: str = Field(..., description="面向游客的整趟行程概述")
    tips: list[str] = Field(default_factory=list, description="面向游客的实用建议")
    day_titles: dict[str, str] = Field(default_factory=dict, description="按 day_index 字符串索引的每日标题")
    day_notes: dict[str, list[str]] = Field(default_factory=dict, description="按 day_index 字符串索引的每日提示")


# ──────────────────────────────────────────────
# P2/P3 LLM Agent 内部响应模型
# ──────────────────────────────────────────────


class PlanningStrategy(BaseModel):
    """Coordinator 节点输出的规划策略，后续节点据此筛选景点与餐饮。"""

    strategy: str = Field(default="", description="整体游览策略描述")
    daily_themes: list[str] = Field(default_factory=list, description="每日主题列表")
    pace_normalized: str | None = Field(default=None, description="标准化节奏：轻松/适中/紧凑")
    spot_keywords: list[str] = Field(default_factory=list, description="景点搜索关键词")
    meal_keywords: list[str] = Field(default_factory=list, description="餐饮搜索关键词")
    budget_hint: dict[str, float] = Field(default_factory=dict, description="预算比例提示，如 {hotel: 0.5}")
    hard_constraints: list[str] = Field(default_factory=list, description="必须满足的硬性约束")


class CoordinatorResponse(PlanningStrategy):
    """Coordinator LLM 节点的完整响应，继承自 PlanningStrategy。"""

    # 目前与 PlanningStrategy 字段完全一致，子类化保留扩展空间


class SpotSelection(BaseModel):
    """SpotCurator 选中的单个景点。"""

    name: str = Field(..., description="景点名称")
    reason: str | None = Field(default=None, description="选中原因")
    is_indoor: bool = Field(default=False, description="是否室内景点")
    suggested_hours: float = Field(default=1.0, ge=0, description="建议游览时长（小时）")
    category: str | None = Field(default=None, description="景点类别")


class SpotCuratorResponse(BaseModel):
    """SpotCurator LLM 节点的完整响应。"""

    selected: list[SpotSelection] = Field(default_factory=list, description="选中的景点列表")
    rejected_names: list[str] = Field(default_factory=list, description="被拒绝的景点名称及原因描述")


class MealSelection(BaseModel):
    """MealCurator 选中的单个餐厅。"""

    name: str = Field(..., description="餐厅名称")
    cuisine: str | None = Field(default=None, description="菜系")
    rating: float | None = Field(default=None, ge=0, le=5, description="评分（0-5）")
    signature_dishes: list[str] = Field(default_factory=list, description="招牌菜")
    review_digest: str | None = Field(default=None, description="评价摘要")
    dietary_ok: bool = Field(default=True, description="是否符合饮食偏好约束")
    reason: str | None = Field(default=None, description="推荐原因")


class MealCuratorResponse(BaseModel):
    """MealCurator LLM 节点的完整响应。"""

    selected: list[MealSelection] = Field(default_factory=list, description="选中的餐厅列表")
    rejected_names: list[str] = Field(default_factory=list, description="被拒绝的餐厅名称及原因描述")


class CriticResponse(BaseModel):
    """Critic LLM 节点的完整响应，用于评审行程质量。"""

    verdict: str = Field(default="accept", description="裁决：accept 或 revise")
    score: float | None = Field(default=None, ge=0, le=1, description="质量评分（0-1），未评分时为 None")
    issues: list[str] = Field(default_factory=list, description="发现的问题列表")
    revise_hints: list[str] = Field(default_factory=list, description="修改建议，供 replan 节点参考")

    @field_validator("verdict", mode="before")
    @classmethod
    def _normalize_verdict(cls, value: object) -> str:
        """规范化裁决值：去空格转小写，非法值回退为 accept（等价接受，避免误触发回环）。"""
        text = str(value or "").strip().lower()
        return text if text in {"accept", "revise"} else "accept"


class Itinerary(BaseModel):
    """完整行程。"""

    trip_id: str = Field(..., description="行程唯一标识")
    destination: str = Field(..., description="目的地")
    summary: str = Field(..., description="整趟行程的概述")
    days: list[DayPlan] = Field(default_factory=list, description="逐日行程")
    estimated_budget: float = Field(default=0.0, ge=0, description="预算总计")
    budget_breakdown: BudgetBreakdown = Field(..., description="预算明细")
    tips: list[str] = Field(default_factory=list, description="旅行建议")
    source_notes: list[str] = Field(
        default_factory=list,
        description="生成过程产生的补充说明",
    )
    token_usage: TokenUsage | None = Field(default=None, description="LLM token 消耗统计")


class TripDetailResponse(BaseModel):
    """查询已保存行程时返回的响应体。"""

    trip_id: str = Field(..., description="行程 ID")
    itinerary: Itinerary = Field(..., description="已保存的完整行程")
    created_at: datetime | None = Field(default=None, description="创建时间")
    updated_at: datetime | None = Field(default=None, description="更新时间")


class TripSummaryItem(BaseModel):
    """已保存行程的摘要信息。"""

    trip_id: str = Field(..., description="行程 ID")
    destination: str = Field(..., description="目的地")
    summary: str = Field(..., description="行程概述")
    created_at: datetime | None = Field(default=None, description="创建时间")
    updated_at: datetime | None = Field(default=None, description="更新时间")


class TripListResponse(BaseModel):
    """行程列表接口的响应结构。"""

    total: int = Field(..., ge=0, description="列表总数")
    items: list[TripSummaryItem] = Field(default_factory=list, description="行程摘要列表")


class TripTokenStatsItem(BaseModel):
    """单个行程的 token 消耗。"""

    trip_id: str = Field(..., description="行程 ID")
    destination: str = Field(..., description="目的地")
    token_usage: TokenUsage = Field(..., description="token 消耗")


class TokenStatsResponse(BaseModel):
    """Token 消耗统计接口的响应结构。"""

    trip_count: int = Field(..., ge=0, description="统计行程数")
    total_prompt_tokens: int = Field(default=0, ge=0, description="总输入 token")
    total_completion_tokens: int = Field(default=0, ge=0, description="总输出 token")
    total_tokens: int = Field(default=0, ge=0, description="总 token")
    items: list[TripTokenStatsItem] = Field(default_factory=list, description="各行程 token 明细")

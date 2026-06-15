from __future__ import annotations

import operator
from datetime import date as DateType
from typing import Annotated, TypedDict

from pydantic import BaseModel, Field

from app.models.schemas import DayPlan, Itinerary, TokenUsage, TripRequest


class GeoPoint(BaseModel):
    """A geographic point in latitude/longitude order."""

    latitude: float
    longitude: float


class NormalizedDemand(BaseModel):
    """Search intents and normalized constraints produced by dispatch."""

    city_canonical: str
    spot_keywords: list[str] = Field(default_factory=list)
    dietary_norm: list[str] = Field(default_factory=list)
    transport_intent: str = "市内打车和步行为主"
    hotel_level: str | None = None


class SpotCandidate(BaseModel):
    """Candidate spot before it is assigned to a day."""

    name: str
    latitude: float | None = None
    longitude: float | None = None
    poi_id: str | None = None
    category: str | None = None
    is_indoor: bool | None = None
    ticket_estimate: float = Field(default=0.0, ge=0)
    description: str | None = None
    address: str | None = None
    image_url: str | None = None


class MealCandidate(BaseModel):
    """Candidate meal or restaurant before day assignment."""

    name: str
    latitude: float | None = None
    longitude: float | None = None
    cuisine: str | None = None
    avg_price: float = Field(default=0.0, ge=0)
    dietary_tags: list[str] = Field(default_factory=list)
    notes: str | None = None


class TransportPlan(BaseModel):
    """High-level transportation guidance for a trip."""

    intercity_advice: str = "建议提前确认到达与返程大交通。"
    intracity_default_mode: str = "打车"
    hub: str | None = None


class WeatherDay(BaseModel):
    """Weather context for one itinerary day."""

    date: DateType
    is_rainy: bool = False
    condition: str = "季节性参考"
    temp_range: str | None = None
    source: str = "seasonal"


class WeatherContext(BaseModel):
    """Weather context for the full trip."""

    days: list[WeatherDay] = Field(default_factory=list)


class BudgetReport(BaseModel):
    """Budget validation output used by the graph router."""

    total: float = Field(default=0.0, ge=0)
    breakdown: dict[str, float] = Field(default_factory=dict)
    over_budget: bool = False
    missing_items: list[str] = Field(default_factory=list)
    passed: bool = True


class NodeTrace(BaseModel):
    """One observed node execution."""

    node: str
    status: str
    elapsed_ms: int = Field(default=0, ge=0)
    tokens: dict[str, int] | None = None
    note: str | None = None


class TripState(TypedDict, total=False):
    """State shared across LangGraph nodes."""

    request: TripRequest
    day_count: int
    normalized: NormalizedDemand
    spot_candidates: list[SpotCandidate]
    meal_candidates: list[MealCandidate]
    transport_options: TransportPlan
    weather: WeatherContext
    day_plans: list[DayPlan]
    day_centroids: list[GeoPoint]
    budget_report: BudgetReport
    replan_count: int
    max_replan: int
    itinerary: Itinerary
    token_usage: TokenUsage
    errors: Annotated[list[str], operator.add]
    trace: Annotated[list[NodeTrace], operator.add]

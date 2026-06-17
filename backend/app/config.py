import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker


BACKEND_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BACKEND_DIR / ".env")


# 数据库配置
DB_DIR = BACKEND_DIR / "db"
DB_DIR.mkdir(parents=True, exist_ok=True)

SQLITE_DB_PATH = DB_DIR / "app.db"
DATABASE_URL = f"sqlite:///{SQLITE_DB_PATH.as_posix()}"

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


# 大模型配置
USE_LLM_AGENTS = os.getenv("USE_LLM_AGENTS", "false").lower() == "true"
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "openai_compatible")
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o-mini")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "")
LLM_TIMEOUT_SECONDS = int(os.getenv("LLM_TIMEOUT_SECONDS", "60"))
LLM_MAX_RETRIES = int(os.getenv("LLM_MAX_RETRIES", "1"))


# Redis / 缓存配置
REDIS_ENABLED = os.getenv("REDIS_ENABLED", "false").lower() == "true"
REDIS_URL = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")
REDIS_KEY_PREFIX = os.getenv("REDIS_KEY_PREFIX", "trip_planner")
REDIS_DEFAULT_TTL_SECONDS = int(os.getenv("REDIS_DEFAULT_TTL_SECONDS", "1800"))
REDIS_WEATHER_TTL_SECONDS = int(os.getenv("REDIS_WEATHER_TTL_SECONDS", "1800"))
REDIS_MAP_TTL_SECONDS = int(os.getenv("REDIS_MAP_TTL_SECONDS", "86400"))


# 高德地图配置
AMAP_API_KEY = os.getenv("AMAP_API_KEY", "")
AMAP_BASE_URL = os.getenv("AMAP_BASE_URL", "https://restapi.amap.com/v3")
AMAP_DEFAULT_CITY = os.getenv("AMAP_DEFAULT_CITY", "")
AMAP_TIMEOUT_SECONDS = int(os.getenv("AMAP_TIMEOUT_SECONDS", "20"))
ENABLE_AMAP_ENRICHMENT = os.getenv("ENABLE_AMAP_ENRICHMENT", "false").lower() == "true"


# Bocha AI 联网搜索配置（P2/P3 阶段可选）
BOCHA_ENABLED = os.getenv("BOCHA_ENABLED", "false").lower() == "true"
BOCHA_API_KEY = os.getenv("BOCHA_API_KEY", "")
BOCHA_BASE_URL = os.getenv("BOCHA_BASE_URL", "https://api.bochaai.com/v1")
BOCHA_TIMEOUT_SECONDS = int(os.getenv("BOCHA_TIMEOUT_SECONDS", "15"))


# Trip graph orchestration
USE_LANGGRAPH = os.getenv("USE_LANGGRAPH", "true").lower() == "true"
TRIP_MAX_REPLAN = int(os.getenv("TRIP_MAX_REPLAN", "2"))
TRIP_SPOT_MIN_CANDIDATES = int(os.getenv("TRIP_SPOT_MIN_CANDIDATES", "8"))
TRIP_SPOT_MAX_SEARCH_ROUNDS = int(os.getenv("TRIP_SPOT_MAX_SEARCH_ROUNDS", "2"))
TRIP_ENABLE_WEB_SEARCH = os.getenv("TRIP_ENABLE_WEB_SEARCH", "false").lower() == "true"
WEATHER_FORECAST_MAX_DAYS = int(os.getenv("WEATHER_FORECAST_MAX_DAYS", "4"))
TRIP_METRICS_PERSIST = os.getenv("TRIP_METRICS_PERSIST", "false").lower() == "true"
TRIP_LOG_LEVEL = os.getenv("TRIP_LOG_LEVEL", "INFO")

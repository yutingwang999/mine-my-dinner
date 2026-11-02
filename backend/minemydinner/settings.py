from decimal import Decimal
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=REPO_ROOT / ".env", extra="ignore")

    database_url: str = "postgresql+psycopg://postgres:postgres@127.0.0.1:15433/minemydinner"
    test_database_url: str = (
        "postgresql+psycopg://postgres:postgres@127.0.0.1:15433/minemydinner_test"
    )
    llm_monthly_cap_usd: Decimal = Decimal("20")
    extraction_log_max_rows: int = 500


@lru_cache
def get_settings() -> Settings:
    return Settings()

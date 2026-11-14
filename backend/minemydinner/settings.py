from decimal import Decimal
from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=REPO_ROOT / ".env", extra="ignore")

    database_url: str = "postgresql+psycopg://postgres:postgres@127.0.0.1:15433/minemydinner"
    test_database_url: str = (
        "postgresql+psycopg://postgres:postgres@127.0.0.1:15433/minemydinner_test"
    )
    google_api_key: SecretStr | None = None
    openai_api_key: SecretStr | None = None
    deepseek_api_key: SecretStr | None = None
    # Model IDs that replace the defaults in config/models.yaml. Empty keeps the default.
    gemini_model: str | None = None
    openai_model: str | None = None
    deepseek_model: str | None = None
    gemini_embedding_model: str | None = None
    llm_monthly_cap_usd: Decimal = Decimal("20")
    extraction_log_max_rows: int = 500
    # Cross-site and DNS-rebinding guard for the API (see main.py). Set these from the
    # environment as JSON lists, e.g. ALLOWED_ORIGINS=["http://localhost:3000"].
    allowed_origins: list[str] = ["http://localhost:3000", "http://127.0.0.1:3000"]
    allowed_hosts: list[str] = ["localhost", "127.0.0.1"]
    # Saved screenshots. The data folder is git-ignored.
    image_dir: Path = REPO_ROOT / "backend" / "data" / "images"

    def model_overrides(self) -> dict[str, str | None]:
        return {
            "gemini": self.gemini_model,
            "openai": self.openai_model,
            "deepseek": self.deepseek_model,
            "gemini_embedding": self.gemini_embedding_model,
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()

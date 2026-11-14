from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=("../.env", ".env"), extra="ignore")
    valio_base_url: str = "https://junction26.valioaimo.fi"
    valio_api_key: str = ""
    valio_customer_id: str = "junction-demo"
    valio_customer_ids: dict[str, str] = {}
    valio_timeout_seconds: float = 20
    openrouter_api_key: str = ""
    llm_model: str = "z-ai/glm-5.3-flash"
    frontend_origins: str = "http://localhost:5173"


@lru_cache
def get_settings() -> Settings:
    return Settings()

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=("../.env", ".env"), extra="ignore")
    mock_mode: bool = True
    database_url: str = "sqlite:///./gateway.db"
    valio_base_url: str = "https://junction26.valioaimo.fi"
    valio_api_key: str = ""
    deepseek_api_key: str = ""
    deepseek_model: str = "deepseek-flash"
    frontend_origins: str = "http://localhost:5173"


@lru_cache
def get_settings() -> Settings:
    return Settings()

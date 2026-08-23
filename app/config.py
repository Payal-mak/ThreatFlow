from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration, overridable via environment variables or a .env file."""

    app_name: str = "ThreatFlow"
    database_url: str = "sqlite:///./threatflow.db"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()

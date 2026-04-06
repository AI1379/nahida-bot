"""Application configuration."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Main application settings."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # Application
    app_name: str = "Nahida Bot"
    debug: bool = False
    log_level: str = "INFO"
    log_json: bool | None = None

    # Server
    host: str = "127.0.0.1"
    port: int = 6185

    # Database
    db_path: str = "./data/nahida.db"


def load_settings() -> Settings:
    """Load application settings."""
    return Settings()

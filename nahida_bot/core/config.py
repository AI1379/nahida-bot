"""Application configuration."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Main application settings."""

    # Application
    app_name: str = "Nahida Bot"
    debug: bool = False

    # Server
    host: str = "127.0.0.1"
    port: int = 6185

    # Database
    db_path: str = "./data/nahida.db"

    class Config:
        """Pydantic config."""

        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False


def load_settings() -> Settings:
    """Load application settings."""
    return Settings()

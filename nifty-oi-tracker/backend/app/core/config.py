from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # Database — must be set via .env or environment variable
    database_url: str = ""
    test_database_url: str = ""

    # Kite Connect
    kite_api_key: str = ""
    kite_api_secret: str = ""
    kite_access_token: str = ""

    # Telegram - Main bot
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # Telegram - Selling bot (external users)
    selling_alert_bot_token: str = ""
    selling_alert_chat_ids: str = ""
    selling_alert_extra_chat_ids: str = ""

    # App
    log_level: str = "INFO"
    environment: str = "development"
    shadow_mode: bool = False

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()

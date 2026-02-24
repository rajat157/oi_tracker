from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # Database
    database_url: str = "postgresql+asyncpg://nifty:nifty_dev_pass@localhost:5432/nifty_oi"
    test_database_url: str = (
        "postgresql+asyncpg://nifty:nifty_dev_pass@localhost:5433/nifty_oi_test"
    )

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

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()

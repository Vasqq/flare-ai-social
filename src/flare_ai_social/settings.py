from pathlib import Path

import structlog
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = structlog.get_logger(__name__)


class Settings(BaseSettings):
    """
    Application settings model that provides configuration for all components.
    """

    # API key for accessing Google's Gemini AI service
    gemini_api_key: str = ""
    # Name of the new tuned model
    tuned_model_name: str = ""
    # Base model to tune upon
    tuning_source_model: str = "models/gemini-1.5-flash-001-tuning"
    # Tuning dataset path
    tuning_dataset_path: Path = (
        Path(__file__).parent.parent / "data" / "training_data.json"
    )
    # Number of epochs to tune for
    tuning_epoch_count: int = 30
    # Batch size
    tuning_batch_size: int = 4
    # Learning rate
    tuning_learning_rate: float = 0.001

    # Twitter Bot settings
    enable_twitter: bool = True  # Enable Twitter bot
    # X/Twitter API credentials (all required for the TwitterBot to function)
    x_bearer_token: str = ""
    x_api_key: str = ""  # Required: Twitter API consumer key
    x_api_key_secret: str = ""  # Required: Twitter API consumer secret
    x_access_token: str = ""  # Required: Twitter API access token
    x_access_token_secret: str = ""  # Required: Twitter API access token secret

    # RapidAPI configuration for X/Twitter search (required for the TwitterBot)
    rapidapi_key: str = ""
    rapidapi_host: str = "twitter241.p.rapidapi.com"

    # Twitter accounts to monitor (comma-separated list with @ symbols)
    twitter_accounts_to_monitor: str = "@FlareNetworks"

    # Twitter monitoring interval in seconds
    twitter_polling_interval: int = 60

    # Telegram Bot settings
    enable_telegram: bool = True  # Enable Telegram bot
    telegram_api_token: str = ""  # Required for Telegram bot
    telegram_allowed_users: str = (
        ""  # Comma-separated list of allowed user IDs (optional)
    )
    telegram_polling_interval: int = 5  # Seconds between checking for updates

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
    )

    @property
    def accounts_to_monitor(self) -> list[str]:
        """Parse the comma-separated list of Twitter accounts to monitor."""
        if not self.twitter_accounts_to_monitor:
            return ["@privychatxyz"]
        return [
            account.strip() for account in self.twitter_accounts_to_monitor.split(",")
        ]

    @property
    def telegram_allowed_user_ids(self) -> list[int]:
        """Parse the comma-separated list of allowed Telegram user IDs."""
        if not self.telegram_allowed_users:
            return []
        try:
            return [
                int(user_id.strip())
                for user_id in self.telegram_allowed_users.split(",")
                if user_id.strip()
            ]
        except ValueError:
            logger.exception(
                "Invalid Telegram user IDs in configuration. User IDs must be integers."
            )
            return []


# Create a global settings instance
settings = Settings()
logger.debug(
    "settings",
    settings=settings.model_dump(
        exclude={"x_api_key_secret", "x_access_token_secret", "telegram_api_token"}
    ),
)

import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    APP_NAME: str = "Hustl"
    APP_SUBTITLE: str = "Blue Collar Staffing Platform"
    SECRET_KEY: str = os.getenv(
        "SECRET_KEY",
        "dev-secret-key-change-in-production-xK9mP2nL8qR5tZ7vW3sY1uA6bC4dE0f"
    )
    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///./staffing.db")
    DEBUG: bool = os.getenv("DEBUG", "True").lower() == "true"
    ITEMS_PER_PAGE: int = 20


settings = Settings()

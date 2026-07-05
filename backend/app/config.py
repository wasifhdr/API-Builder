from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# .env lives at the repo root, one level above backend/ — resolve relative to
# this file so it loads correctly regardless of the process's cwd.
ENV_FILE = Path(__file__).resolve().parent.parent.parent / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ENV_FILE, extra="ignore")

    database_url: str
    redis_url: str
    frontend_origin: str = "http://localhost:3000"

    google_client_id: str
    google_client_secret: str
    oauth_redirect_uri: str
    admin_emails: str = ""

    @property
    def admin_email_set(self) -> set[str]:
        return {e.strip().lower() for e in self.admin_emails.split(",") if e.strip()}


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

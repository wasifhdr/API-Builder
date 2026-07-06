from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# .env lives at the repo root, one level above backend/ — resolve relative to
# this file so it loads correctly regardless of the process's cwd.
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
ENV_FILE = REPO_ROOT / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ENV_FILE, extra="ignore")

    database_url: str
    redis_url: str
    frontend_origin: str = "http://localhost:3000"

    google_client_id: str
    google_client_secret: str
    oauth_redirect_uri: str
    admin_emails: str = ""

    plan_price_pro_bdt: int = 100
    plan_price_max_bdt: int = 500
    quota_tz: str = "Asia/Dhaka"

    rec_max_concurrency: int = 1
    profiles_dir: str = "./data/profiles"
    auth_state_fernet_key: str

    exec_max_concurrency: int = 2
    exec_timeout_seconds: int = 90
    sync_wait_seconds: int = 55
    failures_dir: str = "./data/failures"

    @property
    def admin_email_set(self) -> set[str]:
        return {e.strip().lower() for e in self.admin_emails.split(",") if e.strip()}

    @property
    def profiles_path(self) -> Path:
        # data/ lives at the repo root regardless of the worker's cwd — resolve
        # relative fragments against REPO_ROOT rather than trusting cwd.
        p = Path(self.profiles_dir)
        return p if p.is_absolute() else (REPO_ROOT / p).resolve()

    @property
    def failures_path(self) -> Path:
        p = Path(self.failures_dir)
        return p if p.is_absolute() else (REPO_ROOT / p).resolve()


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

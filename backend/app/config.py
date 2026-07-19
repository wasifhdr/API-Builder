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

    bkash_receive_msisdn: str = "01701050922"
    sms_webhook_token: str = "changeme"
    payment_intent_ttl_hours: int = 24

    rec_max_concurrency: int = 1
    profiles_dir: str = "./data/profiles"
    auth_state_fernet_key: str

    exec_max_concurrency: int = 2
    exec_timeout_seconds: int = 90
    sync_wait_seconds: int = 55
    failures_dir: str = "./data/failures"
    # Debug aids for replay: set REPLAY_HEADLESS=false to watch the Chromium
    # window drive the workflow live, and REPLAY_SLOW_MO_MS to slow each action
    # down so you can see which step drifts. Keep headless in normal operation
    # (llama.cpp owns the VRAM; headful contends for the desktop).
    replay_headless: bool = True
    replay_slow_mo_ms: int = 0

    llama_base_url: str = "http://127.0.0.1:8080/v1"
    llm_enabled: bool = True
    llm_provider: str = "llama"
    craftx_base_url: str = ""
    craftx_api_key: str = ""
    craftx_model: str = ""

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

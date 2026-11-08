from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # mysql+pymysql://user:pass@mysql:3306/wxxd?charset=utf8mb4 in production
    database_url: str = "sqlite:///./wxxd.db"
    # Secret for signing login tokens (JWT). Must be changed in production.
    jwt_secret: str = "change-me"
    jwt_expire_hours: int = 72
    # base64 of 32 random bytes; encrypts store AppSecrets at rest (AES-GCM).
    # Generate: python -c "import os,base64;print(base64.b64encode(os.urandom(32)).decode())"
    secret_key_b64: str = "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY="

    # Shown in the UI so users know which IP to add to their store's API whitelist.
    server_public_ip: str = ""

    wechat_api_base: str = "https://api.weixin.qq.com"

    # DeepSeek title rewrite (OpenAI-compatible). Leave key empty to disable.
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-chat"

    # Upload limits for the product cloud library.
    max_files_per_upload: int = 500

    # Worker
    worker_poll_seconds: float = 2.0
    worker_item_interval_seconds: float = 1.0  # pause between items (rate limit)


@lru_cache
def get_settings() -> Settings:
    return Settings()

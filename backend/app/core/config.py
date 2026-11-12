from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    app_name: str = "PayPilot AI"
    app_env: str = "development"
    database_url: str = f"sqlite:///{(PROJECT_ROOT / 'data' / 'paypilot.db').as_posix()}"
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen3:4b"
    ollama_timeout_seconds: float = 45.0
    cors_origins: str = "http://localhost:8000,http://127.0.0.1:8000"
    seed_demo_data: bool = True
    paypal_mode: Literal["sandbox"] = "sandbox"
    paypal_client_id: str = ""
    paypal_client_secret: str = ""
    paypal_base_url: str = "https://api-m.sandbox.paypal.com"
    workspace_admin_token: str = ""
    app_base_url: str = "http://127.0.0.1:8000"
    marketplace_tax_rate: float = Field(default=0.0825, ge=0, le=0.25)
    marketplace_shipping_flat: float = Field(default=5.99, ge=0, le=1000)
    marketplace_free_shipping_threshold: float = Field(default=75.00, ge=0, le=100000)
    marketplace_order_ttl_minutes: int = Field(default=30, ge=5, le=180)
    product_upload_dir: Path = PROJECT_ROOT / "data" / "product_uploads"
    marketplace_max_image_bytes: int = Field(default=5242880, ge=1024, le=20971520)

    @field_validator("paypal_base_url")
    @classmethod
    def require_sandbox_host(cls, value: str) -> str:
        from urllib.parse import urlparse
        parsed = urlparse(value)
        if parsed.scheme != "https" or parsed.hostname != "api-m.sandbox.paypal.com":
            raise ValueError("PayPal integration is restricted to the PayPal Sandbox host")
        return value.rstrip("/")

    model_config = SettingsConfigDict(env_file=PROJECT_ROOT / ".env", env_file_encoding="utf-8", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()

"""Runtime configuration. Everything secret stays in the environment."""
from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

TIMEFRAMES = ("5m", "15m", "30m", "1h", "4h", "1d")
# Direction is set by the top of the stack; entries are timed at the bottom.
DIRECTION_TFS = ("1d", "4h")
CONFIRM_TFS = ("1h",)
TRIGGER_TFS = ("15m", "5m")
# Timeframes we actually publish signals on.
PUBLISH_TFS = ("15m", "1h", "4h")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="", env_file=".env", extra="ignore")

    env: str = Field("local", alias="SP_ENV")
    log_level: str = Field("info", alias="SP_LOG_LEVEL")
    secret_key: str = Field("dev-only-secret", alias="SP_SECRET_KEY")
    access_token_minutes: int = Field(30, alias="SP_ACCESS_TOKEN_MINUTES")
    refresh_token_days: int = Field(30, alias="SP_REFRESH_TOKEN_DAYS")

    database_url: str = Field(
        "postgresql+asyncpg://signalproof:signalproof@localhost:5432/signalproof",
        alias="SP_DATABASE_URL",
    )
    redis_url: str = Field("redis://localhost:6379/0", alias="SP_REDIS_URL")

    market_mode: Literal["replay", "live"] = Field("replay", alias="SP_MARKET_MODE")
    primary_venue: str = Field("binance", alias="SP_PRIMARY_VENUE")
    venues_raw: str = Field("binance,coinbase", alias="SP_VENUES")
    symbols_raw: str = Field("BTCUSDT,ETHUSDT,SOLUSDT", alias="SP_SYMBOLS")
    replay_speed: float = Field(60.0, alias="SP_REPLAY_SPEED")
    orderbook_depth: int = Field(20, alias="SP_ORDERBOOK_DEPTH")

    coinglass_api_key: str = Field("", alias="COINGLASS_API_KEY")
    coinglass_tier: str = Field("none", alias="COINGLASS_TIER")
    hyperliquid_api_url: str = Field("https://api.hyperliquid.xyz", alias="HYPERLIQUID_API_URL")
    smart_money_enabled: bool = Field(True, alias="SP_SMART_MONEY_ENABLED")
    smart_money_is_signal_driver: bool = Field(False, alias="SP_SMART_MONEY_IS_SIGNAL_DRIVER")

    strategy_version: str = Field("1.1.0", alias="SP_STRATEGY_VERSION")
    cors_origins: str = Field("", alias="SP_CORS_ORIGINS")
    min_rr: float = Field(1.5, alias="SP_MIN_RR")
    min_calibration_sample: int = Field(30, alias="SP_MIN_CALIBRATION_SAMPLE")
    max_book_staleness_ms: int = Field(5000, alias="SP_MAX_BOOK_STALENESS_MS")
    max_trade_staleness_ms: int = Field(10000, alias="SP_MAX_TRADE_STALENESS_MS")
    max_clock_skew_ms: int = Field(2000, alias="SP_MAX_CLOCK_SKEW_MS")
    min_live_venues: int = Field(2, alias="SP_MIN_LIVE_VENUES")

    free_tier_delay_minutes: int = Field(60, alias="SP_FREE_TIER_DELAY_MINUTES")
    free_tier_history_days: int = Field(30, alias="SP_FREE_TIER_HISTORY_DAYS")
    free_tier_symbols: tuple[str, ...] = ("BTCUSDT",)

    telegram_bot_token: str = Field("", alias="TELEGRAM_BOT_TOKEN")
    vapid_public_key: str = Field("", alias="VAPID_PUBLIC_KEY")
    vapid_private_key: str = Field("", alias="VAPID_PRIVATE_KEY")
    smtp_url: str = Field("", alias="SMTP_URL")

    billing_provider: Literal["none", "stripe", "razorpay", "paddle"] = Field(
        "none", alias="SP_BILLING_PROVIDER"
    )
    pro_price_usd: float = Field(10.0, alias="SP_PRO_PRICE_USD")

    anchor_provider: Literal["local", "opentimestamps"] = Field("local", alias="SP_ANCHOR_PROVIDER")

    @field_validator("market_mode", mode="before")
    @classmethod
    def _lower(cls, v: str) -> str:
        return str(v).lower()

    @property
    def venues(self) -> list[str]:
        return [v.strip() for v in self.venues_raw.split(",") if v.strip()]

    @property
    def symbols(self) -> list[str]:
        return [s.strip().upper() for s in self.symbols_raw.split(",") if s.strip()]

    @property
    def is_replay(self) -> bool:
        return self.market_mode == "replay"

    @property
    def sync_database_url(self) -> str:
        return self.database_url.replace("+asyncpg", "")


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

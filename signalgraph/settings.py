from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")

    # Storage. SQLite for a single box; point at Postgres to scale:
    # postgresql+asyncpg://user:pass@host/signalgraph
    database_url: str = f"sqlite+aiosqlite:///{ROOT / 'data' / 'signalgraph.db'}"
    config_dir: Path = ROOT / "config"

    # Mode: "live" polls NSE/BSE/RSS and Angel One; "demo" replays sample data offline.
    mode: str = "live"

    # Claude scoring. Without a key the offline lexicon scorer is used.
    anthropic_api_key: str | None = None
    scoring_model: str = "claude-opus-5-5"
    scoring_effort: str = "low"
    scoring_concurrency: int = 4

    # Angel One SmartAPI. Without these, prices fall back to demo ticks (live mode
    # still pulls news; the UI flags the price feed as DEMO).
    angel_api_key: str | None = None
    angel_client_code: str | None = None
    angel_pin: str | None = None
    angel_totp_secret: str | None = None

    # Telegram alerts (optional).
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None

    # Signal thresholds on the [-1, 1] score.
    green_threshold: float = 0.25
    red_threshold: float = -0.25
    alert_impact: float = 0.25  # Telegram alert when one item's |direction*magnitude*confidence| >= this

    host: str = "127.0.0.1"
    port: int = 8000


settings = Settings()

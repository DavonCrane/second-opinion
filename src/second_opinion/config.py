"""Central configuration. Everything is read from environment variables (via .env) with safe defaults.

Secrets never live in code: copy .env.example -> .env and fill in values. .env is git-ignored.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

# Repo root = two levels above this file's package dir (src/second_opinion/config.py -> repo root)
REPO_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(REPO_ROOT / ".env")


def _bool(name: str, default: bool) -> bool:
    val = os.getenv(name)
    if val is None or val == "":
        return default
    return val.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    # LLM
    anthropic_api_key: str = field(default_factory=lambda: os.getenv("ANTHROPIC_API_KEY", ""))
    model_strong: str = field(default_factory=lambda: os.getenv("SO_MODEL_STRONG", "claude-sonnet-5"))
    model_fast: str = field(default_factory=lambda: os.getenv("SO_MODEL_FAST", "claude-haiku-4-5"))

    # Data providers
    edgar_identity: str = field(default_factory=lambda: os.getenv("EDGAR_IDENTITY", ""))
    reddit_client_id: str = field(default_factory=lambda: os.getenv("REDDIT_CLIENT_ID", ""))
    reddit_client_secret: str = field(default_factory=lambda: os.getenv("REDDIT_CLIENT_SECRET", ""))
    reddit_user_agent: str = field(default_factory=lambda: os.getenv("REDDIT_USER_AGENT", "second-opinion/0.1"))
    finnhub_api_key: str = field(default_factory=lambda: os.getenv("FINNHUB_API_KEY", ""))

    # Behaviour
    offline: bool = field(default_factory=lambda: _bool("SO_OFFLINE", False))
    critic_enabled: bool = field(default_factory=lambda: _bool("SO_CRITIC_ENABLED", True))
    max_critic_rounds: int = field(default_factory=lambda: int(os.getenv("SO_MAX_CRITIC_ROUNDS", "2")))

    # Paths
    data_dir: Path = field(default_factory=lambda: REPO_ROOT / "data")
    fixtures_dir: Path = field(default_factory=lambda: REPO_ROOT / "fixtures")
    reports_dir: Path = field(default_factory=lambda: REPO_ROOT / "reports")

    def __post_init__(self) -> None:
        for d in (self.data_dir, self.reports_dir):
            d.mkdir(parents=True, exist_ok=True)

    @property
    def reddit_configured(self) -> bool:
        return bool(self.reddit_client_id and self.reddit_client_secret)

    @property
    def llm_configured(self) -> bool:
        return bool(self.anthropic_api_key) and not self.anthropic_api_key.startswith("sk-ant-your-key")


settings = Settings()

"""Runtime configuration, read from the environment (.env at the repo root)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data"

load_dotenv(REPO_ROOT / ".env")


@dataclass(frozen=True)
class Settings:
    supabase_url: str | None
    supabase_service_key: str | None
    user_agent: str
    request_timeout: float

    @property
    def has_supabase(self) -> bool:
        return bool(self.supabase_url and self.supabase_service_key)


def load_settings() -> Settings:
    return Settings(
        supabase_url=os.getenv("SUPABASE_URL") or None,
        supabase_service_key=os.getenv("SUPABASE_SERVICE_KEY") or None,
        user_agent=os.getenv(
            "HTTP_USER_AGENT",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        ),
        request_timeout=float(os.getenv("HTTP_TIMEOUT", "30")),
    )


settings = load_settings()

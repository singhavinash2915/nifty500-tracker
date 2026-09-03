"""Runtime configuration, read from the environment (.env at the repo root)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data"

load_dotenv(REPO_ROOT / ".env")


# Blend weights, overridable from .env.
#
# Moved off the plan's 45/20/35 on evidence. The weight sweep found exactly one
# result that clears significance across 14 rebalances, and it is that the
# quality pillar predicted the next six months *negatively*: IC -0.041 with
# t = -2.48. Value and technical were both mildly positive and both inside the
# noise (t = +1.28 and +1.30), and the best combination overall, 0/50/50,
# reached only t = +1.90 — which overlapping holding periods make weaker still.
#
# So the default sits deliberately between the old weighting and what the sweep
# points at: enough of a cut to stop paying for a pillar the evidence is against,
# not so much as to adopt the peak of a 67-candidate grid fitted to one regime.
# It is a provisional setting, not a finding, and it should move again when the
# sample covers more than fourteen overlapping months of one market.
#
# Quality's *gates* are unaffected. A red flag still excludes outright; what
# changed is how much the quality score is paid in the blend.
DEFAULT_BLEND_WEIGHTS = {"quality": 25.0, "value": 35.0, "technical": 40.0}


# The tracker's tables live in their own Postgres schema rather than `public`.
# The project hosting them also runs another application, and a dedicated
# schema means the two can never collide on a table name, the host app's API
# does not grow twenty unfamiliar endpoints, and removing the tracker is one
# `drop schema n500 cascade` rather than twenty drops and a hope.
#
# PostgREST only serves schemas listed under Settings -> API -> Exposed
# schemas, so `n500` has to be added there once.
DB_SCHEMA = os.getenv("SUPABASE_SCHEMA", "n500")


@dataclass(frozen=True)
class Settings:
    supabase_url: str | None
    supabase_service_key: str | None
    user_agent: str
    request_timeout: float
    blend_weights: dict[str, float]
    db_schema: str = DB_SCHEMA

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
        blend_weights={
            name: float(os.getenv(f"WEIGHT_{name.upper()}", default))
            for name, default in DEFAULT_BLEND_WEIGHTS.items()
        },
        db_schema=DB_SCHEMA,
    )


settings = load_settings()

"""Settings, read once from environment variables. Secrets never live in code."""
import os
from dataclasses import dataclass


def _flag(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    anthropic_api_key: str
    router_model: str
    llm_enabled: bool
    framing_enabled: bool
    daily_cost_cap_usd: float
    app_env: str
    suggest_form_url: str
    team_email: str
    version: str
    submission_commit: str


def load_settings() -> Settings:
    commit = os.environ.get("RENDER_GIT_COMMIT", "")
    return Settings(
        anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY", ""),
        router_model=os.environ.get("ROUTER_MODEL", "claude-opus-5-5"),
        llm_enabled=_flag("LLM_ENABLED", False),
        framing_enabled=_flag("FRAMING_ENABLED", True),
        daily_cost_cap_usd=float(os.environ.get("DAILY_COST_CAP_USD", "2")),
        app_env=os.environ.get("APP_ENV", "dev"),
        suggest_form_url=os.environ.get("SUGGEST_FORM_URL", ""),
        team_email=os.environ.get("TEAM_EMAIL", ""),
        version=commit[:7] or "dev",
        submission_commit=os.environ.get("SUBMISSION_COMMIT", ""),
    )

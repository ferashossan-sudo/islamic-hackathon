"""Settings, read once from environment variables. Secrets never live in code."""
import os
from dataclasses import dataclass

DEFAULT_MODELS = {"gemini": "gemini-3.5-flash-lite", "anthropic": "claude-opus-5-5"}
# The verifier judges finer distinctions (claim vs. analogy); a separate model also spreads free-tier limits.
DEFAULT_VERIFY_MODELS = {"gemini": "gemini-3.5-flash", "anthropic": "claude-opus-5-5"}


def _flag(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    router_provider: str
    router_model: str
    gemini_api_key: str
    anthropic_api_key: str
    router_timeout_s: float
    router_retry_timeout_s: float
    converse_model: str
    verify_model: str
    converse_enabled: bool
    converse_timeout_s: float
    confidence_min: str
    llm_enabled: bool
    framing_enabled: bool
    daily_cost_cap_usd: float
    app_env: str
    suggest_form_url: str
    team_email: str
    version: str
    submission_commit: str

    @property
    def router_key(self) -> str:
        return self.gemini_api_key if self.router_provider == "gemini" else self.anthropic_api_key


def load_settings() -> Settings:
    commit = os.environ.get("RENDER_GIT_COMMIT", "")
    provider = os.environ.get("ROUTER_PROVIDER", "gemini").strip().lower()
    router_model = os.environ.get("ROUTER_MODEL", "").strip() or DEFAULT_MODELS.get(provider, "")
    return Settings(
        router_provider=provider,
        router_model=router_model,
        converse_model=os.environ.get("CONVERSE_MODEL", "").strip() or router_model,
        verify_model=os.environ.get("VERIFY_MODEL", "").strip() or DEFAULT_VERIFY_MODELS.get(provider, router_model),
        converse_enabled=_flag("CONVERSE_ENABLED", True),
        converse_timeout_s=float(os.environ.get("CONVERSE_TIMEOUT_S", "15")),
        gemini_api_key=os.environ.get("GEMINI_API_KEY", ""),
        anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY", ""),
        router_timeout_s=float(os.environ.get("ROUTER_TIMEOUT_S", "8")),
        router_retry_timeout_s=float(os.environ.get("ROUTER_RETRY_TIMEOUT_S", "5")),
        confidence_min=os.environ.get("CONFIDENCE_MIN", "medium"),
        llm_enabled=_flag("LLM_ENABLED", False),
        framing_enabled=_flag("FRAMING_ENABLED", True),
        daily_cost_cap_usd=float(os.environ.get("DAILY_COST_CAP_USD", "2")),
        app_env=os.environ.get("APP_ENV", "dev"),
        suggest_form_url=os.environ.get("SUGGEST_FORM_URL", ""),
        team_email=os.environ.get("TEAM_EMAIL", ""),
        version=commit[:7] or "dev",
        submission_commit=os.environ.get("SUBMISSION_COMMIT", ""),
    )

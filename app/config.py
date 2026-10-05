"""Settings, read once from environment variables. Secrets never live in code."""
import os
from dataclasses import dataclass

# Each role names a chain of free-tier models, each with its own daily quota (gemini-3.5-flash-lite: 500 requests;
# gemini-3.5-flash: 20, too few). The next model answers when one is out of quota or busy (app/gemini.py).
# The verifier chain starts with the model that scored 8/8 in eval/check_verifier.py.
DEFAULT_MODELS = {"gemini": "gemini-3.5-flash-lite,gemini-3.1-flash-lite", "anthropic": "claude-opus-5-5"}
DEFAULT_CONVERSE_MODELS = {"gemini": "gemini-3.1-flash-lite,gemini-3.5-flash-lite", "anthropic": "claude-opus-5-5"}
# gemma-4-26b-a4b-it flagged 6/6 invented claims when it answered but failed 2 of 8 calls, so it comes last.
DEFAULT_VERIFY_MODELS = {"gemini": "gemini-3.5-flash-lite,gemini-3.1-flash-lite,gemma-4-26b-a4b-it",
                         "anthropic": "claude-opus-5-5"}


def with_fallbacks(models: str, defaults: str) -> str:
    """The configured models first, then any default model not already named, as fallbacks.

    A single model set in the hosting dashboard (ROUTER_MODEL=gemini-3.5-flash-lite) still gets the free-tier chain.
    """
    names = [m.strip() for m in models.split(",") if m.strip()]
    names += [m.strip() for m in defaults.split(",") if m.strip() and m.strip() not in names]
    return ",".join(names)


def first(models: str) -> str:
    """The first model of a comma-separated chain (providers without fallback use only this one)."""
    return models.split(",")[0].strip()


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
    def models(name: str, defaults: dict) -> str:
        value = os.environ.get(name, "").strip()
        default = defaults.get(provider, "")
        if provider != "gemini":  # one model, no chain
            return value or default
        return with_fallbacks(value, default) if value else default

    router_model = models("ROUTER_MODEL", DEFAULT_MODELS)
    return Settings(
        router_provider=provider,
        router_model=router_model,
        converse_model=models("CONVERSE_MODEL", DEFAULT_CONVERSE_MODELS) or router_model,
        verify_model=models("VERIFY_MODEL", DEFAULT_VERIFY_MODELS) or router_model,
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


def for_evaluation(s: Settings) -> Settings:
    """Evaluation runs use their own key when GEMINI_API_KEY_EVAL is set (another Google project), so they never
    spend the live site's free quota."""
    import dataclasses

    key = os.environ.get("GEMINI_API_KEY_EVAL", "").strip()
    return dataclasses.replace(s, gemini_api_key=key) if key else s

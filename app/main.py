"""FastAPI entry point: the page, the chat API, health, and security headers."""
import os
from pathlib import Path
from time import perf_counter

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app import compose, kb, pipeline, router, texts
from app.config import load_settings
from app.pages import render_index
from app.schemas import ChatRequest, ChatResponse
from app.usage import log_event

ROOT = Path(__file__).resolve().parent.parent
settings = load_settings()
INDEX_HTML = render_index(settings)
APPROVED = kb.load_approved()  # refuses to start if an approved entry breaks a rule
# Local preview of drafts for the team only: never in production, and the page says so in a banner.
PREVIEW_DRAFTS = settings.app_env == "dev" and os.environ.get("PREVIEW_DRAFTS", "").lower() == "true"
if PREVIEW_DRAFTS:
    APPROVED = kb.preview_drafts()
KB_HASH = kb.kb_hash(APPROVED)
pipeline.load(APPROVED)

SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
        "font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; "
        "form-action 'self'; frame-ancestors 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), interest-cohort=()",
}
# Only these paths are logged by name; any other path is logged as "other" so no visitor text reaches the log.
LOGGED_PATHS = {"/", "/health", "/api/config", "/api/chat", "/api/selftest", "/static/app.js", "/static/styles.css"}

app = FastAPI(title="Litatma'inna Qalbi", docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


@app.middleware("http")
async def headers_and_timing(request: Request, call_next):
    started = perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        response = JSONResponse({"ok": False}, status_code=500)
    response.headers.update(SECURITY_HEADERS)
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    path = request.url.path if request.url.path in LOGGED_PATHS else "other"
    log_event(event="http", path=path, status=response.status_code,
              ms=round((perf_counter() - started) * 1000))
    return response


@app.exception_handler(RequestValidationError)
async def invalid_request(request: Request, exc: RequestValidationError):
    # A gentle card instead of FastAPI's default body, which would echo the input back.
    body = ChatResponse(kind="abstain", version=settings.version,
                        blocks=[pipeline.message_block("input_invalid")])
    return JSONResponse(body.model_dump(), status_code=422)


@app.api_route("/", methods=["GET", "HEAD"], response_class=HTMLResponse)
async def index() -> HTMLResponse:
    return HTMLResponse(INDEX_HTML)


@app.api_route("/health", methods=["GET", "HEAD"])
async def health() -> dict:
    return {"ok": True, "version": settings.version, "approved": len(APPROVED), "kb_hash": KB_HASH,
            "llm": router.health(settings), "provider": settings.router_provider if settings.router_key else None,
            "models": {"router": settings.router_model, "converse": settings.converse_model,
                       "verify": settings.verify_model},
            "backup": "gemini" if settings.router_provider != "gemini" and settings.gemini_api_key else None}


@app.get("/api/selftest")
async def selftest(request: Request) -> JSONResponse:
    """For the scheduled check: one known question through the model path. Needs SELFTEST_TOKEN."""
    token = os.environ.get("SELFTEST_TOKEN", "")
    if not token or request.headers.get("x-selftest-token") != token:
        return JSONResponse({"ok": False}, status_code=403)
    if not APPROVED:
        return JSONResponse({"ok": False, "reason": "no_approved_entries"})
    question = APPROVED[0]["question"]
    _, response = await pipeline.handle(ChatRequest(message=question), settings, "selftest")
    ok = response.kind == "answer" and not response.degraded
    return JSONResponse({"ok": ok, "kind": response.kind, "degraded": response.degraded, "llm": router.health(settings)})


def _featured_rank(entry: dict) -> int:
    """Suggested questions in the order of the field survey's most asked topics: «featured» is a rank (1 first).

    A plain true (older entries) comes after every ranked one.
    """
    rank = entry["featured"]
    return rank if type(rank) is int else 1000


@app.get("/api/config")
async def config() -> dict:
    return {
        "version": settings.version,
        "suggest_form_url": settings.suggest_form_url,
        "labels": texts.pairs("ui_labels"),
        "texts": {key: texts.text(key) for key in ("network_error", "support_line", "degraded_badge", "degraded_mode",
                                                   "science_degree_hints")},
        "featured": [{"id": e["id"], "question": compose.question_text(e["question"])}
                     for e in sorted((e for e in APPROVED if e.get("featured")), key=_featured_rank)][:6],
        "preview_drafts": PREVIEW_DRAFTS,
    }


@app.post("/api/chat")
async def chat(req: ChatRequest, request: Request) -> JSONResponse:
    address = request.client.host if request.client else ""  # reduced to a daily salted hash in limits.py
    try:
        status, response = await pipeline.handle(req, settings, address)
    except Exception:
        status, response = 200, pipeline.fail_closed(settings)
    log_event(event="chat", kind=response.kind, degraded=response.degraded)
    return JSONResponse(response.model_dump(), status_code=status)

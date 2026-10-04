"""FastAPI entry point: the page, the chat API, health, and security headers."""
from pathlib import Path
from time import perf_counter

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app import kb, pipeline, texts
from app.config import load_settings
from app.pages import render_index
from app.schemas import ChatRequest, ChatResponse
from app.usage import log_event

ROOT = Path(__file__).resolve().parent.parent
settings = load_settings()
INDEX_HTML = render_index(settings)
APPROVED = kb.load_approved()  # refuses to start if an approved entry breaks a rule
KB_HASH = kb.kb_hash(APPROVED)

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
LOGGED_PATHS = {"/", "/health", "/api/config", "/api/chat", "/static/app.js", "/static/styles.css"}

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


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    return HTMLResponse(INDEX_HTML)


@app.get("/health")
async def health() -> dict:
    return {"ok": True, "version": settings.version, "approved": len(APPROVED), "kb_hash": KB_HASH,
            "llm": "up" if settings.llm_enabled else "off"}


@app.get("/api/config")
async def config() -> dict:
    return {
        "version": settings.version,
        "suggest_form_url": settings.suggest_form_url,
        "labels": texts.pairs("ui_labels"),
        "texts": {key: texts.text(key) for key in ("network_error", "support_line", "degraded_badge")},
    }


@app.post("/api/chat")
async def chat(req: ChatRequest) -> JSONResponse:
    try:
        status, response = pipeline.handle(req, settings)
    except Exception:
        status, response = 200, pipeline.fail_closed(settings)
    log_event(event="chat", kind=response.kind, degraded=response.degraded)
    return JSONResponse(response.model_dump(), status_code=status)

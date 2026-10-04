import re
from pathlib import Path

from fastapi.testclient import TestClient

from app import main, pipeline, texts

ROOT = Path(__file__).resolve().parent.parent
client = TestClient(main.app)


def chat(message, **extra):
    return client.post("/api/chat", json={"message": message, **extra})


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["version"]
    assert body["llm"] in {"up", "off"}


def test_index_shows_disclosure_and_privacy():
    r = client.get("/")
    assert r.status_code == 200
    html = r.text
    assert texts.text("disclosure_header") in html
    assert texts.text("disclosure") in html
    assert 'id="privacy"' in html
    assert "{{" not in html


def test_chat_returns_fixed_abstention_contract():
    r = chat("هل الكون جاء صدفة؟")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"kind", "entry_id", "layer", "degraded", "version", "blocks"}
    assert body["kind"] == "abstain"
    assert body["blocks"][0] == {"type": "message", "key": "abstain", "text": texts.text("abstain")}


def test_empty_and_invisible_messages_are_rejected():
    for message in ["", "   ", "​‏"]:
        r = chat(message)
        assert r.status_code == 422
        assert r.json()["blocks"][0]["key"] == "input_invalid"


def test_message_over_800_chars_is_rejected():
    r = chat("س" * 801)
    assert r.status_code == 422
    assert r.json()["blocks"][0]["key"] == "input_too_long"
    assert chat("س" * 800).status_code == 200


def test_malformed_request_does_not_echo_input():
    marker = "علامة-لا-تعاد-" * 400  # over the 4000-character hard cap
    r = chat(marker)
    assert r.status_code == 422
    assert "علامة-لا-تعاد" not in r.text
    assert r.json()["blocks"][0]["key"] == "input_invalid"


def test_fail_closed_on_any_exception(monkeypatch):
    async def boom(req, s):
        raise RuntimeError("injected")

    monkeypatch.setattr(pipeline, "handle", boom)
    r = chat("سؤال")
    assert r.status_code == 200
    assert r.json()["kind"] == "abstain"
    assert r.json()["blocks"][0]["key"] == "fail_closed"


def test_security_headers_on_page_and_api():
    for r in (client.get("/"), chat("سؤال"), client.get("/health")):
        csp = r.headers["content-security-policy"]
        assert "default-src 'self'" in csp
        assert "script-src 'self'" in csp
        assert r.headers["x-content-type-options"] == "nosniff"
        assert r.headers["referrer-policy"] == "no-referrer"


def test_no_inline_script_style_or_handlers():
    sources = [ROOT / "app" / "templates" / "index.html", *(ROOT / "static").glob("*.js")]
    html = (ROOT / "app" / "templates" / "index.html").read_text(encoding="utf-8")
    assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>", html), "inline <script>"
    assert "<style" not in html
    assert not re.search(r"\sstyle=", html)
    assert not re.search(r"\son[a-z]+=", html), "inline event handler"
    for path in sources:
        assert "innerHTML" not in path.read_text(encoding="utf-8"), path.name


def test_no_message_content_in_logs(capsys):
    marker = "نص-مميز-للتحقق-من-السجل"
    chat(marker)
    chat(marker * 30)  # too long path
    client.get("/" + marker)  # unknown path with visitor text
    out = capsys.readouterr()
    assert marker not in out.out
    assert marker not in out.err

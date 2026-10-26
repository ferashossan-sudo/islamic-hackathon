from fastapi.testclient import TestClient

from app import main, qr

client = TestClient(main.app)


def test_a_source_of_an_approved_answer_gets_a_qr_code():
    url = next(e["source"]["url"] for e in main.APPROVED if e.get("source"))
    rows = client.post("/api/qr", json={"urls": [url, url]}).json()["codes"][url]
    assert len(rows) >= 21 and all(len(r) == len(rows) and set(r) <= {"0", "1"} for r in rows)
    assert rows[0].startswith("1111111")  # a finder pattern in the corner


def test_an_address_outside_the_approved_answers_is_left_out():
    r = client.post("/api/qr", json={"urls": ["https://example.com/anything", "أي نص كتبه الزائر"]})
    assert r.status_code == 200 and r.json() == {"codes": {}}
    assert client.post("/api/qr", json={"urls": [], "text": "x"}).status_code == 422


def test_every_approved_address_fits_a_printable_code():
    sizes = {len(qr.modules(url)) for url in qr.source_urls(main.APPROVED)}
    assert max(sizes) <= 61  # 28 mm in the PDF: modules of about 0.4 mm

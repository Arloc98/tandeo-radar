"""/refresh must hand the events back in its own response body.

Without this the UI needs a follow-up GET /events, which reads module-level STATE. That
works in one process and silently returns [] on any stateless deployment, where the two
requests can land on different instances.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app import collector, config, extractor, main
from app.models import Announcement, TandeoEvent


AVISO = Announcement(url="https://x.test/a", content="corte de agua en Coyoacán")
EVENTO = TandeoEvent(alcaldia="Coyoacán", colonias=["Del Carmen"],
                     kind="suspension", source_url="https://x.test/a")


def _refresh(monkeypatch, events):
    monkeypatch.setattr(config, "NEBIUS_API_KEY", "k")
    monkeypatch.setattr(main.config, "NEBIUS_API_KEY", "k")
    monkeypatch.setattr(collector, "collect", lambda *a, **kw: ([AVISO], 0))
    monkeypatch.setattr(extractor, "extract", lambda *a, **kw: events)
    return TestClient(main.app).post("/refresh?geocode=false").json()


def test_refresh_returns_the_events_in_the_body(monkeypatch):
    body = _refresh(monkeypatch, [EVENTO])

    assert body.get("events") == 1
    items = body.get("items")
    assert isinstance(items, list), f"/refresh must carry the events, got {body}"
    assert len(items) == 1
    assert items[0]["alcaldia"] == "Coyoacán"
    assert items[0]["colonias"] == ["Del Carmen"]


def test_the_body_is_json_serialisable_without_a_follow_up_call(monkeypatch):
    """The payload must be plain JSON, not pydantic objects the client cannot read."""
    body = _refresh(monkeypatch, [EVENTO])

    item = body["items"][0]
    assert isinstance(item, dict)
    # start/end viajan como cadena ISO o null, nunca como datetime
    assert item["start"] is None or isinstance(item["start"], str)
    assert isinstance(item["confidence"], (int, float))


def test_an_empty_run_reports_an_empty_list_not_a_missing_key(monkeypatch):
    """"Sin cortes" and "the key is absent" must not look the same to the client."""
    body = _refresh(monkeypatch, [])

    assert body.get("events") == 0
    assert body.get("items") == []

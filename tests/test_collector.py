"""Offline tests for the collector: degradation must be visible, never silent.

The failure this guards against (measured 24 Sep 2026): Tavily sometimes answers
200 OK with raw_content null and a 140 char snippet, and the collector used
to build an Announcement out of the snippet anyway, so the pipeline reported
notices that were only headlines. Nothing here touches the network: a fake
client stands in for Tavily.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

import pytest


from app import collector, config, llm, main
from app.models import Announcement

# A real notice: colonias with accents, alcaldia, dates and a schedule. Accents
# are the normal case in this data, so they stay in the fixture.
TEXTO_COMPLETO = (
    "La Secretaría de Gestión Integral del Agua informa que habrá suspensión del servicio "
    "de agua potable en las colonias Del Valle, Narvarte y Nápoles, en la alcaldía Benito "
    "Juárez, del 25 al 26 de septiembre. " * 12
)
TITULAR = "Corte de agua en CDMX: estas son las colonias afectadas este jueves"


def _resultado(url: str, raw: str | None, content: str = TITULAR, published: str | None = "Thu, 24 Sep 2026 00:00:00 GMT") -> dict:
    return {
        "url": url,
        "title": "aviso",
        "content": content,
        "raw_content": raw,
        "published_date": published,
    }


class _TavilyFake:
    """Stands in for TavilyClient; returns the same payload for every query."""

    def __init__(self, resultados: list[dict]):
        self.resultados = resultados
        self.consultas: list[str] = []
        self.llamadas: list[dict] = []

    def search(self, query: str, **kwargs) -> dict:
        self.consultas.append(query)
        self.llamadas.append(kwargs)
        return {"results": list(self.resultados)}


@pytest.fixture
def en_linea(monkeypatch):
    """Force the live path with a fake Tavily client; no network is possible."""
    def _montar(resultados: list[dict]) -> _TavilyFake:
        fake = _TavilyFake(resultados)
        monkeypatch.setattr(config, "OFFLINE", False)
        monkeypatch.setattr(config, "TAVILY_API_KEY", "fake-key")
        monkeypatch.setattr(collector, "_tavily_client", lambda: fake)
        return fake
    return _montar


def test_all_complete_results_are_kept(en_linea):
    fake = en_linea([
        _resultado("https://x.test/1", TEXTO_COMPLETO),
        _resultado("https://x.test/2", TEXTO_COMPLETO),
    ])
    announcements, discarded = collector.collect()

    assert [a.url for a in announcements] == ["https://x.test/1", "https://x.test/2"], (
        "complete results must come back as announcements"
    )
    assert discarded == 0, "nothing was short, so nothing should have been discarded"
    assert "Nápoles" in announcements[0].content, "accents must survive collection"
    assert len(fake.consultas) == len(collector.QUERIES), "every query must be searched"


def test_all_degraded_raises_collector_degraded(en_linea):
    """Snippet-only results are an error condition, not a quiet day."""
    en_linea([_resultado(f"https://x.test/{i}", None) for i in range(3)])

    with pytest.raises(collector.CollectorDegraded) as excinfo:
        collector.collect()

    assert excinfo.value.degraded == 3, "one degraded result per unique URL"
    assert excinfo.value.urls == [f"https://x.test/{i}" for i in range(3)], (
        "the exception must name the degraded URLs for diagnosis"
    )


def test_mixed_batch_keeps_only_the_usable_result(en_linea):
    en_linea([
        _resultado("https://x.test/3", TEXTO_COMPLETO),
        _resultado("https://x.test/4", None),               # degraded: snippet only
        _resultado("https://x.test/5", "demasiado corto"),  # complete text, but tiny
    ])
    announcements, discarded = collector.collect()

    assert [a.url for a in announcements] == ["https://x.test/3"], (
        "a snippet and a tiny text must not become announcements"
    )
    assert discarded == 2, "both unusable results must be counted, not swallowed"


def test_text_at_exactly_the_minimum_is_kept(en_linea):
    """'Not reaching' the minimum means < MIN_CONTENT_CHARS, not <=.

    The filler names the city because the collector is CDMX-only and drops notices from
    other states; a text that named nowhere would be rejected for geography, not length,
    and this test would stop measuring the boundary it exists to measure.
    """
    justo = ("corte de agua en la CDMX. " * 60)[:collector.MIN_CONTENT_CHARS]
    assert len(justo) == collector.MIN_CONTENT_CHARS

    en_linea([_resultado("https://x.test/justo", justo)])
    announcements, discarded = collector.collect()

    assert len(announcements) == 1, "a text of exactly MIN_CONTENT_CHARS reaches the minimum"
    assert discarded == 0


def test_minimum_length_is_the_documented_default():
    assert collector.MIN_CONTENT_CHARS == 1200, (
        "the spec sets the default at 1200; changing it is a decision, not a typo fix"
    )


def test_degraded_result_is_logged_with_its_url(en_linea, caplog):
    en_linea([
        _resultado("https://x.test/3", TEXTO_COMPLETO),
        _resultado("https://x.test/recorte", None),
    ])
    with caplog.at_level(logging.WARNING, logger="app.collector"):
        collector.collect()

    assert "https://x.test/recorte" in caplog.text, (
        "a result without raw_content must be logged as degraded with its URL"
    )
    assert "degraded" in caplog.text, "the log must say it is degradation, not just noise"


def test_searches_ask_tavily_for_raw_content(en_linea):
    fake = en_linea([_resultado("https://x.test/1", TEXTO_COMPLETO)])
    collector.collect()

    assert fake.llamadas, "no search was issued"
    assert all(kw.get("include_raw_content") for kw in fake.llamadas), (
        "without include_raw_content every result would arrive degraded"
    )


def test_duplicate_urls_across_queries_are_counted_once(en_linea):
    """The fake answers every query identically, so dedup must collapse them."""
    en_linea([_resultado("https://x.test/repetido", TEXTO_COMPLETO)])
    announcements, discarded = collector.collect()

    assert [a.url for a in announcements] == ["https://x.test/repetido"]
    assert discarded == 0


def test_missing_key_serves_fixtures_without_building_a_client(monkeypatch):
    monkeypatch.setattr(config, "OFFLINE", False)
    monkeypatch.setattr(config, "TAVILY_API_KEY", "")

    def _no_client():
        raise AssertionError("collect() must not build a Tavily client without a key")

    monkeypatch.setattr(collector, "_tavily_client", _no_client)
    announcements, discarded = collector.collect()

    assert len(announcements) >= 1, "the fixture path must keep working without a key"
    assert all(a.url.startswith("synthetic://") for a in announcements), (
        "without a key the synthetic fixtures are served, not live data"
    )
    assert discarded == 0


def test_offline_flag_beats_a_present_key(monkeypatch):
    monkeypatch.setattr(config, "OFFLINE", True)
    monkeypatch.setattr(config, "TAVILY_API_KEY", "fake-key")

    def _no_client():
        raise AssertionError("OFFLINE=true must keep collect() off the network")

    monkeypatch.setattr(collector, "_tavily_client", _no_client)
    announcements, discarded = collector.collect()

    assert len(announcements) >= 1, "the offline fixture path must keep working"
    assert discarded == 0


def _refresh(monkeypatch, collect_stub) -> dict:
    """POST /refresh with collect() and the LLM stubbed; no network is possible."""
    from fastapi.testclient import TestClient

    monkeypatch.setattr(main.config, "NEBIUS_API_KEY", "fake-key")
    monkeypatch.setattr(collector, "collect", collect_stub)
    monkeypatch.setattr(llm, "complete_json", lambda *a, **kw: {"events": []})
    return TestClient(main.app).post("/refresh?geocode=false").json()


def test_refresh_surfaces_collector_discards(monkeypatch):
    notice = Announcement(url="https://x.test/a", content="corte de agua en Coyoacán")

    body = _refresh(monkeypatch, lambda *a, **kw: ([notice], 3))

    assert body.get("collector_discarded") == 3, (
        f"/refresh must expose the collector's discards next to the extractor's, got {body}"
    )
    assert body.get("discarded") == 0


def test_refresh_reports_degradation_as_an_error_not_a_quiet_day(monkeypatch):
    def _degraded(*a, **kw):
        raise collector.CollectorDegraded(
            degraded=4,
            discarded=4,
            urls=[f"https://x.test/{i}" for i in range(4)],
        )

    body = _refresh(monkeypatch, _degraded)

    assert body.get("events") == 0
    assert body.get("collector_discarded") == 4
    assert body.get("errors"), "total degradation must surface as an error, not as silence"


def test_refresh_still_accepts_the_bare_list_shape(monkeypatch):
    """Older callers and test stubs hand collect() back as a bare list."""
    notice = Announcement(url="https://x.test/a", content="corte de agua en Coyoacán")

    body = _refresh(monkeypatch, lambda *a, **kw: [notice])

    assert body.get("events") == 0, f"a bare-list stub must keep working, got {body}"
    assert body.get("collector_discarded") == 0


def test_time_range_is_sent_to_tavily(en_linea):
    fake = en_linea([_resultado("https://x.test/1", TEXTO_COMPLETO)])
    collector.collect()

    assert fake.llamadas, "no search was issued"
    assert all(kw.get("time_range") == collector.SEARCH_TIME_RANGE for kw in fake.llamadas), (
        "every search must request the configured time range"
    )


def test_search_time_range_defaults_to_week():
    assert collector.SEARCH_TIME_RANGE == "week"


def test_a_notice_older_than_the_window_is_discarded(en_linea):
    old = datetime.now() - timedelta(days=30)
    recent = datetime.now() - timedelta(days=2)
    fake = en_linea([
        _resultado("https://x.test/old", TEXTO_COMPLETO, published=old.strftime("%a, %d %b %Y %H:%M:%S GMT")),
        _resultado("https://x.test/recent", TEXTO_COMPLETO, published=recent.strftime("%a, %d %b %Y %H:%M:%S GMT")),
    ])
    announcements, discarded = collector.collect()

    assert [a.url for a in announcements] == ["https://x.test/recent"]
    assert discarded == 1, "an old notice must count as discarded"
    assert all(kw.get("time_range") == collector.SEARCH_TIME_RANGE for kw in fake.llamadas)


def test_an_undated_notice_is_kept(en_linea):
    en_linea([_resultado("https://x.test/sinfecha", TEXTO_COMPLETO, published=None)])
    announcements, discarded = collector.collect()

    assert [a.url for a in announcements] == ["https://x.test/sinfecha"]
    assert discarded == 0


def test_custom_time_range_overrides_default(en_linea):
    fake = en_linea([_resultado("https://x.test/1", TEXTO_COMPLETO)])
    collector.collect(time_range="day")

    assert all(kw.get("time_range") == "day" for kw in fake.llamadas)

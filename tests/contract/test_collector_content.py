"""The collector must not pass headlines off as notices.

What this closes: collect() once returned 18 "notices" of 61 to 140 characters, because Tavily
answered 200 OK with raw_content null and only a snippet, and the collector fell back to the
snippet and built an Announcement anyway. The extractor cannot find colonias in a headline, so
the live pipeline produced nothing while reporting success.

Never touches the network: a stub client stands in for Tavily.
"""
from __future__ import annotations

import pytest

from app import collector, config

TEXTO_COMPLETO = (
    "La Secretaría de Gestión Integral del Agua informa que habrá suspensión del servicio "
    "de agua potable en las colonias Del Valle, Narvarte y Nápoles, en la alcaldía Benito "
    "Juárez, del 25 al 26 de septiembre. " * 12
)
SNIPPET = "Corte de agua en CDMX: estas son las colonias afectadas este jueves"


def _resultado(url, raw, content=SNIPPET):
    return {"url": url, "title": "aviso", "content": content,
            "raw_content": raw, "published_date": "Thu, 24 Sep 2026 00:00:00 GMT"}


class _TavilyStub:
    """Stands in for TavilyClient, returning the same payload for every query."""

    def __init__(self, resultados):
        self.resultados = resultados
        self.consultas: list[str] = []

    def search(self, query, **kwargs):
        self.consultas.append(query)
        return {"results": list(self.resultados)}


@pytest.fixture
def en_linea(monkeypatch):
    """Force the live path with a stubbed Tavily client."""
    def _montar(resultados):
        stub = _TavilyStub(resultados)
        monkeypatch.setattr(config, "OFFLINE", False)
        monkeypatch.setattr(collector.config, "OFFLINE", False)
        monkeypatch.setattr(collector.config, "TAVILY_API_KEY", "fake-key")
        monkeypatch.setattr(collector, "_tavily_client", lambda: stub, raising=False)
        monkeypatch.setattr(collector, "TavilyClient", lambda api_key: stub, raising=False)
        return stub
    return _montar


def test_a_degraded_result_is_not_returned_as_a_notice(en_linea):
    """raw_content null plus a short snippet is not an announcement.

    Keeps one complete result alongside the degraded one on purpose. An earlier version of
    this test passed only the degraded result, which also satisfies "every result degraded"
    and so contradicted the test below -- two opposite behaviours demanded for one case.
    """
    en_linea([
        _resultado("https://x.test/1", None),
        _resultado("https://x.test/ok", TEXTO_COMPLETO),
    ])
    avisos, descartados = collector.collect()
    assert [a.url for a in avisos] == ["https://x.test/ok"], (
        "a snippet must not become an Announcement"
    )
    assert descartados == 1, "the discard has to be counted, not swallowed"


def test_a_complete_result_is_returned(en_linea):
    en_linea([_resultado("https://x.test/2", TEXTO_COMPLETO)])
    avisos, descartados = collector.collect()
    assert len(avisos) == 1
    assert descartados == 0
    assert len(avisos[0].content) >= collector.MIN_CONTENT_CHARS


def test_a_mixed_batch_keeps_only_what_is_usable(en_linea):
    en_linea([
        _resultado("https://x.test/3", TEXTO_COMPLETO),
        _resultado("https://x.test/4", None),
        _resultado("https://x.test/5", "demasiado corto"),
    ])
    avisos, descartados = collector.collect()
    assert [a.url for a in avisos] == ["https://x.test/3"]
    assert descartados == 2


def test_every_result_degraded_is_an_error_condition_not_an_empty_day(en_linea):
    """Zero usable notices because Tavily degraded must be distinguishable from quiet."""
    en_linea([_resultado(f"https://x.test/{i}", None) for i in range(5)])
    with pytest.raises(collector.CollectorDegraded):
        collector.collect()


def test_offline_mode_still_serves_the_fixtures(monkeypatch):
    monkeypatch.setattr(collector.config, "OFFLINE", True)
    avisos, descartados = collector.collect()
    assert len(avisos) >= 1, "the offline fixture path must keep working"
    assert descartados == 0


def test_minimum_length_is_a_module_constant():
    assert isinstance(collector.MIN_CONTENT_CHARS, int)
    assert collector.MIN_CONTENT_CHARS >= 500, (
        "a threshold below 500 would let headlines through again"
    )
